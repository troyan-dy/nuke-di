# String annotations on purpose: every handler below goes through evaluating them
from __future__ import annotations

import asyncio
import functools
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import pytest
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.session.base import BaseSession
from aiogram.filters import Command, Filter
from aiogram.handlers import MessageHandler
from aiogram.methods import GetMe, GetUpdates, SendMessage, TelegramMethod
from aiogram.types import ErrorEvent, Message, Update, User

from nuke_di import DI, BackgroundTasks, Client, Dependencies, Shutdown
from nuke_di.aiogram import _AIOGRAM, setup
from nuke_di.integration.testing import check

if TYPE_CHECKING:
    from decimal import Decimal

events: list[str] = []


@pytest.fixture(autouse=True)
def clear_events() -> Iterator[None]:
    events.clear()
    yield
    DI.flush()


class Database(Client):
    async def connect(self) -> None:
        events.append("database: connected")

    async def disconnect(self) -> None:
        events.append("database: disconnected")

    async def fetch_user(self, user_id: int) -> str:
        return f"user-{user_id}"


class UserService(Client):
    def __init__(self, db: Database) -> None:
        self.db = db

    async def greet(self, user_id: int) -> str:
        return f"Hello, {await self.db.fetch_user(user_id)}!"


class FakeDatabase(Database):
    async def fetch_user(self, user_id: int) -> str:
        return "alice"


class Billing(Client):
    pass


class Broken(Client):
    async def connect(self) -> None:
        raise OSError("unreachable")


class Offline(BaseSession):
    """
    A Bot API that is never reached: no request of these tests leaves the process.
    """

    async def make_request(self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None) -> Any:  # noqa: ASYNC109
        raise AssertionError(f"{type(method).__name__} would reach Telegram")  # pragma: no cover

    async def stream_content(self, *args: Any, **kwargs: Any) -> AsyncGenerator[bytes, None]:
        raise NotImplementedError  # pragma: no cover
        yield b""  # pragma: no cover

    async def close(self) -> None:
        events.append("session: closed")


# A token of the right shape is all a bot needs to be made
BOT = Bot("42:TEST", session=Offline())


def update(text: str = "/start", update_id: int = 1, bot: Bot | None = BOT) -> Update:
    return Update.model_validate(
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "date": 0,
                "chat": {"id": 7, "type": "private"},
                "from": {"id": 7, "is_bot": False, "first_name": "Ada"},
                "text": text,
            },
        },
        context={"bot": bot},
    )


def make_dispatcher(deps: Dependencies, *routers: Router, **kwargs: Any) -> Dispatcher:
    dp = Dispatcher(**kwargs)
    setup(dp, deps)
    for router in routers:
        dp.include_router(router)
    return dp


@asynccontextmanager
async def started(dp: Dispatcher, **kwargs: Any) -> AsyncIterator[Dispatcher]:
    # What start_polling() does around the polling
    await dp.emit_startup(bot=BOT, **kwargs)
    try:
        yield dp
    finally:
        await dp.emit_shutdown(bot=BOT, **kwargs)


async def greet(message: Message, users: UserService) -> str:
    assert message.from_user is not None
    return await users.greet(message.from_user.id)


# --- handlers --------------------------------------------------------------------------------------------


async def test_handler_gets_client_by_type_hint() -> None:
    deps = Dependencies()
    dp = make_dispatcher(deps)
    dp.message.register(greet)

    async with started(dp):
        assert events == ["database: connected"]
        assert await dp.feed_update(BOT, update()) == "Hello, user-7!"

    assert events == ["database: connected", "database: disconnected"]


async def test_override_before_startup() -> None:
    deps = Dependencies()
    dp = make_dispatcher(deps)
    dp.message.register(greet)

    with deps.override(Database, FakeDatabase()):
        async with started(dp):
            assert await dp.feed_update(BOT, update()) == "Hello, alice!"

    assert events == []


async def test_global_container_by_default() -> None:
    dp = Dispatcher()
    setup(dp)
    dp.message.register(greet)

    with DI.override(Database, FakeDatabase()):
        async with started(dp):
            assert await dp.feed_update(BOT, update()) == "Hello, alice!"


