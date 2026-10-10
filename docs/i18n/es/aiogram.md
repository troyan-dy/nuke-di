# <a id="aiogram"></a>aiogram

[English](../../guide/aiogram.md) · [Русский](../ru/aiogram.md) · [简体中文](../zh-CN/aiogram.md) · **Español** · [Português (Brasil)](../pt-BR/aiogram.md) · [日本語](../ja/aiogram.md) · [Polski](../pl/aiogram.md)

← [Documentación](../README.es.md#documentation)

Un handler de aiogram recibe un cliente por su type hint, junto al mensaje. Nada marca el handler: ni
`@inject`, ni `FromDishka[...]`.

```bash
pip install "nuke-di[aiogram]"
```

Requiere aiogram 3.2 o posterior. Con los clientes de los ejemplos de [FastAPI](fastapi.md):

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

Con un token de [@BotFather](https://t.me/BotFather), `BOT_TOKEN=... python -m app.bot` lo pone en marcha. Sin
token, un `Bot` cuya sesión responde dentro del propio proceso ejecuta el mismo dispatcher: aiogram no tiene
cliente de pruebas, y la sesión es adonde van las peticiones de un bot.

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

Qué pasó:

1. `setup(dp)` añadió un único inner middleware a cada evento del dispatcher, que aiogram ejecuta para los
   handlers de cada router incluido en él, y se hizo cargo del arranque y el apagado del dispatcher.
2. Al arrancar, `dp.emit_startup()`, que `start_polling()` llama primero, recorrió los handlers del
   dispatcher y de sus routers, encontró `users: UserService` en `start`, lo resolvió y conectó los
   clientes, cada uno después de sus dependencias.
3. El update coincidió con `start`, y el middleware puso el `UserService` ya conectado en los datos que
   aiogram pasa a un handler por nombre.
4. Al apagarse, los clientes se desconectaron.

El ejemplo [aiogram_bot](../../../examples/aiogram_bot/README.md) ejecuta el propio `start_polling()` contra
un Telegram falso.

## <a id="the-rules"></a>Las reglas

- **Dónde se rellenan los clientes.** En los argumentos de los handlers del dispatcher y de cada router
  incluido en él, a cualquier profundidad y tanto antes de `setup()` como después, para cada tipo de
  update: mensajes, callback queries, inline queries, error handlers y el resto. Un handler es una
  función, un método vinculado, una función envuelta con `functools.wraps` o un `def` normal, que aiogram
  ejecuta en un hilo. Un argumento es un cliente cuando su type hint es un cliente, también dentro de
  `Annotated[UserService, ...]`.
- **Handlers de arranque y apagado.** Los handlers `@dp.startup()` y `@dp.shutdown()`, y los de los
  routers, también reciben clientes, junto al `bot` y los demás argumentos que aiogram les pasa. aiogram
  les pasa a todos los mismos argumentos, así que entre ellos un nombre equivale a un cliente.
- **Por nombre, en cada handler.** aiogram le pasa a un handler los elementos de sus datos por nombre;
  nuke-di añade los argumentos de tipo cliente del handler que coincidió con el update, así que dos
  handlers pueden dar el mismo nombre a clientes distintos. Un nombre que aiogram ya pasa por su cuenta
  lanza `TypeError` al arrancar: `bot`, `state`, `event_from_user` y sus demás nombres, los workflow data
  de `Dispatcher(name=...)` o `dp["name"]`, y los argumentos con nombre de `start_polling()`.
- **Se resuelven al arrancar.** Los clientes se resuelven cuando arranca el dispatcher: `start_polling()`,
  el arranque de la app de webhook o `await dp.emit_startup()` en una prueba. Así que un `override()`
  hecho antes los reemplaza, e importar el bot no construye nada.
- **Orden de arranque y apagado.** Los clientes se conectan antes de los handlers de arranque del
  dispatcher y de sus routers, y se desconectan después de todos sus handlers de apagado, que aiogram
  llama para los routers después de los del dispatcher. Un handler de arranque que falla los vuelve a
  desconectar: `start_polling()` no llama al apagado tras un arranque fallido. Al apagarse, se activa
  `Shutdown` y se detienen las `BackgroundTasks` antes de que los clientes se desconecten, igual que en
  un worker.
- **Handlers que siguen en ejecución.** `start_polling()` procesa cada update en una tarea propia y no
  las espera al detenerse: un handler que sigue ejecutándose cuando los clientes se desconectan los
  encuentra desconectados.
- **Coste de un update.** Una búsqueda en un diccionario por el handler, y un elemento de los datos por
  cada argumento de tipo cliente: unos 0,3 µs por update, frente a los 22 µs que tarda el propio aiogram
  en despachar un mensaje (Python 3.14).
- **La función sigue siendo una función.** No se reescribe nada en ella; llamarla directamente con un
  cliente, por ejemplo en una prueba unitaria, funciona como siempre.
- **Otro contenedor.** `setup(dp, container)`. Dos dispatchers sobre un mismo contenedor se ejecutan de
  uno en uno: el segundo arranque lanza
  `RuntimeError: nuke-di clients failed to start: the container is already connected`.

## <a id="testing"></a>Pruebas

Una prueba reemplaza un cliente antes del arranque y le pasa ella misma los updates, con la sesión de
arriba:

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

`dp.feed_update()` lanza lo que haya lanzado el handler, salvo que lo gestione un error handler del
dispatcher.

## <a id="errors"></a>Errores

**Un cliente que no logra conectarse** hace fallar el arranque: `start_polling()` lanza un `RuntimeError`
simple a partir del `ConnectError` antes de su primera petición a Telegram, y los clientes que llegaron a
conectarse se desconectan.

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

**Los demás**, cada uno lanzado con este mensaje:

| Cuándo | Error |
|---|---|
| Llega un update antes del arranque | ``RuntimeError: UserService is not connected: start the dispatcher with `start_polling()`, or `await dp.emit_startup()` in a test`` |
| Después del arranque se incluye un router con un handler de un cliente que nadie había pedido | `RuntimeError: Billing was not started with the dispatcher: register its handler before the dispatcher starts` |
| Un argumento de tipo cliente con un nombre que aiogram ya pasa por su cuenta | `TypeError: Argument "state" of stateful is UserService, but aiogram passes "state" to handlers itself: rename the argument` |
| Un filtro recibe un cliente | `TypeError: Argument "db" of the filter is_known is Database: aiogram calls filters before the middlewares that fill clients, so a filter takes no clients; check it in the handler instead` |
| A `setup()` se le pasa un router | `TypeError: setup() takes the Dispatcher, not <Router '0x109bbea50'>: the routers included into it are covered` |

## <a id="not-supported"></a>No admitido

- **Los filtros** no reciben clientes, y lanzan `TypeError` al arrancar: aiogram ejecuta los filtros de un
  handler antes que sus middlewares, así que todavía no hay ningún cliente en los datos. Haz la
  comprobación en el handler.
- **Los handlers basados en clases**, subclases de `MessageHandler` y de las demás clases `BaseHandler`,
  no reciben clientes: aiogram les pasa los datos como `self.data`, y su `__init__` es el de aiogram.
- **`setup()` sobre un router.** Recibe el `Dispatcher`: un router solo se incluye en un dispatcher, algo
  que aiogram impone, y el middleware del dispatcher cubre los routers que tiene por debajo.
