
import importlib.metadata

def enrich_diagnose(result: dict) -> dict:
    """Append server/plugin version-match check to a diagnose result."""
    try:
        server_version = importlib.metadata.version("qgis-mcp")
    except importlib.metadata.PackageNotFoundError:
        server_version = "unknown (editable install?)"

    plugin_version = None
    for check in result.get("checks", []):
        if check["name"] == "plugin_version":
            plugin_version = check.get("detail")
            break

    version_match = "ok" if plugin_version == server_version else "mismatch"
    result["checks"].append(
        {
            "name": "version_match",
            "status": version_match,
            "detail": {"server": server_version, "plugin": plugin_version},
        }
    )
    if version_match == "mismatch" and result["status"] == "healthy":
        result["status"] = "degraded"

    return result