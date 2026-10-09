# <a id="testing"></a>Testes

[English](../../guide/testing.md) · [Русский](../ru/testing.md) · [简体中文](../zh-CN/testing.md) · [Español](../es/testing.md) · **Português (Brasil)** · [日本語](../ja/testing.md) · [Polski](../pl/testing.md)

← [Documentação](../README.pt-BR.md#documentation)

**Um cliente via container.** Registre os mocks antes de a árvore ser resolvida; a partir daí, todo consumidor
recebe o mock:

```python
from unittest.mock import call

from nuke_di import Dependencies


async def test_greet() -> None:
    deps = Dependencies()
    db = deps.mock(Database)
    db.fetch_user.return_value = "alice"

    users = deps.resolve(UserService)
    async with deps:
        assert await users.greet(1) == "Hello, alice!"

    assert db.fetch_user.await_args_list == [call(1)]
```

**Um cliente para um único bloco, com `override()`.** `override(cls, new=None)` registra um substituto
como `mock()`, mas ele vale até o fim do bloco `with`, mesmo ao longo de vários ciclos de `async with`,
e o container é limpo com `flush()` na saída, então nada que foi resolvido com ele vaza para o próximo teste.
Também funciona com o `DI` global:

```python
# test_greet.py, with Database, UserService and handler from the Quick start
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


async def test_greet_with_fake() -> None:
    with DI.override(Database, FakeDatabase()):
        injected = DI.inject(handler)
        async with DI:
            print(await injected(1))

    print("after the block:", DI.clients)


async def test_greet_with_autospec() -> None:
    with DI.override(Database) as db:  # an autospec mock by default
        db.fetch_user.return_value = "bob"
        injected = DI.inject(handler)
        async with DI:
            print(await injected(2))

    db.fetch_user.assert_awaited_once_with(2)
```

Os testes assíncronos desta seção usam [pytest-asyncio](https://pypi.org/project/pytest-asyncio/) com
`asyncio_mode = auto` no `pytest.ini`; sem isso, o pytest não executa testes `async def`.

```console
$ pytest -q -s test_greet.py
Hello, alice!
after the block: OrderedDict()
.Hello, bob!
.
2 passed in 0.01s
```

`database: connected` nunca é impresso: um substituto não é conectado.

As regras:

- **Substitua antes de resolver.** Um substituto registrado depois que `cls` foi resolvido chegaria
  só aos consumidores resolvidos depois, enquanto os anteriores continuariam com o cliente real, por isso `mock()`
  lança uma exceção:

  ```python
  DI.inject(handler)  # resolves UserService -> Database
  DI.mock(Database)  # ConnectError: Database is already resolved, call mock() before resolve() or inject()
  ```

- **`override()` parte de um container sem clientes resolvidos.** Caso contrário, o `flush()` na saída
  descartaria silenciosamente o que foi resolvido antes do bloco, por isso ele lança
  `ConnectError: override(Database) needs a container without resolved clients, found: Database, UserService`.
  Chame `DI.flush()` antes ou use a fixture `global_di`, descrita abaixo.
- **Um substituto por classe.** Chamar `mock(cls)` de novo retorna o substituto já
  registrado; `mock(cls, other)` e `override(cls)` lançam `ConnectError: Database already has a
  replacement`.
- **Substitutos não são conectados.** Os seus `connect()` / `disconnect()` nunca são chamados, e eles
  não fazem parte das [camadas](clients.md#layers).
- **Quanto tempo um substituto dura.** O de `mock()` é descartado pelo próximo `flush()`, inclusive o
  do final de `disconnect()`: um teste que conecta o container mais de uma vez deve usar
  `override()`, cujo substituto sobrevive a todos os `flush()` até o fim do bloco. Uma exceção dentro
  do bloco é propagada sem alterações; sair do bloco normalmente com o container ainda conectado
  lança `ConnectError`.
- **Aninhamento.** Blocos para classes diferentes podem ser aninhados, desde que cada um seja aberto antes de qualquer
  resolução, por exemplo `with DI.override(Database), DI.override(Clock):`; sair do bloco interno mantém o
  substituto do bloco externo.

**Fixtures do pytest.** Instalar o `nuke-di` registra um plugin do pytest com duas fixtures. Nenhuma delas é
autouse, então os testes existentes rodam exatamente como antes:

| Fixture     | Fornece                                                |
|-------------|--------------------------------------------------------|
| `di`        | Um `Dependencies` novo para um teste                   |
| `global_di` | O `DI` global, limpo com `flush()` antes e depois do teste |

Um teste que deixa o container conectado recebe um erro no teardown, e o container é limpo
mesmo assim, então o próximo teste começa do zero:

```python
# test_users.py, with Database, UserService and handler from the Quick start
from nuke_di import Dependencies


async def test_greet(di: Dependencies) -> None:
    di.mock(Database).fetch_user.return_value = "alice"
    users = di.resolve(UserService)
    async with di:
        assert await users.greet(1) == "Hello, alice!"


async def test_handler(global_di: Dependencies) -> None:  # e.g. code that calls DI.inject()
    global_di.mock(Database).fetch_user.return_value = "bob"
    injected = global_di.inject(handler)
    async with global_di:
        assert await injected(2) == "Hello, bob!"


async def test_forgets_to_disconnect(di: Dependencies) -> None:
    di.resolve(UserService)
    await di.connect()
```

```console
$ pytest -q test_users.py
...E                                                                     [100%]
==================================== ERRORS ====================================
_______________ ERROR at teardown of test_forgets_to_disconnect ________________
the test left the container of the "di" fixture connected; its clients were not disconnected, use `async with` or call disconnect()
----------------------------- Captured stdout call -----------------------------
database: connected
=========================== short test summary info ============================
ERROR test_users.py::test_forgets_to_disconnect - Failed: the test left the c...
3 passed, 1 error in 0.01s
```

As fixtures não conseguem desconectar sozinhas um container esquecido: no momento do teardown, o event loop do
teste pode já estar fechado. `global_di` só protege os testes que a solicitam: um teste que usa o `DI`
global sem ela ainda pode deixar clientes para trás para o próximo. Um projeto que define a sua própria
fixture `di` continua usando a dele, já que uma fixture do `conftest.py` tem prioridade sobre a de um plugin;
`pytest -p no:nuke_di` desativa o plugin.

**Um job, diretamente.** Importar o módulo não executa o job, então chame a função com
mocks e parâmetros:

```python
async def test_sync_copies_requested_tables() -> None:
    pg, warehouse = AsyncMock(), AsyncMock()
    warehouse.changes.return_value = ["row"]

    await sync(pg, warehouse, day=datetime.date(2026, 10, 1), tables=["users"])

    pg.upsert.assert_awaited_once_with("users", ["row"])
```

**Um job via container**, com os clientes ligados como em produção:

```python
async def test_sync_with_container() -> None:
    deps = Dependencies()
    pg = deps.mock(Postgres)  # mocks first: resolve() and inject() reuse them
    warehouse = deps.mock(Warehouse)
    warehouse.changes.return_value = ["row"]
    injected = deps.inject(sync)

    async with deps:
        await injected(day=datetime.date(2026, 10, 1), tables=["users"])

    assert pg.upsert.await_args_list == [call("users", ["row"])]
```

**Todo entrypoint resolve.** Importar um módulo não executa seu job ou worker, e `inject()` constrói a
árvore sem conectar nada, então um único teste verifica a fiação de todos os entrypoints no CI: um ciclo,
um argumento sem anotação de tipo, um argumento obrigatório que não é cliente ou um `__init__` que lança
exceção o fazem falhar com o mesmo erro que uma execução real imprimiria, e nenhum banco de dados é
necessário:

```python
# test_wiring.py
from collections.abc import Callable

import pytest

from nuke_di import Dependencies

from app.jobs import sync
from app.workers import consumer


@pytest.mark.parametrize("entrypoint", [sync.sync, consumer.consumer])
def test_entrypoint_resolves(entrypoint: Callable[..., object]) -> None:
    Dependencies().inject(entrypoint)  # runs every __init__, connects nothing
```

```console
$ pytest -q test_wiring.py
..                                                                       [100%]
2 passed in 0.05s
```

Guarde o container para obter [o grafo](clients.md#the-graph) de um entrypoint para o README dele:
`deps = Dependencies(); deps.inject(sync.sync); print(deps.graph().to_mermaid())`.

**Um worker.** `Shutdown.set()` faz o mesmo que o SIGTERM faria:

```python
async def test_consumer_stops_on_shutdown() -> None:
    queue, shutdown = AsyncMock(), Shutdown()

    async def last_message() -> str:
        shutdown.set()  # what SIGTERM would do
        return "message-1"

    queue.get.side_effect = last_message

    await consumer(queue, shutdown)

    queue.get.assert_awaited_once()
```
