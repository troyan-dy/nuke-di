# String annotations on purpose: every task below goes through evaluating them
from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import pytest
from taskiq import AsyncBroker, BrokerMessage, InMemoryBroker, TaskiqDepends, TaskiqEvents, TaskiqState
from taskiq.receiver import Receiver

from nuke_di import DI, Client, Dependencies
from nuke_di.integration.testing import check
from nuke_di.taskiq import _TASKIQ, setup

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


class Broken(Client):
    async def connect(self) -> None:
        raise OSError("unreachable")


class QueueBroker(AsyncBroker):
    """
    A broker of a real deployment in miniature: a client process kicks into a queue, a worker process listens.
    """

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[bytes] = []

    async def kick(self, message: BrokerMessage) -> None:
        self.messages.append(message.message)

    async def listen(self) -> AsyncGenerator[bytes, None]:
        for message in self.messages:
            yield message


def make_broker(deps: Dependencies) -> InMemoryBroker:
    broker = InMemoryBroker()
    setup(broker, deps)
    return broker


@asynccontextmanager
async def started(broker: AsyncBroker) -> AsyncIterator[None]:
    # Not `async with broker`, which taskiq 0.11 lacks
    await broker.startup()
    try:
        yield
    finally:
        await broker.shutdown()


async def result(task: Any, *args: Any) -> Any:
    kicked = await task.kiq(*args)
    return (await kicked.wait_result(check_interval=0.01, timeout=5)).raise_for_error().return_value


# --- tasks -----------------------------------------------------------------------------------------------


async def send_report(user_id: int, users: UserService) -> str:
    return await users.greet(user_id)


async def test_task_gets_client_by_type_hint() -> None:
    broker = make_broker(Dependencies())
    task = broker.task(send_report)

    async with started(broker):
        events.append(await result(task, 1))

    assert events == ["database: connected", "Hello, user-1!", "database: disconnected"]


async def test_override_before_startup() -> None:
    deps = Dependencies()
    broker = make_broker(deps)
    task = broker.task(send_report)

    with deps.override(Database, FakeDatabase()):
        async with started(broker):
            events.append(await result(task, 1))

    assert events == ["Hello, alice!"]


async def test_global_container_by_default() -> None:
    broker = InMemoryBroker()
    setup(broker)
    task = broker.task(send_report)

    with DI.override(Database, FakeDatabase()):
        async with started(broker):
            events.append(await result(task, 2))

    assert events == ["Hello, alice!"]


async def report_before(user_id: int, users: UserService) -> str:
    return await users.greet(user_id)


async def report_named(user_id: int, users: UserService) -> str:
    return await users.greet(user_id)


async def report_registered(user_id: int, users: UserService) -> str:
    return await users.greet(user_id)


async def test_tasks_declared_before_and_after_setup() -> None:
    broker = InMemoryBroker()
    before = broker.task(report_before)
    setup(broker, Dependencies())
    named = broker.task("reports:named", retry_on_error=False)(report_named)
    registered = broker.register_task(report_registered, "reports:registered")

    async with started(broker):
        events.extend([await result(before, 1), await result(named, 2), await result(registered, 3)])

    assert events[1:-1] == ["Hello, user-1!", "Hello, user-2!", "Hello, user-3!"]
    assert named.task_name == "reports:named"
    assert named.labels == {"retry_on_error": False}


def sync_report(user_id: int, db: Database) -> str:
    return f"{type(db).__name__} for user-{user_id}"


async def test_sync_task_gets_client() -> None:
    # taskiq resolves the dependencies in the event loop and runs a sync task in its executor
    broker = make_broker(Dependencies())
    task = broker.task(sync_report)

    async with started(broker):
        events.append(await result(task, 3))

    assert events[1] == "Database for user-3"


async def test_task_parameters_are_still_parsed() -> None:
    broker = make_broker(Dependencies())
    task = broker.task(send_report)

    async with started(broker):
        # taskiq casts the argument to the annotation, the client argument is left to the container
        events.append(await result(task, "4"))

    assert events[1] == "Hello, user-4!"


