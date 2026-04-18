# QGIS MCP Server

<div align="center">
  <img src="assets/banner.png" alt="QGIS MCP Banner" width="100%" style="border-radius: 8px; margin: 20px 0;">
  
  <p><strong>Expose QGIS API through the Model Context Protocol for LLM control (Claude, Antigravity, Gemini)</strong></p>
  
  [![Version](https://img.shields.io/badge/Version-0.1.0-blue.svg)](https://github.com/LeoDev2p/mcp_qgis)
  [![QGIS](https://img.shields.io/badge/QGIS-3.22%2B-brightgreen.svg)](https://qgis.org/)
  [![Python](https://img.shields.io/badge/Python-3.10%2B-yellow.svg)](https://www.python.org/)
  [![MCP](https://img.shields.io/badge/MCP-FastMCP-purple.svg)](https://modelcontextprotocol.io/)
  [![License](https://img.shields.io/badge/License-MIT-gray.svg)](LICENSE)
</div>




Connect [QGIS](https://qgis.org/) to [Claude AI](https://claude.ai/), [Antigravity](https://antigravity.ai/), [Gemini](https://gemini.google.com/) through the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/), enabling Claude to directly control QGIS — manage layers, edit features, run processing algorithms, render maps, and more.

## Architecture

- **No GUI:** Use natural language commands
- **No REST API:** Local TCP socket communication
- **Secure:** Only accessible on localhost
- **Powerful:** Complete access to geoprocessing, analysis, and visualization

---

## MCP Architecture

```
Claude  ←→  MCP Server (FastMCP)  ←→  TCP socket  ←→  QGIS Plugin (QTimer)  ←→  PyQGIS API
(LLM)       (localhost:9876)           (async)        (Event listener)        (Desktop)
```

**In detail:**
- **Claude:** Requests tools ("Load cities.shp")
- **MCP Server:** Receives request, translates to JSON-RPC, sends via TCP
- **TCP socket:** Async bidirectional communication on port 9876
- **QGIS Plugin:** Listens with QTimer, executes commands, returns results
- **PyQGIS API:** Native access to QgsProject, processing, rendering

---

## Technologies Used

- **QGIS** 3.22 LTR+ - Desktop GIS application
- **FastMCP** - Model Context Protocol implementation in Python
- **Python** 3.10+ - Core language
- **uv** - Fast Python package manager
- **PyQt/Qt** - QGIS plugin UI
- **AsyncIO** - TCP async JSON-RPC communication

---

## Installing uv

**uv** is the package manager for this project. Install it for your OS:

### Windows
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### macOS / Linux
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Verify installation
```bash
uv --version
```

 [Official uv documentation](https://docs.astral.sh/uv/getting-started/installation/)

---

## Prerequisites

| Component | Version | Notes |
|-----------|---------|-------|
| **QGIS** | 3.22 LTR+ | Desktop application (not server) |
| **Python** | 3.12 | Included in QGIS |
| **uv** | Latest | Package manager |
| **Git** | Latest | Version control |
| **OS** | Windows / macOS / Linux | Any |

**Installing QGIS:**
- Windows: [Download from qgis.org](https://qgis.org/download/) or `winget install QGIS`
- macOS: [Download from qgis.org](https://qgis.org/download/) or `brew install qgis`
- Linux: `apt install qgis` (Ubuntu/Debian) or `dnf install qgis` (Fedora)

---

## Repository Installation and Configuration

### 1. Clone the repository
```bash
git clone https://github.com/LeoDev2p/mcp_qgis.git
cd mcp_qgis
```

### 2. Sync dependencies with uv
```bash
uv sync
```

### 3. Install the plugin in QGIS
```bash
# Linux/macOS
cp -r plugin_mcp_qgis ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/

# Windows (PowerShell)
Copy-Item plugin_mcp_qgis -Destination "$env:APPDATA\QGIS\QGIS3\profiles\default\python\plugins" -Recurse
```

### 4. Activate and Configure the Plugin in QGIS

1. **Open QGIS Desktop**
2. **Menu:** Plugins → Manage and Install Plugins
3. **Search:** "QGIS MCP Server" 
4. **Check:** Enable
5. **Close the window**
6. In the toolbar, click the 🔌 **"QGIS MCP Server"** icon to activate the TCP server

**Verification:**
- You'll see: "QGIS MCP Server started at localhost:9876"
- Log file: `mcp_qgis/log/mcp_qgis.log`
- View in real-time: `tail -f log/mcp_qgis.log`

**Environment variables (optional):**
```bash
set QGIS_MCP_PORT=9876              # Port (default 9876)
set QGIS_MCP_HOST=localhost         # Host (always localhost in v0.1.0)
set PYTHONPATH=C:\path\to\mcp_qgis  # Path to QGIS libs
```

**Ready.** The server is listening on `localhost:9876`

---

## LLM Configuration

### Claude Desktop

**Configuration file:**
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

**Add this configuration:**
```json
{
  "mcpServers": {
    "qgis": {
      "command": "uv",
      "args": [
        "--directory",
        "C:\\path\\to\\mcp_qgis",
        "run",
        "python",
        "-m",
        "src.mcp_qgis.server"
      ],
      "env": {
        "PYTHONPATH": "C:\\path\\to\\mcp_qgis",
        // optional
        "PATH_SKILLS": "c:\\path\\to\\skills",
        "QGIS_MCP_HOST": "localhost",
        "QGIS_MCP_PORT": "9876"
      }
    }
  }
}
```

### Antigravity

**Add to your MCP configuration:**
```json
{
  "qgis": {
    "command": "uv",
    "args": [
      "--directory",
      "/path/to/mcp_qgis",
      "run",
      "python",
      "-m",
      "src.mcp_qgis.server"
    ],
    "env": {
      "PYTHONPATH": "/path/to/mcp_qgis",
      // optional
      "PATH_SKILLS": "c:\\path\\to\\skills",
      "QGIS_MCP_HOST": "localhost",
      "QGIS_MCP_PORT": "9876"
    }
  }
}
```

### Gemini / Google

**Similar configuration:** Use the same pattern as Claude Desktop. Check Google documentation for the exact configuration file location.

**⚠️ Important:** Replace `/path/to/mcp_qgis` with the **complete absolute path** to your directory (Windows: use paths with `C:\`, Linux/macOS: use `~` or full paths).

---

## Available Tools

### Layer Management
| Tool | Description | Parameters |
|------|-------------|-----------|
| **load_layer_from_path** | Load geospatial file | `path` (str), `name` (str, opt) |
| **remove_layer** | Remove layer from project | `layer_id` (str) |
| **get_project_context** | Get layers, CRS, metadata | - |

### Geoprocessing
| Tool | Description | Parameters |
|------|-------------|-----------|
| **search_geoprocessing_tools** | Search QGIS/GRASS/SAGA algorithms | `query` (str) |
| **get_algorithm_details** | Inspect algorithm parameters | `algorithm_id` (str) |
| **run_processing** | Execute algorithm | `algorithm_id`, `params` (dict) |

### Analysis and Reading
| Tool | Description | Parameters |
|------|-------------|-----------|
| **get_layer_features** | Read features, attributes, geometries | `layer_id`, `limit`, `offset` |
| **execute_code** | Execute Python in QGIS context | `code` (str, ⚠️ dangerous) |
| **ping** | Check connectivity | - |

### Utilities
| Tool | Description | Parameters |
|------|-------------|-----------|
| **show_message** | Show dialog in QGIS | `text` (str), `level` (info/warning/error) |
| **delete_file** | Delete file from disk | `path` (str, ⚠️ permanent) |

**Usage example in Claude:**
```
"Load the file /data/cities.shp as 'Cities' and tell me how many features it has"
```

Claude will use: `load_layer_from_path()` → `get_layer_features()`

---

##  Skills (Extended Capabilities)

### What are Skills?

**Skills** are predefined automations (recipes/workflows) for common geospatial tasks. They are written in Markdown and the LLM can discover, read, and implement them automatically.

**Skill examples:**
- Calculate NDVI from Landsat images
- Create buffers around points
- Spatial interpolation (kriging)
- Deforestation analysis
- Batch processing of multiple layers

### Download Skills

```bash
# Clone skills repository
git clone https://github.com/LeoDev2p/skills_gis.git

# Copy skills to the correct directory
cp -r skills_gis/* mcp_qgis/src/mcp_qgis/skills/

# Or simply download individual .md files
# and place them in src/mcp_qgis/skills/
```

### Tools to Interact with Skills

| Tool | Description | Parameters |
|------|-------------|-----------|
| **list_skills** | Discover all available skills | - |
| **read_skill** | Read markdown instructions of a skill | `skill_name` (str) |

**Usage example:**
```
Claude: "What skills do I have available?"
→ list_skills() returns: ["ndvi_calculator", "buffer_analysis", "kriging_interpolation"]

Claude: "Explain how to use ndvi_calculator"
→ read_skill("ndvi_calculator") returns the markdown content with steps
```

---

## Security

### Critical Warnings

- **Local use only:** Do not expose on shared networks
- **`execute_code` is powerful:** Executes Python without restrictions
- **`delete_file` is destructive:** Permanently deletes data (no trash)

### Current Mitigations (v0.1.0)
- TCP connection only on `localhost` (not remote)
- Requires manual plugin activation
- Runs as current user (no elevation)
- All commands are logged

### Planned Improvements (v0.3.0+)
- [ ] RestrictedPython sandbox for `execute_code`
- [ ] Token authentication
- [ ] TLS encryption

**See [SECURITY.md](SECURITY.md) for complete details and Responsible Disclosure.**

---

## Contributing and Resources

### For Contributors
- [CONTRIBUTING.md](CONTRIBUTING.md) - Development guide
  - Setup with uv
  - Code standards
  - Pull Request workflow

### For Security
- [SECURITY.md](SECURITY.md) - Security policy
  - Documented risks
  - Responsible disclosure
  - Patch SLA

### Other Resources
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [QGIS Documentation](https://docs.qgis.org/)
- [FastMCP GitHub](https://github.com/modelcontextprotocol/python-sdk)
- [uv Docs](https://docs.astral.sh/uv/)

---

## License

MIT License - See [LICENSE](LICENSE)
