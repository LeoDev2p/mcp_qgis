from fastmcp import FastMCP

mcp = FastMCP("MCP Server", instructions="A simple MCP server example")

@mcp.tool()
def qgis (data: str) -> str:
    """Función de saludo que devuelve un mensaje personalizado."""
    return f"¡Bienvenido a, {data}! al servidor MCP."

if __name__ == '__main__':
    mcp.run()