async def test_handler_stays_callable_directly() -> None:
    make_dispatcher(Dependencies()).message.register(greet)

    assert await greet(update().message, UserService(FakeDatabase())) == "Hello, alice!"  # type: ignore[arg-type]


async def test_routers_nested_before_and_after_setup() -> None:
    inner, outer, late = Router(name="inner"), Router(name="outer"), Router(name="late")
    outer.include_router(inner)
    inner.message.register(greet, Command("start"))
    dp = make_dispatcher(Dependencies(), outer)

    # Included after setup(), and a router into a router that is in the dispatcher already
    @late.message(Command("bill"))
    async def bill(message: Message, billing: Billing, users: UserService) -> str:
        return f"{type(billing).__name__} {await users.greet(1)}"

    inner.include_router(late)

    async with started(dp):
        assert await dp.feed_update(BOT, update("/start")) == "Hello, user-7!"
        assert await dp.feed_update(BOT, update("/bill")) == "Billing Hello, user-1!"


async def test_filters_pass_their_data_next_to_clients() -> None:
    dp = make_dispatcher(Dependencies())

    @dp.message(Command("echo"), F.text.as_("text"))
    async def echo(message: Message, text: str, users: UserService) -> str:
        return f"{text} {await users.greet(2)}"

    async with started(dp):
        assert await dp.feed_update(BOT, update("/echo")) == "/echo Hello, user-2!"


async def test_handlers_without_clients_and_other_events() -> None:
    dp = make_dispatcher(Dependencies())

    @dp.edited_message()
    @dp.message()
    async def plain(message: Message) -> str:
        return "plain"

    raw = update().model_dump(exclude_none=True)
    edited = Update.model_validate(
        {"update_id": 2, "edited_message": {**raw["message"], "edit_date": 1}}, context={"bot": BOT}
    )
    async with started(dp):
        assert await dp.feed_update(BOT, update()) == "plain"
        assert await dp.feed_update(BOT, edited) == "plain"
    assert events == []


async def test_sync_handler_and_one_client_under_two_names() -> None:
    dp = make_dispatcher(Dependencies())

    @dp.message()
    def same(message: Message, users: UserService, service: UserService, billing: Billing) -> bool:
        return users is service

    async with started(dp):
        assert await dp.feed_update(BOT, update()) is True


