#!/usr/bin/env python3
"""
QGIS MCP Server - Exposes QGIS operations as MCP tools, resources, and prompts.
"""

import asyncio
import contextlib
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations


from src.mcp_qgis.client import QgisMCPClient
from src.setting.config import HOST, PORT, TIMEOUT_DEFAULT
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

    host = os.environ.get("QGIS_MCP_HOST", HOST)
    port_str = os.environ.get("QGIS_MCP_PORT", str(PORT))
    try:
        port = int(port_str)
        if not 1 <= port <= 65535:
            raise ValueError("out of range")
    except ValueError as exc:
        raise ValueError(
            f"QGIS_MCP_PORT must be an integer 1-65535, got: {port_str!r}"
        ) from exc

    _qgis_connection = QgisMCPClient(host=host, port=port)
    if not await _qgis_connection.connect():
        _qgis_connection = None
        raise ConnectionError(
            "Could not connect to QGIS. Make sure the QGIS plugin is running."
        )
    _connection_validated_at = time.monotonic()
    logger.info(f"Created new persistent connection to QGIS at {host}:{port}")
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


async def _send(
    command_type: str, params: dict | None = None, timeout: int = TIMEOUT_DEFAULT
) -> dict:
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
            result = await qgis.send_command(command_type, params, timeout=timeout)
            _first_successful_connection = True
            break
        except _CONNECTION_ERRORS as exc:
            last_exc = exc
            await _invalidate_connection()
            if attempt < max_retries - 1:
                delay = delays[min(attempt, len(delays) - 1)]
                logger.warning(
                    "Connection error (%s), retrying in %.1fs (attempt %d/%d)",
                    exc,
                    delay,
                    attempt + 1,
                    max_retries,
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    "Connection failed after %d attempts: %s", max_retries, exc
                )
                raise
    else:
        raise last_exc  # type: ignore

    if not result or result.get("status") == "error":
        raise RuntimeError(
            result.get("message", "Command failed") if result else "No response"
        )
    return result.get("result", {})


async def _confirm_destructive(ctx: Context, message: str) -> bool:
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
    host = os.environ.get("QGIS_MCP_HOST", HOST)
    port = os.environ.get("QGIS_MCP_PORT", str(PORT))
    logger.info(
        f"QgisMCPServer starting up (will connect to QGIS at {host}:{port} on first call)"
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
    instructions="QGIS integration through the Model Context Protocol. Use tools for actions, resources for read-only data, prompts for workflows.",
    lifespan=server_lifespan,
)


@mcp.tool(
    title="Ping",
    annotations=ToolAnnotations(readOnlyHint=True),
    description="Check connectivity to the QGIS plugin server. Returns pong if connected.",
    structured_output=True,
)
async def ping(ctx: Context) -> dict[str, Any]:
    return await _send("ping")


@mcp.tool(
    description="Busca algoritmos de procesamiento nativos y de terceros en QGIS por su nombre."
)
async def search_geoprocessing_tools(query: str):
    """
    Busca herramientas y algoritmos disponibles en el catálogo de QGIS (incluye 
    herramientas nativas, GRASS, SAGA y plugins). 
    Úsala para descubrir qué procesos puedes ejecutar para resolver una tarea espacial.
    """
    return await _send("buscador_dinamico_mcp", {"busqueda": query})


@mcp.tool(
    description="Obtener parametros especificos de un algoritmo"
)
async def get_algorithm_details(alg_id: str) -> dict[str, Any]:
    """
    Recupera los parametros de un algoritmo en especifico
    """
    return await _send("get_algorithm_details", {"alg_id": alg_id})


if __name__ == "__main__":
    mcp.run()
