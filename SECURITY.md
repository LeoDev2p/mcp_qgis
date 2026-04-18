# Security

## Responsible Disclosure Policy

If you find a vulnerability, **DO NOT open a public issue or PR**.

**Contact:**
- Email: **[whoamy0608@gmail.com]**
- Subject: `[SECURITY] QGIS MCP - Brief description`
- Include:
  - Technical description
  - Steps to reproduce
  - Potential impact

**SLA (guaranteed):**
- Critical: Patch in 7 days
- High: Patch in 14 days
- Medium: Patch in 30 days

We'll agree on a public embargo (60-90 days) before announcing the fix.

---

## Threat Model

### What Permissions Does This MCP Request

The MCP exposes **all PyQGIS functions** to the LLM. This means:

| Permission | Scope | Risk | Mitigation |
|-----------|-------|------|-----------|
| **File system read** | Reads files in `/home/*` | Data exfiltration | Localhost only |
| **File system write** | Creates/modifies files | Malware, corruption | Restrict to project dirs |
| **Python code execution** | `execute_code()` tool | Code injection | Input validation |
| **PyQGIS API access** | `QgsProject`, `processing` | Project changes | Implicit confirmation (plugin active) |
| **Geospatial layer access** | Read/modify SIG data | Data corruption | Backups recommended |

### What This MCP Cannot Do

- Access networks (no HTTP/HTTPS)
- Execute system commands (no `os.system()` exposed)
- Install packages (`pip` blocked)
- Modify MCP code at runtime

---

## Input Validation

### Parameter Sanitization

All LLM input is validated before passing to PyQGIS:

```python
# EXAMPLE: load_layer_from_path

def load_layer(path: str, name: str = None) -> dict:
    # 1. Validate type
    if not isinstance(path, str):
        return {"status": "error", "message": "path must be string"}
    
    # 2. Sanitize path (prevent directory traversal)
    path = os.path.normpath(path)  # Resolves ../ 
    if not os.path.isfile(path):
        return {"status": "error", "message": "file not found"}
    
    # 3. Validate extension (whitelist)
    allowed_ext = ['.shp', '.tif', '.geojson', '.gpkg']
    if not any(path.endswith(ext) for ext in allowed_ext):
        return {"status": "error", "message": "unsupported file type"}
    
    # 4. Execute with exception handling
    try:
        layer = QgsProject.instance().addMapLayer(...)
        return {"status": "success", ...}
    except Exception as e:
        return {"status": "error", "message": str(e)}
```

**Rule:** 
- Type checking (Pydantic for complex parameters)
- Whitelist of allowed values (extensions, algorithms, etc)
- Path sanitization (prevent `../../../etc/passwd`)
- Try-except for all PyQGIS operations

---

## Data Privacy

### 100% Local Processing

**QGIS MCP does not send data to external servers:**

Local processing:
- File read/write: on your machine
- Geospatial analysis: in QGIS Desktop (your RAM/CPU)
- GRASS/SAGA algorithms: execute locally
- Logs: stored in `mcp_qgis/log/` (your machine)

No remote connections:
- No telemetry
- No analytics
- No cloud processing
- No external APIs (except QGIS native providers like WMS/WFS, user's choice)

**Data handled:**
- File paths (local)
- Layer names (in memory)
- Feature attributes (in memory)
- Processing results (local)

**Data NOT handled:**
- User location
- Browser history
- User information
- QGIS credentials

---

## Current Security Measures

### v0.1.0 (Current)

| Measure | Status | Description |
|---------|--------|-------------|
| **Localhost only** | ✅ | TCP on `localhost:9876` (not remote) |
| **Manual activation** | ✅ | Plugin requires explicit click in QGIS |
| **Current user perms** | ✅ | Runs with user permissions (no sudo) |
| **Command logging** | ✅ | All commands in `log/mcp_qgis.log` |
| **Input validation** | ✅ | Path and parameter sanitization |
| **Type checking** | ✅ | Validation with Python types |

### v0.3.0+ (Planned)

| Measure | Timeline | Description |
|---------|----------|-------------|
| **RestrictedPython** | Q3 2025 | Sandbox for `execute_code()` |
| **Token auth** | Q3 2025 | Token authentication (localhost) |
| **TLS encryption** | Q3 2025 | TCP + TLS on port 9877 |
| **Audit trail** | Q4 2025 | Detailed log of each operation |
| **Rate limiting** | Q4 2025 | Limit #commands/minute |

---

## Known Risks

### 🔴 Critical

**`execute_code()` - Executes Python without restrictions**
```python
# LLM could generate:
import shutil
shutil.rmtree("/home/user/important_data")  # ❌ DELETES EVERYTHING
```
**Current mitigation:** Localhost only, plugin active required  
**Future mitigation:** RestrictedPython sandbox (whitelist modules)

### 🟠 High

**`delete_file()` - Permanent deletion (no trash)**
```python
# LLM could generate:
os.remove("/path/to/shapefile.shp")  # ❌ No undo
```
**Mitigation:** Git backups, data versioning

**`remove_layer()` - Delete layers without confirmation**
- **Mitigation:** Automatic logs, implicit confirmation (user opened plugin)

### 🟡 Medium

**Path traversal in `load_layer_from_path()`**
```python
# LLM could try:
path = "../../../../etc/passwd"  # Sanitized to ../etc/passwd
```
**Mitigation:** `os.path.normpath()` + existence validation

**Algorithm injection in `run_processing()`**
```python
# LLM could send:
algorithm_id = "'; DROP TABLE layers; --"  # Not SQL, but we validate
```
**Mitigation:** Whitelist of valid algorithm IDs

---

##  Updates

### How to Stay Informed

- **GitHub:** Enable "Releases" notifications
  - Settings → Notifications → Custom → Security alerts 
- **Email:** Subscribe to [security@leodev2p.com] (coming soon)
- **RSS:** Releases feed (if you enable GitHub Releases)

### Version Support

| Version | Status | Security Support |
|---------|--------|-----------------|
| 0.1.0 | Current | Yes (until 0.2.0) |
| 0.2.0 | Next | Yes (until 0.3.0) |
| < 0.1.0 | EOL | No (upgrade) |

**Recommendation:** Always use the latest version.

---

## Report Vulnerabilities

### Email Template

```
To: security@leodev2p.com
Subject: [SECURITY] QGIS MCP - Short issue name

## Description
[Clear technical description]

## Severity
[ ] Critical  [ ] High  [ ] Medium  [ ] Low

## Steps to Reproduce
1. ...
2. ...

## Impact
[What can be achieved, who is affected]

## System Information
- QGIS version: 
- Python version:
- OS:
- QGIS MCP version:
```

---

## Security FAQ

**Q: Is it safe to use in production?**  
A: No. v0.1.0 is experimental. Use only for development/testing. See v0.3.0+ for production.

**Q: Can I expose the MCP on the internet?**  
A: **No.** Only safe on localhost. In v0.3.0 there will be TLS + token auth.

**Q: Where are logs stored?**  
A: In `mcp_qgis/log/mcp_qgis.log`. They are **private** (your machine).

**Q: Can I modify `execute_code()` to be more secure?**  
A: Yes, contributions welcome. Use RestrictedPython. See [CONTRIBUTING.md](CONTRIBUTING.md).

---

**Remember: Use locally only. You've been warned.**
