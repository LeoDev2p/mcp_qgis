#!/usr/bin/env python3
"""
QGIS MCP Server
===============
Exposes QGIS operations as MCP tools via the FastMCP framework.

Architecture
------------
This module sits between the LLM (Claude) and the QGIS plugin:

    LLM  →  FastMCP (this file)  →  QgisMCPClient (TCP)  →  QGIS plugin

All communication with QGIS is done through a single persistent asyncio TCP
connection (``QgisMCPClient``).  Commands are serialised as length-prefixed
JSON frames, mirroring the framing used by ``QgisMCPServer`` inside QGIS.

Design philosophy
-----------------
Instead of exposing one MCP tool per QGIS feature, this server exposes a small
set of *composable* tools so the LLM can autonomously:

* Discover available geoprocessing algorithms (``search_geoprocessing_tools``).
* Inspect their parameters before execution (``get_algorithm_details``).
* Run any algorithm via the unified ``run_processing`` entry-point.
* Load layers, read project state, and execute arbitrary Python inside QGIS.

Connection Management
---------------------
A module-level ``QgisMCPClient`` instance is reused across tool calls to avoid
per-call TCP handshake overhead.  The connection is validated every
``_CONNECTION_TTL`` seconds (socket-level check) and automatically re-created
on failure.  First-connection attempts use longer retry windows to tolerate
QGIS startup delays.
"""

import asyncio
import contextlib
import json
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from src.mcp_qgis.client import QgisMCPClient
from src.setting.config import HOST, PATH_SKILLS, PORT
from src.setting.logger import get_logger

logger = get_logger("QgisMCPServer")

# Persistent connection management

_qgis_connection: QgisMCPClient | None = None
_connection_validated_at: float = 0.0
_CONNECTION_TTL: float = 5.0  # seconds between validation checks


async def get_qgis_connection() -> QgisMCPClient:
    """Get or create a persistent async QGIS connection."""
    global _qgis_connection, _connection_validated_at

    if _qgis_connection is not None:
        now = time.monotonic()
        if now - _connection_validated_at < _CONNECTION_TTL:
            return _qgis_connection

        # Check if writer is closing or socket is bad
        try:
            if _qgis_connection.writer and _qgis_connection.writer.is_closing():
                raise ConnectionError("Writer is closed")
            # Get peer name of underlying socket to validate
            sock = _qgis_connection.writer.get_extra_info("socket")
            if sock:
                sock.getpeername()

            _connection_validated_at = now
            return _qgis_connection
        except Exception:
            logger.warning("Existing connection is no longer valid, reconnecting")
            with contextlib.suppress(Exception):
                await _qgis_connection.disconnect()
            _qgis_connection = None
            _connection_validated_at = 0.0

    # Conexion con qgis priemra vez
    try:
        if not 1 <= PORT <= 65535:
            raise ValueError("out of range")
    except ValueError as exc:
        raise ValueError(
            f"QGIS_MCP_PORT must be an integer 1-65535, got: {PORT}"
        ) from exc

    _qgis_connection = QgisMCPClient(host=HOST, port=PORT)
    if not await _qgis_connection.connect():
        _qgis_connection = None
        raise ConnectionError(
            "Could not connect to QGIS. Make sure the QGIS plugin is running."
        )
    _connection_validated_at = time.monotonic()
    logger.info(f"Created new persistent connection to QGIS at {HOST}:{PORT}")
    return _qgis_connection


async def _invalidate_connection() -> None:
    """Force-close the cached connection so the next call reconnects."""
    global _qgis_connection, _connection_validated_at
    if _qgis_connection is not None:
        with contextlib.suppress(Exception):
            await _qgis_connection.disconnect()
        _qgis_connection = None
        _connection_validated_at = 0.0


_CONNECTION_ERRORS = (
    OSError,
    ConnectionError,
    asyncio.TimeoutError,
    asyncio.IncompleteReadError,
)
_MAX_RETRIES = 3
_RETRY_DELAYS = (0.5, 1.0)
_FIRST_CONNECT_RETRIES = 5
_FIRST_CONNECT_DELAYS = (1.0, 2.0, 3.0, 5.0)
_first_successful_connection = False