async def test_wrapped_handler_and_bound_method() -> None:
    dp = make_dispatcher(Dependencies())

    def logged(handler: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(handler)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            events.append("logged")
            return await handler(*args, **kwargs)

        return wrapper

    class Commands:
        async def bill(self, message: Message, billing: Billing) -> str:
            return type(billing).__name__

    dp.message.register(logged(greet), Command("start"))
    dp.message.register(Commands().bill, Command("bill"))

    async with started(dp):
        assert await dp.feed_update(BOT, update("/start")) == "Hello, user-7!"
        assert await dp.feed_update(BOT, update("/bill")) == "Billing"
    assert events == ["database: connected", "logged", "database: disconnected"]


async def test_class_based_handler_is_left_to_aiogram() -> None:
    dp = make_dispatcher(Dependencies())

    @dp.message()
    class Hello(MessageHandler):
        async def handle(self) -> str:
            return f"hello {self.from_user.first_name if self.from_user else ''}"

    async with started(dp):
        assert await dp.feed_update(BOT, update()) == "hello Ada"


async def test_error_handler_gets_clients() -> None:
    dp = make_dispatcher(Dependencies())

    @dp.message()
    async def fail(message: Message) -> None:
        raise ValueError("boom")

    @dp.error()
    async def on_error(event: ErrorEvent, users: UserService) -> str:
        return f"{event.exception} {await users.greet(3)}"

    async with started(dp):
        assert await dp.feed_update(BOT, update()) == "boom Hello, user-3!"


async def unevaluable(message: Message, amount: Decimal, users: UserService) -> str:
    return "unevaluable"  # pragma: no cover


async def test_handler_with_unevaluable_hints_is_left_to_aiogram() -> None:
    dp = make_dispatcher(Dependencies())
    dp.message.register(unevaluable)

    async with started(dp):
        # aiogram passes by name and found no `amount` nor `users`
        with pytest.raises(TypeError, match=r"missing 2 required positional arguments: 'amount' and 'users'"):
            await dp.feed_update(BOT, update())


# --- startup and shutdown --------------------------------------------------------------------------------


async def test_startup_and_shutdown_handlers_run_inside_connected_clients() -> None:
    deps = Dependencies()
    child = Router()
    dp = make_dispatcher(deps, child)
    dp.message.register(greet)

    @dp.startup()
    async def on_startup(bot: Bot, users: UserService) -> None:
        events.append(f"dispatcher startup: {await users.greet(bot.id)}, connected={deps.connected}")

    @child.startup()
    async def child_startup(router: Router) -> None:
        events.append(f"router startup: connected={deps.connected}")

    @dp.shutdown()
    async def on_shutdown(users: UserService) -> None:
        events.append(f"dispatcher shutdown: connected={deps.connected}")

    @child.shutdown()
    async def child_shutdown(db: Database) -> None:
        events.append(f"router shutdown: {await db.fetch_user(1)}, connected={deps.connected}")

    async with started(dp):
        pass

    assert events == [
        "database: connected",
        "dispatcher startup: Hello, user-42!, connected=True",
        "router startup: connected=True",
        "dispatcher shutdown: connected=True",
        "router shutdown: user-1, connected=True",
        "database: disconnected",
    ]


async def test_failed_startup_handler_disconnects() -> None:
    deps = Dependencies()
    dp = make_dispatcher(deps)
    dp.message.register(greet)

    @dp.startup()
    async def on_startup() -> None:
        raise ConnectionError("telegram is down")

    # start_polling() never calls the shutdown after a failed startup
    with pytest.raises(ConnectionError, match="telegram is down"):
        await dp.emit_startup(bot=BOT)

    assert events == ["database: connected", "database: disconnected"]
    assert not deps.connected


async def test_failed_shutdown_handler_still_disconnects() -> None:
    deps = Dependencies()
    dp = make_dispatcher(deps)
    dp.message.register(greet)

    @dp.shutdown()
    async def on_shutdown() -> None:
        raise ConnectionError("telegram is down")

    with pytest.raises(ConnectionError, match="telegram is down"):
        async with started(dp):
            pass

    assert events == ["database: connected", "database: disconnected"]


async def loop(message: Message, shutdown: Shutdown, tasks: BackgroundTasks) -> bool:
    return shutdown.is_set()


async def test_shutdown_is_set_and_dispatcher_restarts() -> None:
    deps = Dependencies()
    dp = make_dispatcher(deps)
    dp.message.register(loop)

    for _ in range(2):
        async with started(dp):
            assert await dp.feed_update(BOT, update()) is False
            shutdown = deps.clients[Shutdown]
        assert isinstance(shutdown, Shutdown)
        assert shutdown.is_set()
        assert not deps.connected


async def test_failed_connect_fails_startup_and_forgets_clients() -> None:
    deps = Dependencies()
    dp = make_dispatcher(deps)

    @dp.message()
    async def broken(message: Message, kafka: Broken) -> None: ...

    with pytest.raises(RuntimeError, match=r"nuke-di clients failed to start: Broken.connect\(\) raised OSError"):
        await dp.emit_startup(bot=BOT)

    assert not deps.connected
    assert deps.connect_clients == []
    with pytest.raises(RuntimeError, match=r"Broken is not connected: start the dispatcher"):
        await dp.feed_update(BOT, update())


class FakeTelegram(Offline):
    """
    The Bot API of a test: getUpdates returns `updates` once, then stops the polling; sendMessage is recorded.
    """

    def __init__(self, dp: Dispatcher, updates: list[Update]) -> None:
        super().__init__()
        self.dp = dp
        self.updates = updates
        self.sent: list[str] = []

    async def make_request(self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None) -> Any:  # noqa: ASYNC109
        if isinstance(method, GetMe):
            return User(id=bot.id, is_bot=True, first_name="Test", username="test_bot")
        if isinstance(method, SendMessage):
            self.sent.append(method.text)
            return True
        assert isinstance(method, GetUpdates)
        if self.updates:
            updates, self.updates = self.updates, []
            return updates
        # Nothing more to come: stop as Ctrl+C would, and wait to be cancelled as a long poll would
        asyncio.get_running_loop().create_task(self.dp.stop_polling())
        await asyncio.Event().wait()


async def test_start_polling_runs_the_container() -> None:
    deps = Dependencies()
    dp = make_dispatcher(deps, users_seen="workflow data")

    @dp.message()
    async def reply(message: Message, users: UserService, users_seen: str) -> None:
        assert message.from_user is not None
        await message.answer(f"{await users.greet(message.from_user.id)} ({users_seen})")

    @dp.startup()
    async def on_startup(bots: tuple[Bot, ...], dispatcher: Dispatcher, users: UserService) -> None:
        events.append(f"startup: {len(bots)} bot, {await users.greet(0)}")

    telegram = FakeTelegram(dp, [update(update_id=1, bot=None), update(update_id=2, bot=None)])
    await dp.start_polling(Bot("43:TEST", session=telegram), handle_signals=False, handle_as_tasks=False)

    assert telegram.sent == ["Hello, user-7! (workflow data)"] * 2
    assert events == [
        "database: connected",
        "startup: 1 bot, Hello, user-0!",
        "database: disconnected",
        "session: closed",
    ]


# --- two dispatchers ---------------------------------------------------------------------------------------


async def test_two_dispatchers_with_their_own_containers() -> None:
    first, second = Dependencies(), Dependencies()
    a, b = make_dispatcher(first), make_dispatcher(second)
    a.message.register(greet)
    b.message.register(greet)

    with second.override(Database, FakeDatabase()):
        async with started(a), started(b):
            assert await a.feed_update(BOT, update()) == "Hello, user-7!"
            assert await b.feed_update(BOT, update()) == "Hello, alice!"


async def test_two_dispatchers_on_one_container_run_one_at_a_time() -> None:
    deps = Dependencies()
    a, b = make_dispatcher(deps), make_dispatcher(deps)
    a.message.register(greet)
    b.message.register(greet)

    async with started(a):
        with pytest.raises(RuntimeError, match=r"the container is already connected"):
            await b.emit_startup(bot=BOT)
        assert await a.feed_update(BOT, update()) == "Hello, user-7!"


async def test_dispatcher_started_twice_keeps_its_clients() -> None:
    dp = make_dispatcher(Dependencies())
    dp.message.register(greet)

    async with started(dp):
        with pytest.raises(RuntimeError, match=r"the container is already connected"):
            await dp.emit_startup(bot=BOT)
        assert await dp.feed_update(BOT, update()) == "Hello, user-7!"


def test_one_router_in_two_dispatchers_is_refused_by_aiogram() -> None:
    router = Router()
    make_dispatcher(Dependencies(), router)

    with pytest.raises(RuntimeError, match=r"Router is already attached"):
        make_dispatcher(Dependencies(), router)


# --- what is refused or explained ------------------------------------------------------------------------


async def test_update_before_startup_explains() -> None:
    dp = make_dispatcher(Dependencies())
    dp.message.register(greet)

    with pytest.raises(RuntimeError, match=r"UserService is not connected: start the dispatcher"):
        await dp.feed_update(BOT, update())


async def test_handler_registered_after_startup_explains() -> None:
    dp = make_dispatcher(Dependencies())
    dp.message.register(greet, Command("start"))

    async with started(dp):
        late = Router()

        @late.message(Command("bill"))
        async def bill(message: Message, billing: Billing, users: UserService) -> None: ...

        dp.include_router(late)
        # A client resolved on startup for another handler is there; one nobody asked for is not
        with pytest.raises(RuntimeError, match=r"Billing was not started with the dispatcher"):
            await dp.feed_update(BOT, update("/bill"))


@pytest.mark.parametrize("name", ["state", "bot", "event_from_user"])
async def test_client_under_a_reserved_name_is_refused(name: str) -> None:
    dp = make_dispatcher(Dependencies())
    namespace: dict[str, Any] = {}
    exec(  # noqa: S102
        f"async def handler(message: Message, {name}: UserService) -> None: ...",
        {"Message": Message, "UserService": UserService},
        namespace,
    )
    dp.message.register(namespace["handler"])

    with pytest.raises(TypeError, match=rf'Argument "{name}" of handler is UserService, but aiogram passes'):
        await dp.emit_startup(bot=BOT)
    assert events == []


async def test_client_under_a_name_of_the_workflow_data_is_refused() -> None:
    dp = make_dispatcher(Dependencies(), users="from the dispatcher")
    dp.message.register(greet)

    with pytest.raises(TypeError, match=r'Argument "users" of greet is UserService, but aiogram passes "users"'):
        await dp.emit_startup(bot=BOT)

    dp = make_dispatcher(Dependencies())
    dp.message.register(greet)
    # start_polling(bot, users=...) passes its keyword arguments to the startup and to every update
    with pytest.raises(TypeError, match=r'Argument "users" of greet'):
        await dp.emit_startup(bot=BOT, users="from start_polling")


async def test_filter_with_a_client_is_refused() -> None:
    async def is_admin(message: Message, users: UserService) -> bool:
        return True  # pragma: no cover

    class Admin(Filter):
        async def __call__(self, message: Message, db: Database) -> bool:
            return True  # pragma: no cover

    dp = make_dispatcher(Dependencies())
    dp.message.register(greet, is_admin)
    with pytest.raises(TypeError, match=r'Argument "users" of the filter is_admin is UserService: aiogram calls'):
        await dp.emit_startup(bot=BOT)

    dp = make_dispatcher(Dependencies())
    dp.message.register(greet, Admin())
    with pytest.raises(TypeError, match=r'Argument "db" of the filter Admin is Database'):
        await dp.emit_startup(bot=BOT)


async def test_one_name_for_two_clients_in_startup_handlers_is_refused() -> None:
    dp = make_dispatcher(Dependencies())

    @dp.startup()
    async def first(service: UserService) -> None: ...

    @dp.shutdown()
    async def second(service: Billing) -> None: ...

    with pytest.raises(TypeError, match=r'Argument "service" of the startup and shutdown handlers is UserService and'):
        await dp.emit_startup(bot=BOT)


async def test_handlers_may_use_one_name_for_two_clients() -> None:
    dp = make_dispatcher(Dependencies())

    @dp.message(Command("a"))
    async def first(message: Message, service: UserService) -> str:
        return type(service).__name__

    @dp.message(Command("b"))
    async def second(message: Message, service: Billing) -> str:
        return type(service).__name__

    async with started(dp):
        assert await dp.feed_update(BOT, update("/a")) == "UserService"
        assert await dp.feed_update(BOT, update("/b")) == "Billing"


def test_setup_twice_and_on_a_router() -> None:
    dp = make_dispatcher(Dependencies())
    with pytest.raises(TypeError, match=r"setup\(\) was already called for this dispatcher"):
        setup(dp)
    with pytest.raises(TypeError, match=r"setup\(\) takes the Dispatcher, not <Router"):
        setup(Router())  # type: ignore[arg-type]


async def test_shutdown_without_startup_with_clients_in_shutdown_handlers_explains() -> None:
    dp = make_dispatcher(Dependencies())

    @dp.shutdown()
    async def on_shutdown(users: UserService) -> None: ...

    with pytest.raises(RuntimeError, match=r"UserService is not connected"):
        async with started(dp):
            await dp.emit_shutdown(bot=BOT)


# --- the contract ----------------------------------------------------------------------------------------


def contract_app(container: Dependencies, handler: Callable[..., Any]) -> Dispatcher:
    dp = make_dispatcher(container)

    # aiogram passes the message first; the check's handler takes nothing but clients, which aiogram reads
    # through functools.wraps
    @functools.wraps(handler)
    async def on_message(message: Message, **clients: Any) -> None:
        await handler(**clients)

    dp.message.register(on_message)
    return dp


@asynccontextmanager
async def contract_run(dp: Dispatcher, lifespan: bool) -> AsyncIterator[Callable[[], object]]:
    if lifespan:
        async with started(dp):
            yield lambda: dp.feed_update(BOT, update())
    else:
        yield lambda: dp.feed_update(BOT, update())


async def test_keeps_the_integration_contract() -> None:
    # A plain Framework: aiogram passes data by name, so the case of a dependency is skipped
    await check(_AIOGRAM, contract_app, contract_run)
