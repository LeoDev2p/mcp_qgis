# Contributing

## Development Setup

```bash
# Clone the repository
git clone https://github.com/LeoDev2p/mcp_qgis.git
cd mcp_qgis

# Sync dependencies (including dev)
uv sync

# Activate the environment
source .venv/bin/activate  # Linux/macOS
# or
.venv\Scripts\Activate.ps1  # Windows
```

**Verify installation:**
```bash
uv run pytest --version
uv run black --version
```

---

## Project Structure

```
mcp_qgis/
├── src/
│   └── mcp_qgis/
│       ├── server.py           ← FastMCP server (entry point)
│       ├── client.py           ← TCP async client
│       └── skills/             ← Markdown automations
├── plugin_mcp_qgis/
│       ├── mcp_plugin.py       ← QGIS plugin (Qt/PyQt) - CENTRAL TOOL
│       └── metadata.txt
├── tests/
│       ├── test_server.py
│       ├── test_client.py
│       └── conftest.py
├── pyproject.toml              ← Dependencies (manage with `uv add`)
└── log/                        ← Runtime logs
```

**Key points:**
- `mcp_plugin.py` is the **heart**: executes commands, accesses PyQGIS, returns results
- `server.py` defines the **MCP tools** (what is exposed to the LLM)
- `client.py` communicates with the plugin via TCP socket

---

## 🛠 Tool Rules

### 1. Function Naming

```python
# GOOD - Verb + Noun, action-oriented
async def load_layer_from_path(path: str, name: str = None) -> dict:
    ...

async def run_geoprocessing_algorithm(algorithm_id: str, params: dict) -> dict:
    ...

async def get_layer_features(layer_id: str, limit: int = 100) -> list[dict]:
    ...

# BAD - Vague, without clear action
async def process_data(x):
    ...

async def qgis_stuff(layer):
    ...
```

### 2. Docstrings (LLM-Facing)

The LLM reads your docstring to decide whether to use the tool. **Be clear, detailed, and natural** (no restrictive format like Google style).

```python
@mcp.tool()
async def load_layer_from_path(path: str, name: str = None) -> dict:
    """Load a geospatial layer file (.shp, .tif, .geojson, .gpkg) into QGIS.
    
    Use this tool to add vector or raster data to the active QGIS project. The path 
    must be absolute (e.g., /home/user/data/cities.shp, not ./cities.shp). The optional 
    name parameter sets the display name in QGIS canvas; if omitted, uses the filename.
    
    Returns a dict with layer_id, layer_name, status (success/error), geometry_type 
    (Point/Line/Polygon/Raster), and feature_count if available.
    
    Example: {"path": "/data/cities.shp", "name": "Cities"}
    """
```

**What makes a good docstring for LLM:**
- First line: What it does (imperative)
- Paragraphs: Technical details, limitations, parameters in context
- Return: What structure to expect
- Example: Real command that the LLM can use

**Avoid:** Restrictive format (Args/Returns tables), unexplained jargon, very short docs

### 3. Argument Typing (Python types)

```python
# GOOD - Python 3.10+ type hints
async def run_processing(
    algorithm_id: str,
    params: dict[str, Any],
    output_path: str | None = None
) -> dict[str, str | int]:
    """Run QGIS processing algorithm."""
    ...

# BAD - No types
async def run_processing(algorithm_id, params, output_path=None):
    ...
```

**Simple rule:** Always add types. If optional, use `Type | None`. If dict with structure, docstring explains it.

---

## Git Protocol

### Branch Names

```bash
# New feature
git checkout -b feat/add-wms-layer-support

# Bug fix
git checkout -b fix/null-pointer-in-plugin

# Documentation
git checkout -b docs/update-readme-examples

# Refactor (no functional changes)
git checkout -b refactor/simplify-tcp-handler
```

### Commit Messages

```
# GOOD - Clear, what + why

feat: add tool search_geoprocessing_tools

- Implement fuzzy search for QGIS algorithms (qgis:, grass:, saga:)
- Add unit tests for algorithm matching
- Update docstring with LLM-friendly examples

Fixes #42

# BAD

fix: bugs
updated stuff
small changes
```

**Format:**
```
<type>: <short description (50 chars max)>

<body - what changed and why (wrap at 72 chars)>

Fixes #123
```

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`

---

## PR Checklist

Before pushing, verify:

### Code
- [ ] `uv run pytest` - All tests pass
- [ ] `uv run black --check src/` - Code formatted
- [ ] `uv run pylint src/ --disable=C0111` - No critical errors
- [ ] Docstring on every new function (LLM-friendly)
- [ ] Type hints on arguments and return

### Testing
- [ ] Tests for new logic (unit tests in `tests/`)
- [ ] Manual testing in QGIS Desktop if it's a tool
- [ ] No regression: old feature still works

### Documentation
- [ ] README updated if usage/installation changes
- [ ] Docstring updated in `server.py`
- [ ] If breaking change, mark in PR description

### Commit
- [ ] Atomic commits (one change per commit)
- [ ] Messages following convention (feat: / fix: / etc)
- [ ] No accidental merges (rebase only)

---

## Contribution Flow (Step by Step)

1. **Fork and clone:**
   ```bash
   git clone https://github.com/YOUR_USER/mcp_qgis.git
   cd mcp_qgis
   uv sync
   ```

2. **Create branch:**
   ```bash
   git checkout -b feat/my-feature
   ```

3. **Edit and test:**
   ```bash
   # Edit files
   uv run pytest                    # Unit tests
   uv run black src/                # Format
   uv run pylint src/               # Lint
   ```

4. **Commit:**
   ```bash
   git commit -m "feat: add new tool

   - Implement xyz
   - Add tests

   Fixes #42"
   ```

5. **Push and PR:**
   ```bash
   git push origin feat/my-feature
   ```
   Open PR on GitHub with clear description.

6. **Code Review:**
   - Respond to reviewer comments
   - Push requested changes
   - Wait for approval (minimum 1 reviewer)

---

## Contact

- Issues: [GitHub Issues](https://github.com/LeoDev2p/mcp_qgis/issues)
- Discussions: [GitHub Discussions](https://github.com/LeoDev2p/mcp_qgis/discussions)

---

**Thank you for contributing!**
