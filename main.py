from fastmcp import FastMCP

mcp = FastMCP("MCP Server", instructions="A simple MCP server example")

@mcp.tool()
def saludo (nombre: str) -> str:
    """Función de saludo que devuelve un mensaje personalizado."""
    return f"¡Hola, {nombre}! Bienvenido al servidor MCP."

if __name__ == '__main__':
    mcp.run()