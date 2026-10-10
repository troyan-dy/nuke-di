# <a id="mcp-servers"></a>Serwery MCP

[English](../../guide/mcp.md) · [Русский](../ru/mcp.md) · [简体中文](../zh-CN/mcp.md) · [Español](../es/mcp.md) · [Português (Brasil)](../pt-BR/mcp.md) · [日本語](../ja/mcp.md) · **Polski**

← [Dokumentacja](../README.pl.md#documentation)

Narzędzie MCP przyjmuje klienta po adnotacji typu, tak jak przyjmuje `Context` żądania, a klient nie trafia
do schematu wejścia, który widzi LLM. Klienci łączą się, gdy serwer startuje, i rozłączają, gdy się zatrzymuje.
Są dwa moduły, po jednym na bibliotekę: `nuke_di.mcp` dla `MCPServer` z oficjalnego SDK i `nuke_di.fastmcp`
dla FastMCP.

## <a id="the-mcp-sdk"></a>MCP SDK

```bash
pip install "nuke-di[mcp]"
```

Wymaga `mcp` 2.0 lub nowszego, którego `MCPServer` wypełnia argument narzędzia z adnotacją
`Annotated[T, Resolve(fn)]`, wywołując `fn`; `nuke_di.mcp` przekazuje mu klientów właśnie w ten sposób.
Klasa `FastMCP` z `mcp` 1.x nie ma takiego mechanizmu i nie jest obsługiwana. Księgarnia z jednym narzędziem:

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

Host MCP, taki jak Claude Desktop albo IDE, uruchamia serwer poleceniem `python -m bookshop.server` i rozmawia
z nim przez stdio. Tak samo robi ten skrypt, z klientem z SDK:

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

Schemat wejścia zawiera tylko `genre`. Linia `INFO` to podsumowanie startu `nuke-di` na stderr serwera:
SDK konfiguruje logowanie.

Zasady:

- **Gdzie wstawiani są klienci.** W argumentach narzędzi dodanych przez `@server.tool()` albo
  `server.add_tool()` po `setup(server)` oraz każdego resolvera, którego używają, `Annotated[T, Resolve(fn)]`,
  na dowolnej głębokości: funkcji, metod związanych i obiektów wywoływalnych. Argument ze zwykłą wartością
  domyślną, `catalog: Catalog = None`, też jest wypełniany. Każdy inny argument należy do SDK: wejście
  narzędzia, `Context`. Argument z własnym `Resolve(...)` należy do ciebie, nawet jeśli jego typem jest klient.
- **Tylko narzędzia.** SDK nie ma resolverów dla zasobów i promptów, więc nie przyjmują one klientów: odczytaj
  dane w narzędziu albo użyj [FastMCP](#fastmcp). Klientów nie przyjmują też `functools.partial` ani
  narzędzie zwracające `InputRequiredResult`, którego SDK nie łączy z resolverami: oba zgłaszają `TypeError`
  przy dodawaniu.
- **Którzy klienci startują.** Przy starcie — klienci każdego narzędzia dodanego przez `setup()`. Obiekt `Tool`
  zbudowany wcześniej i przekazany jako `MCPServer(tools=[...])` nie jest widoczny.
- **Lifespan.** Klienci łączą się przed własnym `lifespan=` serwera i rozłączają po nim, więc może on z nich
  korzystać. SDK uruchamia lifespan raz na proces przy stdio, raz na aplikację przy streamable HTTP i raz na
  każdego `Client` w pamięci. Przy transporcie SSE uruchamia go dla każdego połączenia, a drugie połączenie
  w tym samym czasie nie wystartuje: serwuj przez streamable HTTP. Dwa `Client(server)` w pamięci otwarte
  w tym samym czasie zawodzą tak samo.
- **Funkcja pozostaje taka, jak ją napisano.** Znaczniki `Resolve` są w jej adnotacjach tylko wtedy, gdy SDK
  je odczytuje, czyli przy dodawaniu narzędzia; test może ją wywołać z własnymi klientami.

## <a id="fastmcp"></a>FastMCP

```bash
pip install "nuke-di[fastmcp]"
```

Wymaga FastMCP 4.0 lub nowszego. FastMCP wypełnia argument, którego wartością domyślną jest `Depends(fn)`,
i pomija go w schemacie — tak samo dla narzędzi, zasobów i promptów; `nuke_di.fastmcp` daje każdemu argumentowi
z klientem taką wartość domyślną. Ta sama księgarnia, z zależnością, zasobem i promptem:

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

Resztę obsługuje własny klient FastMCP działający w pamięci:

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

Zasady:

- **Gdzie wstawiani są klienci.** W argumentach narzędzi, zasobów i promptów dodanych jako funkcje lub metody
  związane dekoratorami serwera albo jego metodami `add_tool()`, `add_resource()`, `add_prompt()` po
  `setup(mcp)` oraz każdej funkcji z `Depends(...)`, której używają, na dowolnej głębokości. Argument ze zwykłą
  wartością domyślną, `catalog: Catalog = None`, też jest wypełniany; argument z `Depends(...)` albo innym
  znacznikiem FastMCP zostaje nietknięty, nawet jeśli jego typem jest klient. Obiekt `Tool` zbudowany
  wcześniej, jak w `mcp.add_tool(Tool.from_function(fn))`, nie jest widoczny. `functools.partial`, obiekt
  wywoływalny albo klasa w `Depends(...)`, które przyjmują klientów, zgłaszają `TypeError`: zadeklaruj funkcję.
- **Sygnatura się zmienia.** FastMCP odczytuje `Depends` tylko z wartości domyślnych, więc argument z klientem
  staje się argumentem tylko nazwanym (keyword-only) z `Depends(...)` jako wartością domyślną, po argumentach
  przekazywanych przez wywołującego: `count_books(genre, *, catalog=Depends(...))`. Widzi to tylko
  introspekcja: test wywołuje funkcję tak, jak ją napisano.
- **Lifespan.** Klienci łączą się przed własnym `lifespan=` serwera i rozłączają po nim. FastMCP uruchamia
  lifespan raz, niezależnie od tego, ile sesji lub transportów współdzieli serwer.
- **Jeden serwer naraz.** FastMCP zachowuje sygnaturę funkcji na stałe, więc funkcja jest przepisywana raz,
  niezależnie od kontenera: dwa serwery, które współdzielą funkcję narzędzia, działają jeden po drugim, a drugi,
  uruchamiany, gdy działa pierwszy, nie wystartuje. Z tego samego powodu funkcja obsługuje FastMCP albo
  FastAPI, nie oba naraz.
- **Zamontowane serwery.** Wywołaj `setup()` na każdym serwerze, którego funkcje przyjmują klientów, a dopiero
  potem `mount()`. Serwer zamontowany na innym z tym samym kontenerem jest przez niego uruchamiany: klienci obu
  łączą się raz, gdy startuje serwer, który uruchamiasz. Uruchomiony samodzielnie, sam uruchamia swoich
  klientów. Serwer skonfigurowany na innym kontenerze uruchamia swoich, gdy startuje serwer, na którym jest
  zamontowany.

## <a id="testing"></a>Testowanie

Test podmienia klienta przez `override()`, zanim klient w pamięci uruchomi serwer:

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

Z FastMCP test wygląda tak samo, z `fastmcp.Client(mcp)` i `result.data == 1`. Zamiennik nigdy nie jest
łączony, dlatego `FakeDatabase` ma swoje książki już z `__init__`.

## <a id="errors"></a>Błędy

- **Narzędzie dodane przed `setup()`.** SDK i FastMCP od razu odczytują sygnaturę narzędzia, a klient nie jest
  typem, który znają:

  ```
  TypeError: Catalog is a nuke-di client, not a pydantic type. A pydantic model takes it only with arbitrary_types_allowed, and has no JSON schema for it. A framework fills it only as a plain type hint, not as an optional, through its nuke-di integration: e.g. in a FastAPI route declared through nuke_di.fastapi, or in an MCP tool added after setup() of nuke_di.mcp or nuke_di.fastmcp. See https://github.com/troyan-dy/nuke-di#documentation
  ```

- **Narzędzie wywołane bez lifespan**, np. `await server.call_tool(...)` w teście, bez klienta: SDK zgłasza
  `UnexpectedToolError: Error executing tool count_books`, a FastMCP `ToolError: Error calling tool
  'count_books': Failed to resolve dependency 'catalog' for count_books`, w obu przypadkach z przyczyną:

  ```
  RuntimeError: Catalog is not connected: run the server with its lifespan, e.g. `async with Client(server)`
  ```

- **Narzędzie dodane po starcie serwera** zgłasza `RuntimeError: Catalog was not started with the server:
  add its tool before the server starts` (FastMCP: "its tool, resource or prompt").
- **Nieudany `connect()`** przerywa start: `async with Client(server)` zgłasza `RuntimeError: nuke-di
  clients failed to start: Database.connect() raised OSError: db.local:5432 is unreachable` (klient FastMCP
  poprzedza go prefiksem `Client failed to connect: `), a serwer na stdio kończy działanie z kodem 1 i tym
  błędem w tracebacku, co host zgłasza jako serwer, który nie wystartował.
- **Dwukrotne `setup()`** dla jednego serwera: `TypeError: setup() was already called for this app`.
- **Dwa serwery FastMCP współdzielące funkcję, w tym samym czasie:** drugi nie wystartuje z `RuntimeError:
  nuke-di clients failed to start: Catalog is filled for another app that is running; apps that share a
  handler function run one at a time`.
- **Funkcja FastMCP odczytana przed `setup()`.** Przeniesienie `setup()` wyżej naprawia powyższy `TypeError`
  w następnym procesie, nie w tym, który już odczytał funkcję: FastMCP zachowuje odczytaną sygnaturę. Dodanie
  tej funkcji później w tym procesie do serwera po `setup()` zgłasza `TypeError: FastMCP read count_books
  before setup() and keeps the signature it read for good: call setup() before the function is first added to
  any server, and start the process again`.
- **Serwer zamontowany przed `setup()`** serwera, na którym jest zamontowany, z tym samym kontenerem, przerywa
  start: `RuntimeError: nuke-di clients failed to start: shelf is mounted on a server that runs on the same
  container, but not through it: call setup() of that server before mount()`.
- **Kształty, które nie przyjmują klientów,** zgłaszają `TypeError` przy dodawaniu: `Argument "catalog" of a
  functools.partial of count_books is Catalog: nuke-di fills the clients of a function, a bound method or a
  callable object, so declare a function instead`; w FastMCP `Argument "db" of the class Reader is Database:
  ...` dla klasy w `Depends(...)` i obiektu wywoływalnego; w SDK `ask takes clients and returns an
  InputRequiredResult: nuke-di fills clients through the SDK's Resolve(...), which the SDK does not combine
  with an InputRequiredResult of the tool itself; such a tool takes no clients`.
- **Jedna funkcja dla FastAPI i FastMCP:** `TypeError: count_books takes clients in both FastAPI and FastMCP
  handlers: nuke-di rewrites its signature for one framework, so give each framework its own function`.