async def _send(command_type: str, params: dict | None = None) -> dict:
    """Send a command asynchronously and return the unwrapped result.
    Retries on connection/socket errors with increasing delays.
    """
    global _first_successful_connection
    last_exc: Exception | None = None

    if _first_successful_connection:
        max_retries = _MAX_RETRIES
        delays = _RETRY_DELAYS
    else:
        max_retries = _FIRST_CONNECT_RETRIES
        delays = _FIRST_CONNECT_DELAYS

    for attempt in range(max_retries):
        try:
            qgis = await get_qgis_connection()
            result = await qgis.send_command(command_type, params)
            _first_successful_connection = True
            break
        except _CONNECTION_ERRORS as exc:
            last_exc = exc
            await _invalidate_connection()
            if attempt < max_retries - 1:
                delay = delays[min(attempt, len(delays) - 1)]
                logger.warning(
                    f"Connection error {exc}, retrying in {delay}s (attempt {attempt + 1}/{max_retries})"
                )

                await asyncio.sleep(delay)
            else:
                logger.error(f"Connection failed after {max_retries} attempts: {exc}")
                raise
    else:
        raise last_exc  # type: ignore

    if not result or result.get("status") == "error":
        raise RuntimeError(
            result.get("message", "Command failed") if result else "No response"
        )
    return result.get("result", {})


async def _confirm_destructive(ctx: Context, message: str) -> bool:
    """Ask the user to confirm a destructive operation via MCP elicitation.

    If the connected client does not support elicitation (e.g. older clients),
    the call is allowed to proceed automatically so that basic workflows are
    not broken.

    Args:
        ctx:     FastMCP request context, used to send an interactive prompt.
        message: Human-readable description of the operation to confirm.

    Returns:
        ``True`` if the user confirmed (or elicitation is unsupported),
        ``False`` if the user explicitly rejected the operation.
    """
    try:
        response = await ctx.elicit(
            message=message,
            schema={
                "type": "object",
                "properties": {
                    "confirm": {
                        "type": "boolean",
                        "description": "Confirm this operation",
                    },
                },
                "required": ["confirm"],
            },
        )
        return response.action == "accept" and bool(response.data.get("confirm"))
    except Exception:
        logger.info("Elicitation not supported by client, proceeding with operation")
        return True


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """FastMCP lifespan context manager — handles startup and graceful shutdown.

    On startup the server only logs the target QGIS address; the actual TCP
    connection is deferred to the first tool call so QGIS does not need to be
    running before the MCP server starts.

    On shutdown the persistent ``QgisMCPClient`` connection is closed cleanly.
    """
    logger.info(
        f"QgisMCPServer starting up (will connect to QGIS at {HOST}:{PORT} on first call)"
    )
    try:
        yield {}
    finally:
        if _qgis_connection:
            logger.info("Disconnecting from QGIS on shutdown")
            await _invalidate_connection()
        logger.info("QgisMCPServer shut down")


mcp = FastMCP(
    name="Qgis_mcp",
    instructions="""QGIS integration through the Model Context Protocol.
        Use tools for actions, resources for read-only data, prompts for workflows.
        Discover available geospatial skills with list_skills() before complex tasks.""",
    lifespan=server_lifespan,
)

# * ------------------------- MCP tools for QGIS control ------------------------------


@mcp.tool(
    title="Ping",
    annotations=ToolAnnotations(readOnlyHint=True),
    structured_output=True,
)
async def ping(ctx: Context) -> dict[str, Any]:
    """
    Check QGIS plugin server connectivity.

    * Use before complex commands to ensure QGIS is available.
    * Returns 'pong' if connection is active.
    """
    return await _send("ping")


@mcp.tool()
async def search_geoprocessing_tools(query: str):
    """
    Search QGIS processing algorithms by name.

    * Includes native tools, GRASS, SAGA, and plugins.
    * Use this to discover the exact `algorithm` ID needed to solve a spatial task.
    """
    return await _send("search_geoprocessing_tools", {"search": query})


