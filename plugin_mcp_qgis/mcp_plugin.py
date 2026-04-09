import contextlib
import json
import struct
from pathlib import Path
from typing import ClassVar

from qgis.core import Qgis, QgsApplication, QgsMessageLog, QgsSettings
from qgis.PyQt.QtCore import QObject, Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices, QIcon
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

BASE_DIR = Path(__file__).resolve().parent
PATH_ASSETS = BASE_DIR / "assets"

_DEFAULT_HOST = "localhost"
_DEFAULT_PORT = 9876
_RECV_CHUNK_SIZE = 65536
_MAX_MESSAGE_SIZE = 10 * 1024 * 1024  # 10 MB
_HEADER_STRUCT = struct.Struct(">I")

# ── Message levels ───────────────────────────────────────────────────
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
    TOOLBUTTON_MENU_POPUP = QToolButton.ToolButtonPopupMode.MenuButtonPopup
except AttributeError:
    TOOLBUTTON_MENU_POPUP = QToolButton.MenuButtonPopup

try:
    TOOLBUTTON_ICON_ONLY = Qt.ToolButtonStyle.ToolButtonIconOnly
except AttributeError:
    TOOLBUTTON_ICON_ONLY = Qt.ToolButtonIconOnly


class QgisMCPServer(QObject):
    """Server class to handle network connections and execute QGIS commands natively with Qt."""

    LOG_TAG: ClassVar[str] = "MCP"
    MAX_CLIENTS: ClassVar[int] = 10

    def __init__(self, host=_DEFAULT_HOST, port=_DEFAULT_PORT, iface=None):
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
            )
            return True
        else:
            QgsMessageLog.logMessage(
                f"Failed to start server: {self.server.errorString()}", self.LOG_TAG
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
                "buscador_dinamico_mcp": self.buscador_dinamico_mcp,
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

    # -----------------------------------------------------------------------
    # Command handlers
    # -----------------------------------------------------------------------

    def ping(self, **kwargs):
        return {"pong": True}

    def buscador_dinamico_mcp(self, busqueda: str, **kwargs):
        registry = QgsApplication.processingRegistry()
        algs = registry.algorithms()

        # Filtrar objetos
        filtrados = [a for a in algs if busqueda.lower() in a.displayName().lower()]

        # Devolver un resumen serializable a JSON
        return [
            {
                "id": a.id(),
                "titulo": a.displayName(),
                "descripcion_corta": a.shortDescription(),
                "tags": a.tags(),
            }
            for a in filtrados[:15]
        ]

    def get_algorithm_details(alg_id):
        alg = QgsApplication.processingRegistry().algorithmById(alg_id)
        if not alg:
            return {"error": "Algoritmo no encontrado"}

        detalles = {"id": alg.id(), "nombre": alg.displayName(), "parametros": []}

        # Esto le dice a Claude EXACTAMENTE qué escribir en el JSON
        for param in alg.parameterDefinitions():
            detalles["parametros"].append(
                {
                    "nombre": param.name(),
                    "descripcion": param.description(),
                    "tipo": str(type(param)),
                    "valor_defecto": param.defaultValue(),
                }
            )

        return detalles


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
        self._toolbar_action = None  # the action wrapping the tool button

    def _logo_icon(self):
        """Load the MCP logo from the plugin directory."""
        icon_path = f"{PATH_ASSETS}/icono.ico"
        return QIcon(icon_path)

    def initGui(self):
        toolbar = self.iface.pluginToolBar()

        # Main action (used for menu entry + click handler)
        self.action = QAction(self._logo_icon(), "Run MCP", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.setToolTip(f"Start MCP server on port {_DEFAULT_PORT}")
        self.action.triggered.connect(self.toggle_server)

        # Port config in dropdown menu
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(_DEFAULT_PORT)
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
        saved_port = settings.value(
            f"{self.SETTINGS_PREFIX}/port", _DEFAULT_PORT, type=int
        )
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
        icon_path = f"{PATH_ASSETS}/icono.ico"
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