async def report_typed(user_id: int, users: UserService = TaskiqDepends()) -> str:  # noqa: B008
    return await users.greet(user_id)


async def test_client_with_a_default_for_type_checkers() -> None:
    # taskiq types `.kiq()` with the task's signature: a default lets a type checker accept a call without the
    # client, which still comes from the container
    broker = make_broker(Dependencies())
    task = broker.task(report_typed)

    async with started(broker):
        kicked = await task.kiq(5)
        events.append((await kicked.wait_result(check_interval=0.01, timeout=5)).raise_for_error().return_value)

    assert events[1] == "Hello, user-5!"


async def test_task_stays_callable_directly() -> None:
    broker = make_broker(Dependencies())
    task = broker.task(send_report)

    assert await task(1, UserService(FakeDatabase())) == "Hello, alice!"


# --- dependencies ----------------------------------------------------------------------------------------


async def current_user(user_id: int, db: Database) -> str:
    return await db.fetch_user(user_id)


async def opened(db: Database) -> AsyncGenerator[str, None]:
    events.append("dependency: opened")
    yield "session"
    events.append("dependency: closed")


async def test_dependency_function_takes_client() -> None:
    broker = make_broker(Dependencies())

    async def report(
        user: Annotated[str, TaskiqDepends(current_user, kwargs={"user_id": 7})],
        session: str = TaskiqDepends(opened),
    ) -> str:
        return f"{user} in {session}"

    task = broker.task(report)
    async with started(broker):
        events.append(await result(task))

    assert events == [
        "database: connected",
        "dependency: opened",
        "dependency: closed",
        "user-7 in session",
        "database: disconnected",
    ]


class Auth:
    def __init__(self, db: Database) -> None:
        self.db = db


async def test_dependency_class_is_built_by_taskiq() -> None:
    # taskiq reads a dependency class from its own __init__, which nuke-di does not rewrite
    broker = make_broker(Dependencies())

    async def audit(auth: Annotated[Auth, TaskiqDepends()]) -> None: ...  # pragma: no cover

    task = broker.task(audit)
    async with started(broker):
        with pytest.raises(TypeError, match="missing 1 required positional argument: 'db'"):
            await result(task)


# --- lifecycle -------------------------------------------------------------------------------------------


async def test_not_connected_without_startup() -> None:
    broker = make_broker(Dependencies())
    task = broker.task(send_report)

    with pytest.raises(RuntimeError, match=r"UserService is not connected: the clients connect when the worker"):
        await result(task, 1)

    assert events == []


async def test_task_declared_after_startup_was_not_started() -> None:
    broker = make_broker(Dependencies())

    async with started(broker):

        async def late(users: UserService) -> None: ...  # pragma: no cover

        task = broker.task(late)
        with pytest.raises(RuntimeError, match="UserService was not started with the worker"):
            await result(task)


async def test_failed_connect_fails_the_startup() -> None:
    deps = Dependencies()
    broker = make_broker(deps)

    async def task(broken: Broken) -> None: ...  # pragma: no cover

    broker.task(task)
    with pytest.raises(RuntimeError, match=r"nuke-di clients failed to start: .*Broken.*unreachable"):
        await broker.startup()

    assert not deps.connected
    assert not deps.clients
    # A worker shuts the broker down after a failed start; nothing is left to disconnect
    await broker.shutdown()


async def test_handlers_around_the_container() -> None:
    broker = InMemoryBroker()
    deps = Dependencies()

    @broker.on_event(TaskiqEvents.WORKER_STARTUP)
    def before_setup(state: TaskiqState) -> None:
        events.append(f"startup handler: connected={deps.connected}")

    setup(broker, deps)

    @broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
    def after_setup(state: TaskiqState) -> None:
        events.append(f"shutdown handler: connected={deps.connected}")

    broker.task(send_report)
    async with started(broker):
        pass

    assert events == [
        "database: connected",
        "startup handler: connected=True",
        "shutdown handler: connected=True",
        "database: disconnected",
    ]