@mcp.tool()
async def get_algorithm_details(alg_id: str) -> dict[str, Any]:
    """
    Get required parameters for a specific processing algorithm.

    * Requires `alg_id` (discover this using `search_geoprocessing_tools`).
    """
    return await _send("get_algorithm_details", {"alg_id": alg_id})


@mcp.tool()
async def run_processing(algorithm: str, parameter: dict = {}) -> dict:
    """
    Execute a QGIS processing algorithm.

    * Requires valid `algorithm` ID (find via `search_geoprocessing_tools`).
    * Requires `parameter` dict (discover exact keys via `get_algorithm_details`).
    * Returns a dictionary with generated layer IDs or output paths.
    """
    return await _send(
        "run_processing", {"algorithm": algorithm, "parameter": parameter}
    )


@mcp.tool()
async def get_project_context() -> list[dict]:
    """
    Return a snapshot of the active QGIS project state.

    * Call this FIRST to understand loaded data (layers, fields, CRS).
    * Returns a list with ID, name, type, and attributes for all loaded layers.
    """
    return await _send("get_project_context", {})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_layer_features(
    layer_id: str,
    limit: int = 10,
    offset: int = 0,
    filter_expression: str = "",
    include_geometry: bool = False,
) -> dict:
    """
    Read rows from a vector layer's attribute table.

    * Requires `layer_id` (obtain from `get_project_context`).
    * Use `limit` (max 100 recommended) and `offset` for pagination.
    * Use QGIS syntax in `filter_expression` to filter features (e.g. "area > 10").
    * Set `include_geometry` to True ONLY if WKT geometry is explicitly needed.
    """
    return await _send(
        "get_layer_features",
        {
            "layer_id": layer_id,
            "limit": limit,
            "offset": offset,
            "filter_expression": filter_expression,
            "include_geometry": include_geometry,
        },
    )


@mcp.tool()
async def load_layer_from_path(path: str, name: str) -> dict:
    """
    Load a local spatial file (vector or raster) into the QGIS canvas.

    * Requires an absolute file `path`.
    * Formats are auto-detected (.shp, .gpkg, .geojson, .tif, etc).
    * Makes data immediately visible to the user.
    """
    return await _send("load_layer_from_path", {"path": path, "name": name})


@mcp.tool()
async def save_project(path: str = "") -> dict:
    """
    Save the active QGIS project to disk.

    * Provide an absolute `path` (.qgz or .qgs) to save as a new file.
    * Omit `path` to silently overwrite the existing project file.
    """
    return await _send("save_project", {"path": path})


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
async def remove_layer(ctx: Context, layer_id: str) -> dict:
    """
    Remove a layer from the active project without deleting its source file.

    * Requires `layer_id` (obtain from `get_project_context`).
    * Destructive action to the project state: triggers a user confirmation prompt.
    """
    confirmed = await _confirm_destructive(
        ctx,
        f"Remove layer '{layer_id}' from the project? "
        "The file on disk will NOT be deleted, but unsaved changes to the layer will be lost.",
    )
    if not confirmed:
        return {"status": "cancelled", "message": "Operation cancelled by user."}
    return await _send("remove_layer", {"layer_id": layer_id})


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
async def delete_file(ctx: Context, path: str) -> dict:
    """
    Permanently delete a file from the filesystem.

    * THIS CANNOT BE UNDONE.
    * Use ONLY when explicitly instructed by the user to delete a file.
    """
    confirmed = await _confirm_destructive(
        ctx,
        f"⚠️  PERMANENTLY DELETE '{path}' from disk?\n"
        "This action CANNOT be undone. The file will be gone forever.",
    )
    if not confirmed:
        return {"status": "cancelled", "message": "Deletion cancelled by user."}
    return await _send("delete_file", {"path": path})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def show_message(
    text: str,
    level: str = "info",
    duration: int = 5,
) -> dict:
    """
    Display a temporary visual notification in the QGIS UI.

    * Use this to inform the user about workflow progress (e.g. 'Processing layers...').
    * Valid `level` options: 'info', 'warning', 'error', 'success'.
    """
    return await _send(
        "show_message", {"text": text, "level": level, "duration": duration}
    )


