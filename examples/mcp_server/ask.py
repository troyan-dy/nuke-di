"""
An MCP host in a few lines: start a server over stdio, list its tools and call one. Run `python -m mcp_server.ask`,
or `python -m mcp_server.ask fastmcp_server` for the FastMCP one.
"""

import asyncio
import sys

from mcp import Client, StdioServerParameters


async def main(module: str) -> None:
    server = StdioServerParameters(command=sys.executable, args=["-m", f"mcp_server.{module}"])
    async with Client(server) as client:
        for tool in (await client.list_tools()).tools:
            arguments = ", ".join(tool.input_schema["properties"])
            print(f"tool {tool.name}({arguments}): {tool.description}")
        tool_result = await client.call_tool("count_books", {"genre": "fantasy"})
        print(f"count_books(genre='fantasy') = {tool_result.structured_content}")


if __name__ == "__main__":
    server_module = sys.argv[1] if len(sys.argv) > 1 else "server"
    asyncio.run(main(server_module))
