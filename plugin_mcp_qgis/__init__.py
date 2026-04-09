def classFactory(iface):
    from .mcp_plugin import QgisMCPPlugin

    return QgisMCPPlugin(iface)
