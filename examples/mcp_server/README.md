# MCP server

A bookshop as an MCP server, twice: on the official SDK's `MCPServer` with `nuke_di.mcp`, and on FastMCP with
`nuke_di.fastmcp`. The tools take the shop's clients by type hint; the LLM sees only `genre`.

| File                | What it holds                                                                       |
|---------------------|-------------------------------------------------------------------------------------|
| `clients.py`        | `Database` and `Catalog` that depends on it; they print to stderr, as stdout is MCP's |
| `server.py`         | `MCPServer` with two tools, `setup(server)` before them                              |
| `fastmcp_server.py` | The same on FastMCP, with a dependency, a resource and a prompt that take clients   |
| `ask.py`            | An MCP host in a few lines: starts a server over stdio, lists its tools, calls one  |
| `test_app.py`       | In-memory clients with `Database` replaced through `DI.override()`                  |

## Run

```console
$ cd examples
$ uv run python -m mcp_server.ask
database: connected
[10/10/26 18:43:49] INFO     Connected 2 clients in 0.00s (slowest:   core.py:177
                             Database 0.00s, Catalog 0.00s)
tool count_books(genre): How many books of a genre the shop has.
tool list_books(genre): The titles of a genre.
count_books(genre='fantasy') = {'result': 3}
database: disconnected
$ uv run python -m mcp_server.ask fastmcp_server
database: connected
[10/10/26 18:43:50] INFO     Starting MCP server 'bookshop'     transport.py:241
                             with transport 'stdio'
tool count_books(genre): How many books of a genre the shop has.
tool recommend(): The book to start with.
count_books(genre='fantasy') = {'result': 3}
database: disconnected
```

`ask.py` starts `python -m mcp_server.server` as a subprocess and talks to it over stdio, as Claude Desktop or
an IDE does with the same command in its MCP configuration. The `INFO` lines are the server's logs on stderr:
the SDK configures logging for every logger, so the startup summary of `nuke-di` shows up; FastMCP only for
its own.

## Test

```console
$ uv run pytest -q mcp_server
...                                                                      [100%]
3 passed in 0.76s
```

## What to look at

- `setup(server)` comes before the tools: the SDK and FastMCP read a tool's signature when it is added.
- `count_books(genre: str, catalog: Catalog)`: `catalog` is filled by nuke-di and is not in the input schema
  (`test_clients_are_not_in_the_schema`); `Catalog` brings its `Database` along.
- In FastMCP, `recommend` gets its `title` from `Depends(bestseller)`, FastMCP's own mechanism; only the client
  argument of `bestseller` comes from nuke-di. Resources and prompts take clients too.
- The clients connect when the server's lifespan starts: once per stdio process, once per in-memory client in
  the tests, which is why `DI.override()` goes before `Client(server)`.