async def test_failed_shutdown_still_disconnects() -> None:
    broker = make_broker(Dependencies())
    broker.task(send_report)

    @broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
    def fail(state: TaskiqState) -> None:
        raise OSError("result backend is gone")

    await broker.startup()
    with pytest.raises(OSError, match="result backend is gone"):
        await broker.shutdown()

    assert events == ["database: connected", "database: disconnected"]


async def test_startup_twice() -> None:
    broker = make_broker(Dependencies())
    broker.task(send_report)

    async with started(broker):
        with pytest.raises(RuntimeError, match="the container is already connected"):
            await broker.startup()

    assert events == ["database: connected", "database: disconnected"]


async def test_container_connected_by_another_app() -> None:
    # An InMemoryBroker started inside an app that runs the same container, e.g. in its tests
    deps = Dependencies()
    broker = make_broker(deps)
    broker.task(send_report)

    async with deps:
        with pytest.raises(RuntimeError, match="the container is already connected"):
            await broker.startup()


# --- worker and client processes -------------------------------------------------------------------------


async def report_in_worker(user_id: int, users: UserService) -> None:
    events.append(await users.greet(user_id))


async def test_worker_process() -> None:
    # What `taskiq worker` does: the tasks are imported, the receiver reads their signatures, then it starts
    deps = Dependencies()
    broker = QueueBroker()
    setup(broker, deps)
    task: Any = broker.task(report_in_worker)  # `.kiq()` is typed with the clients, see below
    await task.kiq(5)

    broker.is_worker_process = True
    receiver = Receiver(broker, max_async_tasks=10)
    # taskiq 0.11 listens without a finish event; either way it stops when the queue runs out
    finish = [asyncio.Event()] if "finish_event" in inspect.signature(receiver.listen).parameters else []
    await receiver.listen(*finish)
    events.append(f"listened: connected={deps.connected}")
    await broker.shutdown()

    assert events == ["database: connected", "Hello, user-5!", "listened: connected=True", "database: disconnected"]


async def test_client_process_connects_nothing() -> None:
    deps = Dependencies()
    broker = QueueBroker()
    setup(broker, deps)
    task: Any = broker.task(report_in_worker)

    async with started(broker):
        assert not deps.connected
        await task.kiq(5)

    assert len(broker.messages) == 1
    assert events == []
    assert not deps.clients


async def shared_report(user_id: int, users: UserService) -> None:
    events.append(await users.greet(user_id))


async def test_shared_task_declared_before_setup() -> None:
    from taskiq import async_shared_broker

    # After the broker: an InMemoryBroker reads the signatures of the shared tasks there are when it is made
    broker = InMemoryBroker()
    shared: Any = async_shared_broker.task(shared_report)
    try:
        setup(broker, Dependencies())
        async_shared_broker.default_broker(broker)
        async with started(broker):
            await (await shared.kiq(6)).wait_result(check_interval=0.01, timeout=5)
    finally:
        AsyncBroker.global_task_registry.pop(shared.task_name)
        async_shared_broker.default_broker(None)  # type: ignore[arg-type]

    assert events == ["database: connected", "Hello, user-6!", "database: disconnected"]


def test_setup_twice() -> None:
    broker = make_broker(Dependencies())

    with pytest.raises(TypeError, match=r"setup\(\) was already called for this broker"):
        setup(broker)


# --- the contract ----------------------------------------------------------------------------------------


def contract_app(container: Dependencies, handler: Callable[..., Any]) -> InMemoryBroker:
    broker = make_broker(container)
    broker.task("check")(handler)
    return broker


@asynccontextmanager
async def contract_run(broker: InMemoryBroker, lifespan: bool) -> AsyncIterator[Callable[[], object]]:
    task = broker.find_task("check")
    assert task is not None

    async def send() -> None:
        await result(task)

    if lifespan:
        async with started(broker):
            yield send
    else:
        yield send


async def test_keeps_the_integration_contract() -> None:
    await check(_TASKIQ, contract_app, contract_run)
