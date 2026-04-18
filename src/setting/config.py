import os
import struct
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
# Definimos la ruta
DIR_LOG = BASE_DIR / "log"
PATH_ASSETS = BASE_DIR / "plugin_mcp_qgis" / "assets"
PATH_SKILLS = os.getenv("PATH_SKILLS", "Skills")

# Constantes técnicas
RECV_CHUNK_SIZE = 65536
TIMEOUT_DEFAULT = 30

RECV_CHUNK_SIZE = 65536
MAX_MESSAGE_SIZE = 10 * 1024 * 1024
HEADER_STRUCT = struct.Struct(">I")

# 2. Configuraciones que PUEDEN cambiar (Variables de entorno)
PORT = int(os.environ.get("QGIS_MCP_PORT", 9876))
HOST = os.environ.get("QGIS_MCP_HOST", "localhost")
