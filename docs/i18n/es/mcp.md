# <a id="mcp-servers"></a>Servidores MCP

[English](../../guide/mcp.md) · [Русский](../ru/mcp.md) · [简体中文](../zh-CN/mcp.md) · **Español** · [Português (Brasil)](../pt-BR/mcp.md) · [日本語](../ja/mcp.md) · [Polski](../pl/mcp.md)

← [Documentación](../README.es.md#documentation)

Una herramienta MCP recibe un cliente por su type hint, igual que recibe el `Context` de la petición, y el
cliente queda fuera del esquema de entrada que ve el LLM. Los clientes se conectan cuando el servidor arranca
y se desconectan cuando se detiene. Hay dos módulos, uno por biblioteca: `nuke_di.mcp` para el `MCPServer` del
SDK oficial y `nuke_di.fastmcp` para FastMCP.

## <a id="the-mcp-sdk"></a>El SDK de MCP

```bash
pip install "nuke-di[mcp]"
```

Requiere `mcp` 2.0 o posterior, cuyo `MCPServer` rellena un argumento de herramienta anotado como
`Annotated[T, Resolve(fn)]` llamando a `fn`; `nuke_di.mcp` le entrega los clientes de esa manera. La clase
`FastMCP` de `mcp` 1.x no tiene un mecanismo así y no está soportada. Una librería con una sola herramienta:

```python
# bookshop/clients.py
import sys

from nuke_di import Client


class Database(Client):
    async def connect(self) -> None:
        # A server on stdio speaks MCP on stdout, so everything else goes to stderr
        print("database: connected", file=sys.stderr)
        self.books = {"fantasy": ["A Wizard of Earthsea", "The Hobbit"], "poetry": ["Leaves of Grass"]}

    async def disconnect(self) -> None:
        print("database: disconnected", file=sys.stderr)


class Catalog(Client):
    def __init__(self, db: Database) -> None:
        self.db = db

    def count(self, genre: str) -> int:
        return len(self.db.books.get(genre, []))
```

```python
# bookshop/server.py
from mcp.server.mcpserver import MCPServer

from bookshop.clients import Catalog
from nuke_di.mcp import setup

server = MCPServer("bookshop")
setup(server)  # before the tools: clients connect when the server starts, disconnect when it stops


@server.tool()
async def count_books(genre: str, catalog: Catalog) -> int:
    """How many books of a genre the shop has."""
    return catalog.count(genre)


if __name__ == "__main__":
    server.run()  # stdio
```

Un host MCP, como Claude Desktop o un IDE, arranca el servidor con `python -m bookshop.server` y se comunica
con él por stdio. Lo mismo hace este script, con el cliente del SDK:

```python
# ask.py: starts the server over stdio, as an MCP host does, and calls its tool
import asyncio
import sys

from mcp import Client, StdioServerParameters


async def main() -> None:
    server = StdioServerParameters(command=sys.executable, args=["-m", sys.argv[1]])
    async with Client(server) as client:
        for tool in (await client.list_tools()).tools:
            print(tool.name, tool.input_schema["properties"])
        result = await client.call_tool("count_books", {"genre": "fantasy"})
        print(result.structured_content)


asyncio.run(main())
```

```console
$ python ask.py bookshop.server
database: connected
[10/10/26 18:46:46] INFO     Connected 2 clients in 0.00s (slowest:  core.py:177
                             Database 0.00s, Catalog 0.00s)
count_books {'genre': {'title': 'Genre', 'type': 'string'}}
{'result': 2}
database: disconnected
```

El esquema de entrada solo tiene `genre`. La línea `INFO` es el resumen de arranque de `nuke-di` en el stderr
del servidor: el SDK configura el logging.

Las reglas:

- **Dónde se rellenan los clientes.** En los argumentos de las herramientas añadidas con `@server.tool()` o
  `server.add_tool()` después de `setup(server)`, y de cada resolver que usen, `Annotated[T, Resolve(fn)]`, a
  cualquier profundidad: funciones, métodos ligados y objetos invocables. Un argumento con un valor por
  defecto simple, `catalog: Catalog = None`, también se rellena. Cualquier otro argumento es del SDK: la
  entrada de la herramienta, `Context`. Un argumento con un `Resolve(...)` propio es tuyo, aunque su tipo sea
  un cliente.
- **Solo herramientas.** El SDK no tiene resolvers para resources y prompts, así que estos no reciben
  clientes: lee los datos en una herramienta, o usa [FastMCP](#fastmcp). Tampoco reciben clientes un
  `functools.partial` ni una herramienta que devuelve un `InputRequiredResult`, que el SDK no combina con
  resolvers: ambos lanzan un `TypeError` al añadirse.
- **Qué clientes arrancan.** Al arrancar, los de cada herramienta añadida a través de `setup()`. Un objeto
  `Tool` construido antes y pasado como `MCPServer(tools=[...])` no se ve.
- **Lifespan.** Los clientes se conectan antes del `lifespan=` propio del servidor y se desconectan después
  de él, así que este puede usarlos. El SDK ejecuta el lifespan una vez por proceso en stdio, una vez por app
  en streamable HTTP y una vez por `Client` en memoria. En el transporte SSE lo ejecuta en cada conexión, y
  una segunda conexión simultánea no logra arrancar: sirve por streamable HTTP. Dos `Client(server)` en
  memoria abiertos a la vez fallan del mismo modo.
- **La función queda tal como se escribió.** Los marcadores `Resolve` están en sus anotaciones solo mientras
  el SDK las lee, al añadir la herramienta; una prueba puede llamarla con sus propios clientes.

## <a id="fastmcp"></a>FastMCP

```bash
pip install "nuke-di[fastmcp]"
```

Requiere FastMCP 4.0 o posterior. FastMCP rellena un argumento cuyo valor por defecto es `Depends(fn)` y lo
deja fuera del esquema, tanto en herramientas como en resources y prompts; `nuke_di.fastmcp` da a cada
argumento de tipo cliente un valor por defecto así. La misma librería, con una dependencia, un resource y un
prompt:

```python
# bookshop/fastmcp_server.py
from fastmcp import FastMCP
from fastmcp.dependencies import Depends

from bookshop.clients import Catalog, Database
from nuke_di.fastmcp import setup

mcp = FastMCP("bookshop")
setup(mcp)  # before the tools: clients connect when the server starts, disconnect when it stops


async def bestseller(db: Database) -> str:
    return db.books["fantasy"][0]


@mcp.tool
async def count_books(genre: str, catalog: Catalog) -> int:
    """How many books of a genre the shop has."""
    return catalog.count(genre)


@mcp.tool
async def recommend(title: str = Depends(bestseller)) -> str:
    """The book to start with."""
    return f"Start with {title}."


@mcp.resource("books://{genre}")
async def shelf(genre: str, db: Database) -> list[str]:
    return db.books[genre]


@mcp.prompt
async def review(genre: str, catalog: Catalog) -> str:
    return f"Write a short review of our {catalog.count(genre)} {genre} books."


if __name__ == "__main__":
    mcp.run(show_banner=False)  # stdio
```

```console
$ python ask.py bookshop.fastmcp_server
database: connected
[10/10/26 18:46:47] INFO     Starting MCP server 'bookshop'     transport.py:241
                             with transport 'stdio'
count_books {'genre': {'type': 'string'}}
recommend {}
{'result': 2}
database: disconnected
```

El cliente en memoria propio de FastMCP llega al resto:

```python
# browse.py: the tool with a dependency, the resource and the prompt, through FastMCP's in-memory client
import asyncio

from fastmcp import Client

from bookshop.fastmcp_server import mcp


async def main() -> None:
    async with Client(mcp) as client:
        print((await client.call_tool("recommend", {})).data)
        (shelf,) = await client.read_resource("books://poetry")
        print(shelf.text)
        (message,) = (await client.get_prompt("review", {"genre": "fantasy"})).messages
        print(message.content.text)


asyncio.run(main())
```

```console
$ python browse.py
database: connected
Start with A Wizard of Earthsea.
["Leaves of Grass"]
Write a short review of our 2 fantasy books.
database: disconnected
```

Las reglas:

- **Dónde se rellenan los clientes.** En los argumentos de las herramientas, resources y prompts añadidos como
  funciones o métodos ligados con los decoradores del servidor o con sus `add_tool()`, `add_resource()`,
  `add_prompt()` después de `setup(mcp)`, y de cada función en un `Depends(...)` que usen, a cualquier
  profundidad. Un argumento con un valor por defecto simple, `catalog: Catalog = None`, también se rellena;
  uno con un `Depends(...)` u otro marcador de FastMCP se deja como está, aunque su tipo sea un cliente. Un
  objeto `Tool` construido de antemano, como en `mcp.add_tool(Tool.from_function(fn))`, no se ve. Un
  `functools.partial`, un objeto invocable o una clase en `Depends(...)` que recibe clientes lanza un
  `TypeError`: declara una función.
- **La firma cambia.** FastMCP solo lee `Depends` de los valores por defecto, así que un argumento de tipo
  cliente pasa a ser keyword-only, con `Depends(...)` como valor por defecto, después de los argumentos que
  pasa quien llama: `count_books(genre, *, catalog=Depends(...))`. Solo la introspección lo ve: una prueba
  llama a la función tal como se escribió.
- **Lifespan.** Los clientes se conectan antes del `lifespan=` propio del servidor y se desconectan después de
  él. FastMCP ejecuta el lifespan una sola vez, por muchas sesiones o transportes que compartan el servidor.
- **Un servidor a la vez.** FastMCP conserva la firma de una función para siempre, así que una función se
  reescribe una sola vez, sea cual sea el contenedor: dos servidores que comparten una función de herramienta
  se ejecutan uno tras otro, y el segundo que arranca mientras corre el primero no logra arrancar. Por la
  misma razón, una función sirve a FastMCP o a FastAPI, no a ambos.
- **Servidores montados.** Llama a `setup()` en cada servidor cuyas funciones reciben clientes, y a `mount()`
  después. Un servidor montado sobre otro con el mismo contenedor lo arranca este: los clientes de ambos se
  conectan una sola vez, cuando arranca el servidor que ejecutas. Arrancado por su cuenta, arranca sus
  clientes él mismo. Un servidor configurado sobre otro contenedor arranca los suyos cuando arranca el
  servidor sobre el que está montado.

## <a id="testing"></a>Pruebas

Una prueba reemplaza un cliente con `override()` antes de que el cliente en memoria arranque el servidor:

```python
# tests/test_server.py
from mcp import Client

from bookshop.clients import Database
from bookshop.server import server
from nuke_di import DI


class FakeDatabase(Database):
    def __init__(self) -> None:
        self.books = {"fantasy": ["Dune"]}


async def test_count_books() -> None:
    # An in-memory client runs the server's lifespan, which resolves and connects the clients
    with DI.override(Database, FakeDatabase()):
        async with Client(server) as client:
            result = await client.call_tool("count_books", {"genre": "fantasy"})

    assert result.structured_content == {"result": 1}
```

```console
$ pytest -q tests/test_server.py
.                                                                        [100%]
1 passed in 0.36s
```

Con FastMCP la prueba es la misma con `fastmcp.Client(mcp)`, y `result.data == 1`. Un Reemplazo nunca se
conecta, por eso `FakeDatabase` tiene sus libros desde `__init__`.

## <a id="errors"></a>Errores

- **Una herramienta añadida antes de `setup()`.** El SDK y FastMCP leen la firma de la herramienta de
  inmediato, y el cliente no es un tipo que conozcan:

  ```
  TypeError: Catalog is a nuke-di client, not a pydantic type. A pydantic model takes it only with arbitrary_types_allowed, and has no JSON schema for it. A framework fills it only as a plain type hint, not as an optional, through its nuke-di integration: e.g. in a FastAPI route declared through nuke_di.fastapi, or in an MCP tool added after setup() of nuke_di.mcp or nuke_di.fastmcp. See https://github.com/troyan-dy/nuke-di#documentation
  ```

- **Una herramienta llamada sin el lifespan**, p. ej. `await server.call_tool(...)` en una prueba, sin un
  cliente: el SDK lanza `UnexpectedToolError: Error executing tool count_books`, y FastMCP `ToolError: Error calling tool
  'count_books': Failed to resolve dependency 'catalog' for count_books`, ambos causados por:

  ```
  RuntimeError: Catalog is not connected: run the server with its lifespan, e.g. `async with Client(server)`
  ```

- **Una herramienta añadida después de que el servidor arrancó** lanza `RuntimeError: Catalog was not started with the server:
  add its tool before the server starts` (FastMCP: "its tool, resource or prompt").
- **Un `connect()` fallido** hace fallar el arranque: `async with Client(server)` lanza `RuntimeError: nuke-di
  clients failed to start: Database.connect() raised OSError: db.local:5432 is unreachable` (el cliente de
  FastMCP le antepone `Client failed to connect: `), y un servidor en stdio termina con el código 1 y ese error
  en su traceback, que el host informa como un servidor que no logró arrancar.
- **`setup()` dos veces** para un mismo servidor: `TypeError: setup() was already called for this app`.
- **Dos servidores FastMCP que comparten una función, a la vez:** el segundo no logra arrancar con `RuntimeError:
  nuke-di clients failed to start: Catalog is filled for another app that is running; apps that share a
  handler function run one at a time`.
- **Una función de FastMCP leída antes de `setup()`.** Subir `setup()` corrige el `TypeError` de arriba en el
  siguiente proceso, no en el que ya leyó la función: FastMCP conserva la firma que leyó. Añadir la función a
  un servidor configurado más tarde en ese proceso lanza `TypeError: FastMCP read count_books before setup()
  and keeps the signature it read for good: call setup() before the function is first added to any server,
  and start the process again`.
- **Un servidor montado antes del `setup()`** del servidor sobre el que se monta, con el mismo contenedor, hace
  fallar el arranque: `RuntimeError: nuke-di clients failed to start: shelf is mounted on a server that runs
  on the same container, but not through it: call setup() of that server before mount()`.
- **Las formas que no reciben clientes** lanzan un `TypeError` al añadirse: `Argument "catalog" of a
  functools.partial of count_books is Catalog: nuke-di fills the clients of a function, a bound method or a
  callable object, so declare a function instead`; en FastMCP, `Argument "db" of the class Reader is
  Database: ...` para una clase en `Depends(...)` y un objeto invocable; en el SDK, `ask takes clients and
  returns an InputRequiredResult: nuke-di fills clients through the SDK's Resolve(...), which the SDK does not
  combine with an InputRequiredResult of the tool itself; such a tool takes no clients`.
- **Una función para FastAPI y FastMCP:** `TypeError: count_books takes clients in both FastAPI and FastMCP
  handlers: nuke-di rewrites its signature for one framework, so give each framework its own function`.
