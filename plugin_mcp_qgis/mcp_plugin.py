import contextlib
import json
import struct
import sys
from pathlib import Path
from typing import ClassVar

import processing
from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCategorizedSymbolRenderer,
    QgsClassificationEqualInterval,
    QgsClassificationJenks,
    QgsClassificationQuantile,
    QgsFeatureRequest,
    QgsFillSymbol,
    QgsGraduatedSymbolRenderer,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsMessageLog,
    QgsProject,
    QgsRasterLayer,
    QgsRendererCategory,
    QgsRendererRange,
    QgsSettings,
    QgsSingleSymbolRenderer,
    QgsStyle,
    QgsSymbol,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QObject, Qt, QUrl
from qgis.PyQt.QtGui import QColor, QDesktopServices, QIcon
from qgis.PyQt.QtNetwork import QHostAddress, QTcpServer
from qgis.PyQt.QtWidgets import (
    QAction,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

# Assets route configuration
BASE_DIR = Path(__file__).resolve().parent
PATH_ASSETS = BASE_DIR / "assets"

_HOST = "localhost"
_PORT = 9876
_RECV_CHUNK_SIZE = 65536
_MAX_MESSAGE_SIZE = 10 * 1024 * 1024  # 10 MB
_HEADER_STRUCT = struct.Struct(">I")

# ---------- Message levels -----------------------
try:
    MSG_INFO = Qgis.MessageLevel.Info
except AttributeError:
    MSG_INFO = Qgis.Info

try:
    MSG_WARNING = Qgis.MessageLevel.Warning
except AttributeError:
    MSG_WARNING = Qgis.Warning

try:
    MSG_CRITICAL = Qgis.MessageLevel.Critical
except AttributeError:
    MSG_CRITICAL = Qgis.Critical

try:
    MSG_SUCCESS = Qgis.MessageLevel.Success
except AttributeError:
    MSG_SUCCESS = MSG_INFO  # Fallback for older QGIS versions

try:
    TOOLBUTTON_MENU_POPUP = QToolButton.ToolButtonPopupMode.MenuButtonPopup
except AttributeError:
    TOOLBUTTON_MENU_POPUP = QToolButton.MenuButtonPopup

try:
    TOOLBUTTON_ICON_ONLY = Qt.ToolButtonStyle.ToolButtonIconOnly
except AttributeError:
    TOOLBUTTON_ICON_ONLY = Qt.ToolButtonIconOnly

# * ------- Qgis server -----------------


class QgisMCPServer(QObject):
    """Server class to handle network connections and execute QGIS commands natively with Qt."""

    LOG_TAG: ClassVar[str] = "MCP"
    MAX_CLIENTS: ClassVar[int] = 10

    def __init__(self, host=_HOST, port=_PORT, iface=None):
        super().__init__()
        self.host = host
        self.port = port
        self.iface = iface
        self.running = False
        self.server = None
        self.clients = {}

    def start(self):
        """Start the async QTcpServer"""
        self.server = QTcpServer(self)
        self.server.newConnection.connect(self.on_new_connection)

        address = QHostAddress(self.host)
        if self.host.lower() == "localhost":
            address = QHostAddress(QHostAddress.SpecialAddress.LocalHost)

        if self.server.listen(address, self.port):
            self.running = True
            QgsMessageLog.logMessage(
                f"QGIS MCP server (QtNative) started on {self.host}:{self.port}",
                self.LOG_TAG,
                MSG_INFO,
            )
            return True
        else:
            QgsMessageLog.logMessage(
                f"Failed to start server: {self.server.errorString()}",
                self.LOG_TAG,
                MSG_WARNING,
            )
            self.server = None
            return False

    def on_new_connection(self):
        """Handle incoming TCP connections cleanly on the Qt Event Loop"""
        while self.server.hasPendingConnections():
            client_sock = self.server.nextPendingConnection()

            if len(self.clients) >= self.MAX_CLIENTS:
                client_sock.disconnectFromHost()
                continue

            self.clients[client_sock] = b""
            # Attach readyRead directly to parse data when available natively
            client_sock.readyRead.connect(
                lambda sock=client_sock: self.on_ready_read(sock)
            )
            client_sock.disconnected.connect(
                lambda sock=client_sock: self.on_disconnected(sock)
            )

            QgsMessageLog.logMessage(
                f"Connected to client ({len(self.clients)} active)",
                self.LOG_TAG,
                MSG_INFO,
            )

    def on_ready_read(self, client_sock):
        """Slot invoked directly when the socket has incoming data."""
        data_bytes = client_sock.readAll().data()
        if not data_bytes:
            return

        buf = self.clients.get(client_sock, b"") + data_bytes

        if len(buf) > _MAX_MESSAGE_SIZE:
            # Drop connection if buffer grows too huge
            client_sock.disconnectFromHost()
            return

        # Process complete length-prefixed messages
        while len(buf) >= 4:
            msg_len = _HEADER_STRUCT.unpack(buf[:4])[0]
            if msg_len > _MAX_MESSAGE_SIZE:
                QgsMessageLog.logMessage("Message too large", self.LOG_TAG, MSG_WARNING)
                client_sock.disconnectFromHost()
                return
            if len(buf) < 4 + msg_len:
                break  # Incomplete message, wait for next readyRead

            msg_bytes = buf[4 : 4 + msg_len]
            buf = buf[4 + msg_len :]

            try:
                command = json.loads(msg_bytes.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                QgsMessageLog.logMessage(
                    f"Malformed request: {e!s}", self.LOG_TAG, MSG_WARNING
                )
                self._send_response(
                    client_sock,
                    {
                        "status": "error",
                        "message": f"Invalid JSON: {e!s}",
                    },
                )
                continue

            # Process command
            response = self.execute_command(command)
            self._send_response(client_sock, response)

        self.clients[client_sock] = buf

    def execute_command(self, command):
        """Execute a command"""
        try:
            cmd_type = command.get("type")
            params = command.get("params", {})

            handlers = {
                "ping": self.ping,
                "search_geoprocessing_tools": self.search_geoprocessing_tools,
                "get_algorithm_details": self.get_algorithm_details,
                "run_processing": self.run_processing,
                "get_project_context": self.get_project_context,
                "get_layer_features": self.get_layer_features,
                "get_selection": self.get_selection,
                "load_layer_from_path": self.load_layer_from_path,
                "save_project": self.save_project,
                "remove_layer": self.remove_layer,
                "delete_file": self.delete_file,
                "show_message": self.show_message,
                "execute_code": self.execute_code,
            }

            handler = handlers.get(cmd_type)
            if handler:
                try:
                    QgsMessageLog.logMessage(
                        f"Executing: {cmd_type}", self.LOG_TAG, MSG_INFO
                    )
                    result = handler(**params)
                    return {"status": "success", "result": result}
                except Exception as e:
                    QgsMessageLog.logMessage(
                        f"Error in {cmd_type}: {e!s}", self.LOG_TAG, MSG_CRITICAL
                    )
                    return {"status": "error", "message": str(e)}
            else:
                QgsMessageLog.logMessage(
                    f"Unknown command: {cmd_type}", self.LOG_TAG, MSG_WARNING
                )
                return {
                    "status": "error",
                    "message": f"Unknown command type: {cmd_type}",
                }

        except Exception as e:
            QgsMessageLog.logMessage(
                f"Error executing command: {e!s}", self.LOG_TAG, MSG_CRITICAL
            )
            return {"status": "error", "message": str(e)}

    def _send_response(self, client_sock, response):
        """Send length-prefixed JSON natively via QTcpSocket."""
        resp_bytes = json.dumps(response).encode("utf-8")
        header = _HEADER_STRUCT.pack(len(resp_bytes))
        client_sock.write(header + resp_bytes)
        client_sock.flush()

    def on_disconnected(self, client_sock):
        """Clean up when client closes the socket."""
        if client_sock in self.clients:
            del self.clients[client_sock]
        client_sock.deleteLater()
        QgsMessageLog.logMessage(
            f"Client disconnected ({len(self.clients)} active)", self.LOG_TAG, MSG_INFO
        )

    def stop(self):
        """Stop the server natively."""
        self.running = False
        if self.server:
            self.server.close()
            self.server.deleteLater()
            self.server = None

        for client_sock in list(self.clients):
            with contextlib.suppress(Exception):
                client_sock.disconnectFromHost()
        self.clients.clear()

        QgsMessageLog.logMessage("QGIS MCP server stopped", self.LOG_TAG, MSG_INFO)

    # *  Command handlers ----------------------------------------

    def ping(self, **kwargs):
        """Check connection between MCP server and QGIS plugin."""
        return {"pong": True}

    def search_geoprocessing_tools(self, search: str, **kwargs):
        """Search for QGIS processing algorithms by display name."""
        registry = QgsApplication.processingRegistry()
        algs = registry.algorithms()

        # Filtrar objetos
        filtrados = [a for a in algs if search.lower() in a.displayName().lower()]

        # Devolver un resumen serializable a JSON
        return [
            {
                "id": a.id(),
                "title": a.displayName(),
                "description_short": a.shortDescription(),
                "tags": a.tags(),
            }
            for a in filtrados[:15]
        ]

    def get_algorithm_details(self, alg_id, **kwargs):
        """Get parameter definitions and details for a specific algorithm."""
        alg = QgsApplication.processingRegistry().algorithmById(alg_id)
        if not alg:
            return {"error": "Algorithm not found"}

        detalles = {"id": alg.id(), "name": alg.displayName(), "parameters": []}

        # Esto le dice a Claude EXACTAMENTE qué escribir en el JSON
        for param in alg.parameterDefinitions():
            detalles["parameters"].append(
                {
                    "name": param.name(),
                    "description": param.description(),
                    "type": str(type(param)),
                    "default_value": param.defaultValue(),
                }
            )

        return detalles

    def run_processing(self, algorithm: str, parameter: dict = None, **kwargs) -> dict:
        """Run a QGIS Processing algorithm.

        Args:
            algorithm: Full algorithm ID (e.g. 'native:buffer').
            parameter: Dict of algorithm parameters with exact names from
                       get_algorithm_details (e.g. {'INPUT': 'path', 'DISTANCE': 100}).
        """
        try:
            params = parameter or {}
            output = processing.run(algorithm, params)
            return {
                "algorithm": algorithm,
                "output": {key: str(value) for key, value in output.items()},
            }
        except Exception as e:
            raise Exception(f"Processing error: {str(e)}")

    def get_project_context(self, **kwargs):
        """Snapshot of loaded layers, fields, and project CRS."""
        layers = QgsProject.instance().mapLayers().values()
        context = []
        for layer in layers:
            # Obtenemos los nombres de los campos si es vectorial
            fields = [f.name() for f in layer.fields()] if layer.type() == 0 else []

            context.append(
                {
                    "name": layer.name(),
                    "id": layer.id(),
                    "type": "Vector" if layer.type() == 0 else "Raster",
                    "geometry": layer.geometryType() if layer.type() == 0 else "N/A",
                    "source": layer.source(),
                    "fields": fields,
                    "crs": layer.crs().authid(),
                }
            )
        return context

    def load_layer_from_path(self, path: str, name: str = "Nueva Capa", **kwargs):
        """Load a vector or raster file from disk into the project."""
        import os

        if not os.path.exists(path):
            return {"error": f"The path {path} does not exist"}

        # Detectar si es raster o vector por extensión
        ext = os.path.splitext(path)[1].lower()
        if ext in [".shp", ".gpkg", ".geojson", ".kml", ".csv"]:
            layer = QgsVectorLayer(path, name, "ogr")
        elif ext in [".tif", ".tiff", ".asc", ".img"]:
            layer = QgsRasterLayer(path, name, "gdal")
        else:
            return {"error": "Format not supported"}

        if not layer.isValid():
            return {"error": "The layer is invalid or corrupt"}

        QgsProject.instance().addMapLayer(layer)
        return {"status": "success", "layer_id": layer.id(), "name": layer.name()}

    def save_project(self, path: str = "", **kwargs) -> dict:
        """Save the current QGIS project to disk.

        Args:
            path: Absolute file path where the project will be saved
                  (.qgz or .qgs extension). If empty, saves to the
                  project's current path (must have been saved before).
        """
        import os

        project = QgsProject.instance()

        if not path:
            current = project.fileName()
            if not current:
                return {
                    "error": "The project has no path. Specify a path using the 'path' parameter."
                }
            path = current

        directory = os.path.dirname(path)
        if directory and not os.path.exists(directory):
            try:
                os.makedirs(directory, exist_ok=True)
            except OSError as e:
                return {"error": f"Could not create directory: {e}"}

        ok = project.write(path)
        if ok:
            return {
                "status": "success",
                "path": path,
                "message": f"Project saved in {path}",
            }
        return {"error": f"QGIS could not save the project to {path}"}

    def remove_layer(self, layer_id: str, **kwargs) -> dict:
        """Remove a layer from the QGIS project by ID (does not delete the file)."""
        project = QgsProject.instance()
        layer = project.mapLayer(layer_id)
        if not layer:
            return {"error": f"Layer not found: {layer_id}"}
        name = layer.name()
        project.removeMapLayer(layer_id)
        return {
            "status": "success",
            "message": f"Layer '{name}' removed from project (file on disk intact)",
        }

    def delete_file(self, path: str, **kwargs) -> dict:
        """Delete a project file (.qgz / .qgs) from disk permanently.

        Restricted to QGIS project file extensions for safety.
        The confirmation dialog is handled on the server side before this executes.
        """
        import os

        allowed_ext = {".qgz", ".qgs"}
        ext = os.path.splitext(path)[1].lower()
        if ext not in allowed_ext:
            return {
                "error": (
                    "Only QGIS project files (.qgz, .qgs) can be deleted.",
                    f"Received extension: '{ext}'",
                )
            }

        if not os.path.exists(path):
            return {"error": f"The file does not exist: {path}"}

        try:
            os.remove(path)
            return {"status": "success", "message": f"Archivo eliminado: {path}"}
        except OSError as e:
            return {"error": f"Could not delete file: {e}"}

    def get_layer_features(
        self,
        layer_id: str,
        limit: int = 10,
        offset: int = 0,
        filter_expression: str = "",
        include_geometry: bool = False,
        **kwargs,
    ) -> dict:
        """Read features from a vector layer and return them as a JSON-safe dict.

        Supports pagination (limit/offset) and QGIS expression filtering.
        """
        project = QgsProject.instance()
        layer = project.mapLayer(layer_id)
        if not layer:
            return {"error": f"Layer not found: {layer_id}"}
        if not isinstance(layer, QgsVectorLayer):
            return {"error": "get_layer_features only works with vector layers"}

        fields = [f.name() for f in layer.fields()]

        request = QgsFeatureRequest()
        if filter_expression:
            request.setFilterExpression(filter_expression)

        features_out = []
        skipped = 0
        for feat in layer.getFeatures(request):
            if skipped < offset:
                skipped += 1
                continue

            attrs = {}
            for field in fields:
                val = feat[field]
                if isinstance(val, (int, float, str, bool, type(None))):
                    attrs[field] = val
                else:
                    attrs[field] = str(val)

            feat_dict = {"id": feat.id(), "attributes": attrs}

            if include_geometry:
                geom = feat.geometry()
                if geom and not geom.isEmpty():
                    feat_dict["geometry_wkt"] = geom.asWkt(precision=6)

            features_out.append(feat_dict)
            if len(features_out) >= limit:
                break

        return {
            "layer_id": layer_id,
            "layer_name": layer.name(),
            "total_features": layer.featureCount(),
            "returned": len(features_out),
            "offset": offset,
            "limit": limit,
            "fields": fields,
            "features": features_out,
        }

    def get_selection(self, layer_id: str, **kwargs) -> dict:
        """Return the features currently selected by the user on the QGIS canvas."""
        project = QgsProject.instance()
        layer = project.mapLayer(layer_id)
        if not layer:
            return {"error": f"Layer not found: {layer_id}"}
        if not isinstance(layer, QgsVectorLayer):
            return {"error": "The selection only applies to vector layers"}

        fields = [f.name() for f in layer.fields()]
        selected = layer.selectedFeatures()

        features_out = []
        for feat in selected:
            attrs = {}
            for field in fields:
                val = feat[field]
                if isinstance(val, (int, float, str, bool, type(None))):
                    attrs[field] = val
                else:
                    attrs[field] = str(val)
            geom = feat.geometry()
            feat_dict = {"id": feat.id(), "attributes": attrs}
            if geom and not geom.isEmpty():
                feat_dict["geometry_wkt"] = geom.asWkt(precision=6)
            features_out.append(feat_dict)

        return {
            "layer_id": layer_id,
            "layer_name": layer.name(),
            "selected_count": len(features_out),
            "fields": fields,
            "features": features_out,
        }

    def show_message(
        self, text: str, level: str = "info", duration: int = 5, **kwargs
    ) -> dict:
        """Display a message in the QGIS message bar for user feedback."""
        if not self.iface:
            return {
                "error": "iface unavailable — the server was not started from the plugin"
            }

        level_map = {
            "info": MSG_INFO,
            "warning": MSG_WARNING,
            "error": MSG_CRITICAL,
            "success": MSG_SUCCESS,
        }
        qgis_level = level_map.get(level.lower(), MSG_INFO)

        self.iface.messageBar().pushMessage(
            "Claude MCP",
            text,
            level=qgis_level,
            duration=duration,
        )
        return {"status": "success", "message": f"Mostrado en QGIS: {text}"}

    def execute_code(self, code: str, **kwargs):
        """
        Run arbitrary Python code within the QGIS environment.
        The execution context exposes the most frequently used classes and instances
        so the LLM doesn't need to import anything manually:
        """
        try:
            iface_obj = getattr(sys.modules.get("qgis.utils"), "iface", None)
            globals_dict = {
                # -- Proyecto y UI --
                "QgsProject": QgsProject,
                "QgsApplication": QgsApplication,
                "iface": iface_obj,
                "canvas": iface_obj.mapCanvas() if iface_obj else None,
                # -- Geoprocesamiento --
                "processing": sys.modules.get("processing"),
                # -- Simbología: renderers --
                "QgsGraduatedSymbolRenderer": QgsGraduatedSymbolRenderer,
                "QgsCategorizedSymbolRenderer": QgsCategorizedSymbolRenderer,
                "QgsSingleSymbolRenderer": QgsSingleSymbolRenderer,
                "QgsRendererRange": QgsRendererRange,
                "QgsRendererCategory": QgsRendererCategory,
                # -- Simbología: símbolos --
                "QgsSymbol": QgsSymbol,
                "QgsFillSymbol": QgsFillSymbol,
                "QgsLineSymbol": QgsLineSymbol,
                "QgsMarkerSymbol": QgsMarkerSymbol,
                # -- Clasificación automática --
                "QgsClassificationJenks": QgsClassificationJenks,
                "QgsClassificationQuantile": QgsClassificationQuantile,
                "QgsClassificationEqualInterval": QgsClassificationEqualInterval,
                # -- Estilos y color --
                "QgsStyle": QgsStyle,
                "QColor": QColor,
            }
            # Capture stdout so print() statements are visible to the LLM
            import io

            stdout_capture = io.StringIO()
            globals_dict["_result"] = None  # LLM can set this to return structured data

            import sys as _sys

            _old_stdout = _sys.stdout
            _sys.stdout = stdout_capture
            try:
                exec(code, globals_dict)
            finally:
                _sys.stdout = _old_stdout

            output = stdout_capture.getvalue()
            result = globals_dict.get("_result")

            response = {"status": "success"}
            if output:
                response["output"] = output.strip()
            if result is not None:
                response["result"] = result
            if not output and result is None:
                response["message"] = "Código ejecutado correctamente"
            return response
        except Exception as e:
            return {"error": f"Error de ejecución: {str(e)}"}


class QgisMCPPlugin:
    """Main plugin class for QGIS MCP"""

    REPO_URL = "https://github.com/LeoDev2p/mcp_qgis"

    SETTINGS_PREFIX = "qgis_mcp"

    def __init__(self, iface):
        self.iface = iface
        self.server = None
        self.action = None
        self.help_action = None
        self.tool_button = None
        self._toolbar_action = None

    def _logo_icon(self):
        """Load the MCP logo from the plugin directory."""
        icon_path = f"{PATH_ASSETS}/icons/icono.png"
        return QIcon(icon_path)

    def initGui(self):
        toolbar = self.iface.pluginToolBar()

        # Main action (used for menu entry + click handler)
        self.action = QAction(self._logo_icon(), "Run MCP", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.setToolTip(f"Start MCP server on port {_PORT}")
        self.action.triggered.connect(self.toggle_server)

        # Port config in dropdown menu
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(_PORT)
        self.port_spin.setPrefix("Port: ")
        self.port_spin.valueChanged.connect(self._save_port)

        port_widget = QWidget()
        port_layout = QHBoxLayout()
        port_layout.setContentsMargins(6, 4, 6, 4)
        port_layout.addWidget(self.port_spin)
        port_widget.setLayout(port_layout)

        port_wa = QWidgetAction(self.iface.mainWindow())
        port_wa.setDefaultWidget(port_widget)

        # Auto-start checkbox
        self.autostart_cb = QCheckBox("Auto-start on startup")
        settings = QgsSettings()
        self.autostart_cb.setChecked(
            settings.value(f"{self.SETTINGS_PREFIX}/autostart", False, type=bool)
        )
        self.autostart_cb.toggled.connect(self._save_autostart)

        autostart_widget = QWidget()
        autostart_layout = QHBoxLayout()
        autostart_layout.setContentsMargins(6, 4, 6, 4)
        autostart_layout.addWidget(self.autostart_cb)
        autostart_widget.setLayout(autostart_layout)

        autostart_wa = QWidgetAction(self.iface.mainWindow())
        autostart_wa.setDefaultWidget(autostart_widget)

        menu = QMenu()
        menu.addAction(port_wa)
        menu.addAction(autostart_wa)

        # Tool button with dropdown (like Plugin Reloader)
        self.tool_button = QToolButton()
        self.tool_button.setDefaultAction(self.action)
        self.tool_button.setMenu(menu)
        self.tool_button.setPopupMode(TOOLBUTTON_MENU_POPUP)
        self.tool_button.setToolButtonStyle(TOOLBUTTON_ICON_ONLY)
        self._toolbar_action = toolbar.addWidget(self.tool_button)

        self.help_action = QAction("Help / Install MCP Server", self.iface.mainWindow())
        self.help_action.triggered.connect(self._show_help)

        self.iface.addPluginToMenu("QGIS MCP", self.action)
        self.iface.addPluginToMenu("QGIS MCP", self.help_action)

        # Restore saved port
        saved_port = settings.value(f"{self.SETTINGS_PREFIX}/port", _PORT, type=int)
        self.port_spin.setValue(saved_port)

        # Auto-start if enabled
        if self.autostart_cb.isChecked():
            self.action.setChecked(True)
            self.toggle_server(True)

    def _save_autostart(self, checked):
        """Persist auto-start preference."""
        QgsSettings().setValue(f"{self.SETTINGS_PREFIX}/autostart", checked)

    def _save_port(self, port):
        """Persist port preference."""
        QgsSettings().setValue(f"{self.SETTINGS_PREFIX}/port", port)

    def _green_logo_icon(self):
        """Load the green MCP logo for active state."""
        icon_path = f"{PATH_ASSETS}/icons/icono.png"
        return QIcon(icon_path)

    def _show_help(self):
        """Show help dialog with MCP server installation instructions."""
        dlg = QDialog(self.iface.mainWindow())
        dlg.setWindowTitle("QGIS MCP — Setup Guide")
        dlg.setMinimumWidth(520)

        layout = QVBoxLayout()
        label = QLabel(
            "<p>This plugin is only one half of the setup. You also need an "
            "<b>MCP server</b> so that Claude (or another LLM) can talk to QGIS.</p>"
            "<p><b>Quick setup:</b> Run <code>python install.py</code> from the "
            "repository root to configure your MCP client(s) automatically.</p>"
            "<p>Full instructions are on the "
            f'<a href="{self.REPO_URL}#installation">GitHub repository</a>.</p>'
        )
        label.setWordWrap(True)
        label.setOpenExternalLinks(True)
        layout.addWidget(label)

        btn_layout = QHBoxLayout()
        github_btn = QToolButton()
        github_btn.setText("Open GitHub")
        github_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(self.REPO_URL))
        )
        btn_layout.addWidget(github_btn)
        btn_layout.addStretch()
        ok_btn = QToolButton()
        ok_btn.setText("OK")
        ok_btn.setMinimumWidth(80)
        ok_btn.clicked.connect(dlg.accept)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)

        dlg.setLayout(layout)
        dlg.exec()

    def toggle_server(self, checked):
        if checked:
            port = self.port_spin.value()
            self.server = QgisMCPServer(port=port, iface=self.iface)
            if self.server.start():
                self.action.setIcon(self._green_logo_icon())
                self.action.setText(f"MCP :{port}")
                self.action.setToolTip(f"MCP server running on :{port} — click to stop")
                self.port_spin.setEnabled(False)
            else:
                self.server = None
                self.action.setChecked(False)
        else:
            if self.server:
                self.server.stop()
                self.server = None
            self.action.setIcon(self._logo_icon())
            self.action.setText("Run MCP")
            self.action.setToolTip("Start MCP server")
            self.port_spin.setEnabled(True)

    def unload(self):
        if self.server:
            self.server.stop()
            self.server = None
        if self.action:
            self.action.triggered.disconnect(self.toggle_server)
            self.iface.removePluginMenu("QGIS MCP", self.action)
            self.action = None
        if self.help_action:
            self.help_action.triggered.disconnect(self._show_help)
            self.iface.removePluginMenu("QGIS MCP", self.help_action)
            self.help_action = None
        if self._toolbar_action:
            self.iface.pluginToolBar().removeAction(self._toolbar_action)
            self._toolbar_action = None
        if hasattr(self, "port_spin"):
            self.port_spin.valueChanged.disconnect(self._save_port)
        if hasattr(self, "autostart_cb"):
            self.autostart_cb.toggled.disconnect(self._save_autostart)