@mcp.tool()
async def execute_code(code: str) -> dict:
    """
    IMPORTANT: Do not use this tool without first validating the parameters with 'get_algorithm_tool', search_geoprocessing_tools.

    Execute arbitrary Python code inside the live QGIS environment.

    * Available globals: QgsProject, iface, canvas, processing, and renderer classes.
    * Use `print()` to return plain text output.
    * Assign data to `_result` (e.g. `_result = <data>`) to return structured JSON to the LLM.
    * Use for styling, custom canvas control, or undocumented edge cases.
    """
    return await _send("execute_code", {"code": code})


# ---------------------------------------------------------------------------
# MCP Resources — live, read-only views of the current QGIS state
# The LLM can read these like files without consuming tool-call budget.
# ---------------------------------------------------------------------------


@mcp.resource(
    "qgis://project",
    name="QGIS Project State",
    mime_type="application/json",
)
async def resource_project() -> str:
    """
    Live static snapshot of the active QGIS project state.

    * Returns layers, paths, IDs, and metadata in read-only mode.
    * Analogous to calling `get_project_context`.
    """
    try:
        data = await _send("get_project_context", {})
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.resource(
    "qgis://selection/{layer_id}",
    name="Selected Features",
    mime_type="application/json",
)
async def resource_selection(layer_id: str) -> str:
    """
    Read active user feature selection on the QGIS canvas.

    * Inject a valid layer ID into `{layer_id}` (obtain from `get_project_context`).
    """
    try:
        data = await _send("get_selection", {"layer_id": layer_id})
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# * ------- MCP Tools for Skills — Autonomous skill discovery by the LLM ---------------


def _get_all_skills():
    """Helper to recursively find all .md files in PATH_SKILLS."""
    all_skills = []
    if not os.path.exists(PATH_SKILLS):
        return all_skills

    for root, _, files in os.walk(PATH_SKILLS):
        for file in files:
            if file.endswith(".md"):
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, PATH_SKILLS)
                all_skills.append({
                    "name": file[:-3],
                    "rel_path": rel_path.replace("\\", "/"),
                    "full_path": full_path
                })
    return all_skills


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def list_skills() -> dict:
    """
    List all predefined geospatial skills and recipes available recursively.

    * Check this for complex or vague tasks to see if a workflow is already implemented.
    """
    skills = _get_all_skills()
    if not skills:
        return {"skills": [], "message": f"No skills directory found at {PATH_SKILLS}."}

    return {
        "skills_root": str(PATH_SKILLS),
        "skills": [
            {"name": s["name"], "path": s["rel_path"]} for s in skills
        ]
    }


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def read_skill(skill_path: str) -> dict:
    """
    Read the exact markdown instruction steps for a specific skill.

    * Requires a `skill_path` (relative path) obtained from `list_skills`.
    * Invoke this BEFORE attempting complex geoprocessing if a matching skill exists.
    """
    filepath = os.path.join(PATH_SKILLS, skill_path)
    if not os.path.exists(filepath):
        return {"error": f"Skill at '{skill_path}' no encontrado."}

    with open(filepath, encoding="utf-8") as f:
        content = f.read()

    return {"skill_path": skill_path, "instructions": content}


# * --------- MCP Prompts — Dynamic loading of workflows from the skills directory -------


def _register_skills_as_prompts():
    """Load all .md files in the skills directory and register them as MCP Prompts.

    This allows Claude to discover complex workflows natively without hardcoding
    them inside the server script.
    """
    skills = _get_all_skills()
    
    for skill in skills:
        skill_name = skill["name"]
        filepath = skill["full_path"]
        
        # Unique name for prompt if nested to avoid collisions
        # e.g. "spectral_analysis_fire_severity"
        prompt_name = skill["rel_path"].replace("/", "_").replace(".md", "").lower()

        # Closure to bind the specific name and path per loop iteration
        def _bind_prompt(name=prompt_name, path=filepath, display_name=skill_name):
            @mcp.prompt(
                name,
                description=f"Ejecuta el Skill geoespacial: {display_name.replace('_', ' ')}",
            )
            def dynamic_prompt() -> str:
                with open(path, encoding="utf-8") as f:
                    return f.read()

        _bind_prompt()



_register_skills_as_prompts()


if __name__ == "__main__":
    mcp.run()
