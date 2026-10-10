# MCP integrations: the SDK and FastMCP

Status: implemented.
Issue: [#67](https://github.com/troyan-dy/nuke-di/issues/67) (the SDK) and the FastMCP item of [#105](https://github.com/troyan-dy/nuke-di/issues/105).
Decision record: [ADR-0011](../adr/0011-mcp-clients-through-the-sdk-markers.md).
Terms: see [CONTEXT.md](../../CONTEXT.md). This spec uses **Client**, **Container**, **Resolution**, **Replacement**, **Override** and **Integration** as defined there.

## Problem

An MCP tool that reads a database or calls an HTTP API has the shape of a nuke-di client: connected once per server process, used by every tool call. Without an integration, `@server.tool()` on `async def count_books(genre: str, catalog: Catalog)` fails when the tool is declared, because the SDK builds a pydantic model of every argument, and the SDK's own answer is a hand-written lifespan and `ctx.request_context.lifespan_context.db` in every tool.

## Goal

```python
server = MCPServer("bookshop")
setup(server)


@server.tool()
async def count_books(genre: str, catalog: Catalog) -> int:
    return catalog.count(genre)
```

`catalog` is filled from the container and is not in the tool's input schema; the clients connect when the server's lifespan starts and disconnect when it ends; `override()` before the server starts replaces a client. The same for FastMCP with `nuke_di.fastmcp.setup(mcp)`.

## Non-goals

- **`mcp` 1.x** (`mcp.server.fastmcp.FastMCP`): it passes a tool only the arguments the LLM sent and the `Context`; filling a client there takes a wrapper around the user's function. FastMCP 3.x, which runs on `mcp` 1.x, finishes its lifespan only after the in-memory client has exited.
- **Resources and prompts on the SDK**: 2.x resolves `Resolve(...)` for tools only.
- **Per-call clients**: as everywhere, a `Client` is one per container (ADR-0006).

## Public API

Two modules, one per distribution, each installed with its extra: `nuke_di.mcp` (`pip install nuke-di[mcp]`, `mcp>=2.0`) and `nuke_di.fastmcp` (`pip install nuke-di[fastmcp]`, `fastmcp>=4.0`). One module that detected the server would import both libraries or guess by attributes, and its users import from one of them anyway. `import nuke_di` imports neither.

### `nuke_di.mcp.setup(server, container=DI)`

`server` is a `mcp.server.mcpserver.MCPServer`.

1. Wraps the lifespan of the server's low-level `Server` (`server._lowlevel_server.lifespan`), which every transport and the in-memory `Client` enter, with `wrap_lifespan()`: on entry the clients of every tool added so far are resolved and the container connects, then the server's own `lifespan=` runs; on exit the reverse. A failed startup is rolled back, flushed and raised as a `RuntimeError`. Raises `TypeError` when called twice.
2. Replaces `server.add_tool` on the instance; `@server.tool()` calls it. For the time the SDK's `add_tool` reads the function, the annotation of every client argument becomes `Annotated[<hint>, Resolve(binding.get)]`, in the tool and in every resolver it reaches through `Annotated[T, Resolve(fn)]`; afterwards the annotations are as written. The SDK keeps the markers it read in the `Tool`, so a function added to two servers on two containers gets a `Binding` per server.

An argument already annotated with a `Resolve(...)` is the user's. A bound method is annotated through its function; a callable object, or a function whose annotations do not evaluate (`NameError`), is left to the SDK.

### `nuke_di.fastmcp.setup(server, container=DI)`

`server` is a `fastmcp.FastMCP`.

1. Wraps `server._lifespan`, which FastMCP enters once for every transport and client (ref-counted), with `wrap_lifespan()`.
2. Replaces `add_tool`, `add_resource` and `add_prompt` of `server.local_provider` on the instance; the server's decorators and its own `add_*` methods call them with the function. Every client argument gets `Depends(binding.get)` as its default and becomes keyword-only, sorted after the other arguments by kind, in the function and in every function of a `Depends(...)` default it uses. FastMCP reads dependencies from defaults only: a `Depends` in `Annotated[...]` is entered but its value is not passed, so the kit's `bind()` does not apply.

A function is rewritten once, whatever the container: FastMCP and uncalled-for cache a function's signature and dependency parameters for good. Every server that starts resolves the same bindings from its own container, and two servers that share a function run one at a time, as in FastStream (`per_container=False` in the kit's terms). An argument with a default of its own is the user's.

### Internals read

| Library | What | Public |
|---|---|---|
| `mcp` 2.x | `Resolve`, `MCPServer.add_tool`, `Tool.from_function` reading annotations with `get_type_hints()` when a tool is added | yes |
| `mcp` 2.x | `MCPServer._lowlevel_server.lifespan` | no; the SDK's own in-memory client reads it the same way |
| `fastmcp` 4.x | `Depends`, `FastMCP.local_provider`, `LocalProvider.add_tool` / `add_resource` / `add_prompt`, called by the decorators | yes |
| `fastmcp` 4.x | `FastMCP._lifespan` | no |

CI runs the tests on the latest releases and, in the `mcp-min` and `fastmcp-min` jobs, on `mcp` 2.0.0 and FastMCP 4.0.0.

## Testing

```python
with DI.override(Database, FakeDatabase()):
    async with Client(server) as client:
        result = await client.call_tool("count_books", {"genre": "fantasy"})
```

The in-memory `Client` of either library runs the lifespan. `server.call_tool()` without it raises the "not connected" error as the cause of the library's `ToolError`. Both modules run `nuke_di.integration.testing.check()`; the SDK's as a `DependsFramework` with `Resolve` as the marker, so the case of a resolver that takes a client runs too, and FastMCP's as a `Framework`, since the kit's dependency case writes the marker into `Annotated[...]`, which FastMCP does not fill. `tests/test_fastmcp.py` checks dependencies of its own.

## Decisions from self-grilling

| # | Question | Decision |
|---|---|---|
| 1 | A wrapper around the tool, as #67 proposed? | No: `mcp` 2.0 added `Resolve`, the SDK's own dependency marker, which fills an argument and drops it from the schema. The tool stays the user's function, and no ADR-0003 departure is needed for wrapping. |
| 2 | `__signature__` or annotations for the SDK? | Annotations: the SDK finds `Resolve` with `get_type_hints()`, which ignores `__signature__`. They are restored once the tool is added, since the `Tool` keeps what it read. |
| 3 | When are the clients found? | When a tool is added; the SDK reads it then, and a tool added before `setup()` fails at once with the `TypeError` of `NotSingletonClient`, whose message names `setup()`. |
| 4 | One module or two? | Two, named after the package the user imports from; the mechanisms differ (annotations for a time, signatures for good), and so do the extras. |
| 5 | `per_container` for FastMCP | `False`: uncalled-for keeps `_signature_cache` and `_parameter_cache` per function forever, and FastMCP `lru_cache`s the wrapper it builds, so a second rewrite would never be read. |
| 6 | Resources and prompts | FastMCP: yes, through the same `Depends`. SDK: no, it has no resolver there; a wrapper would be the only way. |
| 7 | Lowest versions | `mcp` 2.0.0, the first with `Resolve`; FastMCP 4.0.0, the first on `mcp` 2 where the decorators register through `LocalProvider.add_*` and the lifespan ends before the in-memory client exits (3.0 fails the contract's disconnect check). |
| 8 | SSE | The SDK runs the lifespan for every SSE connection, so a second connection at once fails with "the container is already connected". Documented; streamable HTTP and stdio run it once. |
