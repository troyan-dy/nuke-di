# MCP tools get clients through the markers of their own library, not a wrapper

An MCP tool declares a client by its type, `async def count_books(genre: str, catalog: Catalog)`, like a FastAPI route. Issue #67 proposed wrapping the tool in a function whose signature leaves the clients out, since the SDK passes a tool only its validated arguments. It does not have to: `mcp` 2.0 fills an argument annotated `Annotated[T, Resolve(fn)]` by calling `fn` and drops it from the input schema, and FastMCP does the same for an argument whose default is `Depends(fn)`. So `nuke_di.mcp` and `nuke_di.fastmcp` hand the clients to the library's own marker, with a `Binding.get` as the function, and the tool stays the user's function. Both connect the container with `wrap_lifespan()` around the server's lifespan, and both intercept the registration of a function on the server instance, since that is when the library reads it.

The rewrite departs from ADR-0003 in two ways. In the SDK it is in `__annotations__`, which is where the SDK looks for `Resolve`, and only for the time the SDK's `add_tool` reads the function: the `Tool` keeps the markers it read, and the function is left as written. In FastMCP the marker goes into the parameter's default, the only place FastMCP reads it from, so a client argument becomes keyword-only in `__signature__`, and a function is rewritten once whatever the container, because FastMCP caches signatures for good.

## Considered Options

- **A `functools.wraps` wrapper with a narrower `__signature__`** (#67): works on `mcp` 1.x too, but the server holds a function the user did not write, with its own frame in every traceback, a second place where arguments are passed, and generators and sync functions to delegate by hand, while 2.x offers the mechanism itself.
- **Supporting `mcp` 1.x** (`mcp.server.fastmcp.FastMCP`): only through that wrapper. Not supported; 2.x and FastMCP 4 are the targets.
- **One `nuke_di.mcp` for both libraries**, detecting the server: two mechanisms behind one name, an import of the library the user does not use, and a module named after neither package.
- **The kit's `bind()`** with `Resolve` or FastMCP's `Depends` as the `DependsFramework` marker: `bind()` writes `__signature__`, which the SDK's `get_type_hints()` does not read, and `Annotated[...]`, which FastMCP enters without passing the value.
- **A nuke-di lifespan the user passes as `MCPServer(lifespan=...)`**: public API only, but it would not find the tools, and the tools would still fail when declared.

## Consequences

- `setup()` comes before the tools; a tool declared before it fails when declared with the `TypeError` of `NotSingletonClient`, whose message names `setup()`. Tools built as `Tool` objects beforehand are not seen.
- The SDK has no resolvers for resources and prompts, so on the SDK only tools and their resolvers take clients; FastMCP fills tools, resources, prompts and their `Depends` functions.
- Two private attributes are read: `MCPServer._lowlevel_server.lifespan` and `FastMCP._lifespan`. CI runs the lowest supported versions, `mcp` 2.0.0 and FastMCP 4.0.0, and the latest.
- FastMCP servers that share a function run one at a time, as FastStream apps do.
