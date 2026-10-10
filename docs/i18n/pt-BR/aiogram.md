# <a id="aiogram"></a>aiogram

[English](../../guide/aiogram.md) · [Русский](../ru/aiogram.md) · [简体中文](../zh-CN/aiogram.md) · [Español](../es/aiogram.md) · **Português (Brasil)** · [日本語](../ja/aiogram.md) · [Polski](../pl/aiogram.md)

← [Documentação](../README.pt-BR.md#documentation)

Um handler do aiogram recebe um cliente pelo type hint, ao lado da mensagem. Nada marca o handler: nem
`@inject`, nem `FromDishka[...]`.

```bash
pip install "nuke-di[aiogram]"
```

Requer aiogram 3.2 ou mais recente. Com os clientes dos exemplos de [FastAPI](fastapi.md):

```python
# app/bot.py
import asyncio
import os

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.clients import UserService
from nuke_di.aiogram import setup

router = Router()


@router.message(CommandStart())
async def start(message: Message, users: UserService) -> None:
    await message.answer(await users.greet(message.chat.id))


dp = Dispatcher()
dp.include_router(router)
setup(dp)  # clients connect before the startup handlers, disconnect after the shutdown handlers


async def main() -> None:
    await dp.start_polling(Bot(os.environ["BOT_TOKEN"]))


if __name__ == "__main__":
    asyncio.run(main())
```

Com um token do [@BotFather](https://t.me/BotFather), `BOT_TOKEN=... python -m app.bot` coloca o bot para
rodar. Sem token, um `Bot` cuja sessão responde dentro do próprio processo roda o mesmo dispatcher: o aiogram
não tem um test client, e a sessão é para onde vão as requisições de um bot.

```python
# app/telegram.py
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage, TelegramMethod
from aiogram.types import Update


class PrintingSession(BaseSession):
    """
    The Bot API of a test: prints what the bot sends, and keeps it in `sent`, instead of calling Telegram.
    """

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[str] = []

    async def make_request(self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None) -> Any:
        assert isinstance(method, SendMessage), method
        print(f"bot -> chat {method.chat_id}: {method.text}")
        self.sent.append(method.text)
        return True

    async def stream_content(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def close(self) -> None:
        pass


def message(bot: Bot, text: str, chat_id: int = 42) -> Update:
    """
    An update with a message in a private chat, as Telegram sends it.
    """
    chat = {"id": chat_id, "type": "private"}
    return Update.model_validate(
        {"update_id": 1, "message": {"message_id": 1, "date": 0, "chat": chat, "text": text}},
        context={"bot": bot},
    )
```

```python
# app/try_bot.py
import asyncio

from aiogram import Bot

from app.bot import dp
from app.telegram import PrintingSession, message


async def main() -> None:
    bot = Bot("42:TEST", session=PrintingSession())
    await dp.emit_startup(bot=bot)  # what start_polling() does first
    try:
        await dp.feed_update(bot, message(bot, "/start"))  # and then with every update
    finally:
        await dp.emit_shutdown(bot=bot)


asyncio.run(main())
```

```console
$ python -m app.try_bot
database: connected
bot -> chat 42: Hello, user-42!
database: disconnected
```

O que aconteceu:

1. `setup(dp)` adicionou um inner middleware a cada evento do dispatcher, que o aiogram executa para os
   handlers de todo router incluído nele, e assumiu a inicialização e o desligamento do dispatcher.
2. Na inicialização, `dp.emit_startup()`, que o `start_polling()` chama primeiro, percorreu os handlers do
   dispatcher e dos routers dele, encontrou `users: UserService` em `start`, resolveu o cliente e conectou os
   clientes, cada um depois das próprias dependências.
3. O update casou com `start`, e o middleware colocou o `UserService` já conectado nos dados que o aiogram
   passa a um handler pelo nome.
4. No desligamento, os clientes se desconectaram.

O exemplo [aiogram_bot](../../../examples/aiogram_bot/README.md) roda o próprio `start_polling()` contra um
Telegram falso.

## <a id="the-rules"></a>As regras

- **Onde os clientes são preenchidos.** Nos argumentos dos handlers do dispatcher e de todo router incluído
  nele, em qualquer profundidade, antes ou depois de `setup()`, para todo tipo de update: mensagens,
  callback queries, inline queries, error handlers e os demais. Um handler pode ser uma função, um método
  vinculado, uma função envolvida com `functools.wraps`, um `functools.partial`, sem os argumentos que ele
  fixa, um objeto chamável ou um `def` comum, que o aiogram executa em uma thread. Um argumento é um cliente
  quando o type hint dele é um cliente, inclusive dentro de `Annotated[UserService, ...]`; um hint que não pode
  ser avaliado, por exemplo um nome importado sob `TYPE_CHECKING`, fica com o aiogram, e os outros argumentos
  continuam sendo preenchidos.
- **Handlers de inicialização e de desligamento.** Os handlers `@dp.startup()` e `@dp.shutdown()`, e os dos
  routers, também recebem clientes, ao lado do `bot` e dos outros argumentos que o aiogram passa a eles. O
  aiogram passa os mesmos argumentos a todos eles, então, entre eles, um nome significa um cliente, e um
  argumento nomeado de `dp.emit_shutdown()` com esse nome lança `TypeError`.
- **Por nome, por handler.** O aiogram passa a um handler os itens dos seus dados pelo nome; o nuke-di
  adiciona os argumentos de cliente do handler que casou com o update, então dois handlers podem dar o mesmo
  nome a clientes diferentes. Um nome que o próprio aiogram passa lança `TypeError` na inicialização: `bot`,
  `state`, `event_from_user` e os outros nomes dele, os workflow data de `Dispatcher(name=...)` ou `dp["name"]`,
  e os argumentos nomeados de `start_polling()`. Uma chave que entra nos dados de um update sob o nome de um
  cliente, vinda de um filtro que a retorna, de um middleware ou de `dp.feed_update(..., name=...)`, lança
  `TypeError` nesse update em vez de ser substituída. O handler recebe uma cópia dos dados, então o próximo
  depois de um `SkipHandler` não vê os clientes do que foi pulado.
- **Resolvidos na inicialização.** Os clientes são resolvidos quando o dispatcher inicia: `start_polling()`, a
  inicialização da aplicação de webhook ou `await dp.emit_startup()` em um teste. Assim, um `override()` feito
  antes disso os substitui, e importar o bot não constrói nada. Um handler de um router incluído depois da
  inicialização recebe os clientes que o container conectou, para outros handlers ou como dependências deles.
- **Ordem de inicialização e de desligamento.** Os clientes se conectam antes dos handlers de inicialização do
  dispatcher e dos routers dele, e se desconectam depois de todos os handlers de desligamento, que o aiogram
  chama para os routers depois dos do dispatcher. Um handler de inicialização que falha os desconecta de novo:
  o `start_polling()` não chama o desligamento depois de uma inicialização que falhou. Um desligamento depois
  de uma inicialização que falhou, que a aplicação de webhook chama na limpeza, ou um segundo desligamento
  executa os handlers de desligamento só com os argumentos da chamada. No desligamento, `Shutdown` é acionado
  e as `BackgroundTasks` são paradas antes que os clientes se desconectem, como em um worker.
- **Handlers ainda em execução.** O `start_polling()` processa cada update em uma task, assim como o
  `SimpleRequestHandler` do webhook com `handle_in_background=True`, o padrão dele; nenhum dos dois espera
  por elas quando para, então um handler que ainda está rodando quando os clientes se desconectam os
  encontra desconectados.
- **Custo de um update.** Uma busca em dicionário pelo handler e, para um handler com clientes, uma cópia dos
  dados com um item por argumento de cliente: menos de 1 µs por update, contra 22 µs do próprio dispatch de
  uma mensagem pelo aiogram (Python 3.14).
- **A função continua sendo uma função.** Nada nela é reescrito; chamá-la diretamente com um cliente, por
  exemplo em um teste unitário, funciona como antes.
- **Outro container.** `setup(dp, container)`. Dois dispatchers sobre o mesmo container rodam um de cada vez: a
  segunda inicialização lança `RuntimeError: nuke-di clients failed to start: the container is already
  connected`.

## <a id="your-own-middleware"></a>Seu próprio middleware

O nuke-di coloca os clientes nos dados a partir do seu próprio inner middleware, só para o handler, então os
outer middlewares do bot, e os inner registrados antes de `setup()`, não os encontram ali. Em vez disso, um
middleware recebe o seu cliente de um handler de inicialização:

```python
# app/audit.py
import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.types import TelegramObject

from app.bot import dp
from app.clients import Database
from app.telegram import PrintingSession, message


class Audit(BaseMiddleware):
    """
    A middleware of the bot's own: it gets its client from a startup handler.
    """

    def __init__(self) -> None:
        self.db: Database | None = None

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        assert self.db is not None, "the dispatcher has not started"
        print(f"audit: an update from {await self.db.fetch_user(data['event_chat'].id)}")
        return await handler(event, data)


audit = Audit()
dp.update.outer_middleware(audit)


@dp.startup()
async def connect_audit(db: Database) -> None:
    audit.db = db  # connected already: the clients connect before the startup handlers


async def main() -> None:
    bot = Bot("42:TEST", session=PrintingSession())
    await dp.emit_startup(bot=bot)
    try:
        await dp.feed_update(bot, message(bot, "/start"))
    finally:
        await dp.emit_shutdown(bot=bot)


if __name__ == "__main__":
    asyncio.run(main())
```

```console
$ python -m app.audit
database: connected
audit: an update from user-42
bot -> chat 42: Hello, user-42!
database: disconnected
```

## <a id="testing"></a>Testes

Um teste substitui um cliente antes da inicialização e ele mesmo entrega os updates, com a sessão acima:

```python
# tests/test_bot.py
from aiogram import Bot

from app.bot import dp
from app.clients import Database
from app.telegram import PrintingSession, message
from nuke_di import DI


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


async def test_start() -> None:
    session = PrintingSession()
    bot = Bot("42:TEST", session=session)
    with DI.override(Database, FakeDatabase()):
        await dp.emit_startup(bot=bot)
        try:
            await dp.feed_update(bot, message(bot, "/start"))
        finally:
            await dp.emit_shutdown(bot=bot)

    assert session.sent == ["Hello, alice!"]
```

```console
$ pytest -q tests/test_bot.py
.                                                                        [100%]
1 passed in 1.35s
```

`dp.feed_update()` lança o que o handler lançou, a menos que um error handler do dispatcher trate o erro.

## <a id="errors"></a>Erros

**Um cliente que falha ao se conectar** faz a inicialização falhar: o `start_polling()` lança um `RuntimeError`
simples a partir do `ConnectError` antes da sua primeira requisição ao Telegram, e os clientes que já tinham se
conectado são desconectados.

```python
# app/broken.py
import asyncio

from aiogram import Bot, Dispatcher
from aiogram.types import Message

from nuke_di import Client
from nuke_di.aiogram import setup


class Kafka(Client):
    async def connect(self) -> None:
        raise OSError("broker kafka-1:9092 is unreachable")


dp = Dispatcher()
setup(dp)


@dp.message()
async def publish(message: Message, kafka: Kafka) -> None: ...


if __name__ == "__main__":
    # The startup fails before the first request to Telegram, so any token of the right shape shows it
    asyncio.run(dp.start_polling(Bot("42:TEST")))
```

```console
$ python -m app.broken
Kafka.connect() raised OSError: broker kafka-1:9092 is unreachable
Traceback (most recent call last):
  ...
OSError: broker kafka-1:9092 is unreachable
Traceback (most recent call last):
  ...
nuke_di.errors.ConnectError: Kafka.connect() raised OSError: broker kafka-1:9092 is unreachable

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  ...
RuntimeError: nuke-di clients failed to start: Kafka.connect() raised OSError: broker kafka-1:9092 is unreachable
$ echo $?
1
```

**Os demais**, cada um lançado com esta mensagem:

| Quando | Erro |
|---|---|
| Um update chega antes da inicialização | ``RuntimeError: UserService is not connected: start the dispatcher with `start_polling()`, or `await dp.emit_startup()` in a test`` |
| Um router com um handler de um cliente que ninguém mais pediu é incluído depois da inicialização | `RuntimeError: Billing was not started with the dispatcher: register its handler before the dispatcher starts` |
| Um argumento de cliente sob um nome que o próprio aiogram passa | `TypeError: Argument "state" of stateful is UserService, but aiogram passes "state" to handlers itself: rename the argument` |
| Um filtro recebe um cliente | `TypeError: Argument "db" of the filter is_known is Database: aiogram calls filters before the middlewares that fill clients, so a filter takes no clients; check it in the handler instead` |
| Um filtro, um middleware ou `feed_update()` coloca o nome de um cliente nos dados | `TypeError: "users" is in the data of the update already, and start takes the client UserService under that name: a filter, a middleware or feed_update() passed it; rename the argument or the key` |
| Uma cena recebe um cliente | ``TypeError: Argument "db" of the scene handler Quiz.ask is Database: aiogram calls the handlers of a scene from its own machinery, so a scene takes no clients; pass what it needs from a handler outside it, e.g. `await scenes.enter(Quiz, ...)` `` |
| `dp.emit_shutdown()` recebe o nome de um cliente | `TypeError: "users" is passed to emit_shutdown(), but it is the client UserService of a startup or shutdown handler: rename the argument or the key` |
| `setup()` recebe um router | `TypeError: setup() takes the Dispatcher, not <Router '0x109bbea50'>: the routers included into it are covered` |

## <a id="not-supported"></a>Não suportado

- **Filtros** não recebem clientes, nem os de um handler nem os de um observer, `router.message.filter(...)`,
  e lançam `TypeError` na inicialização: o aiogram os executa antes dos seus middlewares, então ainda não há
  nenhum cliente nos dados. Faça a verificação no handler.
- **Cenas** (`aiogram.fsm.scene`) não recebem clientes e lançam `TypeError` na inicialização: o aiogram chama
  os handlers das ações de uma cena, como `on.message.enter()`, a partir do seu próprio mecanismo, fora do
  alcance do middleware. Passe o que a cena precisa a partir de um handler fora dela,
  `await scenes.enter(Quiz, questions=...)`, que o aiogram entrega ao handler de entrada. Uma cena é
  reconhecida pelos seus handlers nos routers do dispatcher; uma cena só com ações não é, e falha com o
  erro de argumento ausente do próprio aiogram.
- **Handlers baseados em classe**, subclasses de `MessageHandler` e das outras classes `BaseHandler`, não
  recebem clientes: o aiogram passa os dados a eles como `self.data`, e o `__init__` deles é o do aiogram.
- **`setup()` em um router.** Ele recebe o `Dispatcher`: um router é incluído em um único dispatcher, o que o
  aiogram garante, e o middleware do dispatcher cobre os routers abaixo dele.
