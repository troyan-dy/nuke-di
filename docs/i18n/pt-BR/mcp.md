# <a id="mcp-servers"></a>Servidores MCP

[English](../../guide/mcp.md) · [Русский](../ru/mcp.md) · [简体中文](../zh-CN/mcp.md) · [Español](../es/mcp.md) · **Português (Brasil)** · [日本語](../ja/mcp.md) · [Polski](../pl/mcp.md)

← [Documentação](../README.pt-BR.md#documentation)

Uma tool MCP recebe um cliente pelo type hint, do mesmo jeito que recebe o `Context` da requisição, e o cliente
fica de fora do input schema que o LLM vê. Os clientes se conectam quando o servidor inicia e se desconectam
quando ele para. São dois módulos, um por biblioteca: `nuke_di.mcp` para o `MCPServer` do SDK oficial e
`nuke_di.fastmcp` para o FastMCP.

## <a id="the-mcp-sdk"></a>O SDK do MCP

```bash
pip install "nuke-di[mcp]"
```

Requer `mcp` 2.0 ou mais recente, cujo `MCPServer` preenche um argumento de tool anotado como
`Annotated[T, Resolve(fn)]` chamando `fn`; é assim que o `nuke_di.mcp` entrega os clientes. A classe `FastMCP`
do `mcp` 1.x não tem esse mecanismo e não é suportada. Uma livraria com uma tool:

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

Um host MCP, como o Claude Desktop ou uma IDE, inicia o servidor com `python -m bookshop.server` e conversa
com ele via stdio. Este script faz o mesmo, com o client do SDK:

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

O input schema tem só `genre`. A linha `INFO` é o resumo de inicialização do `nuke-di` no stderr do servidor:
o SDK configura o logging.

As regras:

- **Onde os clientes são preenchidos.** Nos argumentos das tools adicionadas com `@server.tool()` ou
  `server.add_tool()` depois de `setup(server)`, e de todo resolver que elas usam, `Annotated[T, Resolve(fn)]`,
  em qualquer profundidade. Todos os outros argumentos são do SDK: o input da tool, `Context`. Um argumento com
  um `Resolve(...)` seu é seu, mesmo que o tipo dele seja um cliente.
- **Só tools.** O SDK não tem resolvers para resources e prompts, então eles não recebem clientes: leia os
  dados em uma tool, ou use o [FastMCP](#fastmcp).
- **Quais clientes sobem.** Na inicialização, os de todas as tools adicionadas depois de `setup()`. Um objeto
  `Tool` construído antes e passado como `MCPServer(tools=[...])` não é visto.
- **Lifespan.** Os clientes se conectam antes do `lifespan=` do próprio servidor e se desconectam depois dele,
  então ele pode usá-los. O SDK roda o lifespan uma vez por processo no stdio, uma vez por aplicação no
  streamable HTTP, uma vez por `Client` em memória. No transporte SSE ele o roda a cada conexão, e uma segunda
  conexão simultânea falha ao iniciar: sirva via streamable HTTP.
- **A função continua como foi escrita.** Os marcadores `Resolve` ficam nas anotações dela só enquanto o SDK as
  lê, quando a tool é adicionada; um teste pode chamá-la com clientes próprios.

## <a id="fastmcp"></a>FastMCP

```bash
pip install "nuke-di[fastmcp]"
```

Requer FastMCP 4.0 ou mais recente. O FastMCP preenche um argumento cujo default é `Depends(fn)` e o deixa de
fora do schema, tanto em tools quanto em resources e prompts; o `nuke_di.fastmcp` dá esse default a todo
argumento de cliente. A mesma livraria, com uma dependência, um resource e um prompt:

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

O client em memória do próprio FastMCP alcança o resto:

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

As regras:

- **Onde os clientes são preenchidos.** Nos argumentos das tools, resources e prompts adicionados com os
  decoradores do servidor ou com `add_tool()`, `add_resource()`, `add_prompt()` depois de `setup(mcp)`, e de
  toda função em um `Depends(...)` que eles usam, em qualquer profundidade. Um argumento com um default próprio
  é deixado em paz, mesmo que o tipo dele seja um cliente.
- **A assinatura muda.** O FastMCP só lê `Depends` dos defaults, então um argumento de cliente vira
  keyword-only, com `Depends(...)` como default, depois dos argumentos que quem chama passa:
  `count_books(genre, *, catalog=Depends(...))`. Só a introspecção vê isso: um teste chama a função como ela
  foi escrita.
- **Lifespan.** Os clientes se conectam antes do `lifespan=` do próprio servidor e se desconectam depois dele.
  O FastMCP roda o lifespan uma única vez, não importa quantas sessões ou transportes compartilhem o servidor.
- **Um servidor por vez.** O FastMCP guarda a assinatura de uma função para sempre, então uma função é reescrita
  uma única vez, qualquer que seja o container: dois servidores que compartilham uma função de tool rodam um
  depois do outro, e o segundo que inicia enquanto o primeiro está rodando falha ao iniciar.

## <a id="testing"></a>Testes

Um teste substitui um cliente com `override()` antes que o client em memória inicie o servidor:

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

Com o FastMCP o teste é o mesmo, com `fastmcp.Client(mcp)` e `result.data == 1`. Um Replacement nunca é
conectado, e é por isso que o `FakeDatabase` recebe os livros no `__init__`.

## <a id="errors"></a>Erros

- **Uma tool adicionada antes de `setup()`.** O SDK e o FastMCP leem a assinatura da tool na hora, e o cliente
  não é um tipo que eles conheçam:

  ```
  TypeError: Catalog is a nuke-di client, not a pydantic type. A pydantic model takes it only with arbitrary_types_allowed, and has no JSON schema for it. A framework fills it only as a plain type hint, not as an optional, through its nuke-di integration: e.g. in a FastAPI route declared through nuke_di.fastapi, or in an MCP tool added after setup() of nuke_di.mcp or nuke_di.fastmcp. See https://github.com/troyan-dy/nuke-di#documentation
  ```

- **Uma tool chamada sem o lifespan**, por exemplo `await server.call_tool(...)` em um teste, sem um client:
  o SDK lança `UnexpectedToolError: Error executing tool count_books`, e o FastMCP `ToolError: Error calling tool
  'count_books': Failed to resolve dependency 'catalog' for count_books`, ambos causados por
  `RuntimeError: Catalog is not connected: run the server with its lifespan`.
- **Uma tool adicionada depois que o servidor iniciou** lança `RuntimeError: Catalog was not started with the
  server: add its tool before the server starts` (no FastMCP: "its tool, resource or prompt").
- **Um `connect()` que falha** faz a inicialização falhar: `async with Client(server)` lança `RuntimeError:
  nuke-di clients failed to start: Database.connect() raised OSError: db.local:5432 is unreachable` (o client
  do FastMCP coloca na frente `Client failed to connect: `), e um servidor no stdio sai com código 1 e esse erro
  no traceback, que o host reporta como um servidor que falhou ao iniciar.
- **`setup()` duas vezes** para o mesmo servidor: `TypeError: setup() was already called for this app`.
- **Dois servidores FastMCP que compartilham uma função, ao mesmo tempo:** o segundo falha ao iniciar com
  `RuntimeError: nuke-di clients failed to start: Catalog is filled for another app that is running; apps that
  share a handler function run one at a time`.
