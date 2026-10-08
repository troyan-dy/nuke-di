# String annotations on purpose: every handler below goes through evaluating them
from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import pytest
from fastapi import Depends as FastAPIDepends
from fastapi import FastAPI
from faststream import Depends, FastStream, TestApp
from faststream.asgi import AsgiFastStream
from faststream.nats import NatsBroker, NatsRouter, TestNatsBroker

from nuke_di import DI, BackgroundTasks, Client, Dependencies, NotSingletonClient, Shutdown
from nuke_di.fastapi import setup as fastapi_setup
from nuke_di.faststream import setup

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


class Session(NotSingletonClient):
    pass


class Broken(Client):
    async def connect(self) -> None:
        raise OSError("unreachable")


def make_app(deps: Dependencies, broker: NatsBroker | None = None, **kwargs: Any) -> FastStream:
    app = FastStream(broker or NatsBroker(), **kwargs)
    setup(app, deps)
    return app


def broker_of(app: FastStream | AsgiFastStream) -> NatsBroker:
    broker = app.broker
    assert isinstance(broker, NatsBroker)
    return broker


# --- handlers --------------------------------------------------------------------------------------------


async def greet(user_id: int, users: UserService) -> None:
    events.append(await users.greet(user_id))


async def test_handler_gets_client_by_type_hint() -> None:
    deps = Dependencies()
    app = make_app(deps)
    broker_of(app).subscriber("greet")(greet)

    async with TestNatsBroker(broker_of(app)) as broker, TestApp(app):
        await broker.publish(1, "greet")

    assert events == ["database: connected", "Hello, user-1!", "database: disconnected"]


async def test_override_before_startup() -> None:
    deps = Dependencies()
    app = make_app(deps)
    broker_of(app).subscriber("greet")(greet)

    with deps.override(Database, FakeDatabase()):
        async with TestNatsBroker(broker_of(app)) as broker, TestApp(app):
            await broker.publish(1, "greet")

    assert events == ["Hello, alice!"]


async def test_global_container_by_default() -> None:
    app = FastStream(NatsBroker())
    setup(app)
    broker_of(app).subscriber("greet")(greet)

    with DI.override(Database, FakeDatabase()):
        async with TestNatsBroker(broker_of(app)) as broker, TestApp(app):
            await broker.publish(2, "greet")

    assert events == ["Hello, alice!"]


async def test_handler_stays_callable_directly() -> None:
    app = make_app(Dependencies())
    broker_of(app).subscriber("greet")(greet)

    async with TestNatsBroker(broker_of(app)), TestApp(app):
        pass

    await greet(1, UserService(FakeDatabase()))
    assert events[-1] == "Hello, alice!"


async def test_subscriber_declared_after_setup_and_on_router() -> None:
    deps = Dependencies()
    broker = NatsBroker()
    app = make_app(deps, broker)
    router = NatsRouter(prefix="r.")
    router.subscriber("greet")(greet)
    broker.include_router(router)

    async with TestNatsBroker(broker) as test_broker, TestApp(app):
        await test_broker.publish(3, "r.greet")

    assert events[1:-1] == ["Hello, user-3!"]


async def current_user(user_id: int, db: Database) -> str:
    return await db.fetch_user(user_id)


async def me(user: Annotated[str, Depends(current_user)], users: UserService) -> None:
    events.append(f"{user}: {await users.greet(9)}")


async def broker_audit(db: Database) -> None:
    events.append("broker audit")


async def router_audit(db: Database) -> None:
    events.append("router audit")


async def subscriber_audit(db: Database) -> None:
    events.append("subscriber audit")


async def test_dependencies_get_clients() -> None:
    deps = Dependencies()
    broker = NatsBroker(dependencies=[Depends(broker_audit)])
    app = make_app(deps, broker)
    router = NatsRouter(dependencies=[Depends(router_audit)])
    router.subscriber("me", dependencies=[Depends(subscriber_audit)])(me)
    broker.include_router(router)

    async with TestNatsBroker(broker) as test_broker, TestApp(app):
        await test_broker.publish(4, "me")

    assert sorted(events[1:-1]) == ["broker audit", "router audit", "subscriber audit", "user-4: Hello, user-9!"]


class Auth:
    def __init__(self, user_id: int, db: Database) -> None:
        self.user_id = user_id
        self.db = db


async def whoami(auth: Annotated[Auth, Depends(Auth)]) -> None:
    events.append(await auth.db.fetch_user(auth.user_id))


async def test_class_dependency_gets_clients() -> None:
    app = make_app(Dependencies())
    broker_of(app).subscriber("whoami")(whoami)

    async with TestNatsBroker(broker_of(app)) as broker, TestApp(app):
        await broker.publish(5, "whoami")

    assert events[1:-1] == ["user-5"]


def sync_greet(name: str, session: Session) -> None:
    events.append(f"sync {name} {type(session).__name__}")


async def sessions(first: Session, second: Session) -> None:
    events.append(f"same session: {first is second}")


async def test_sync_handler_and_not_singleton_clients() -> None:
    app = make_app(Dependencies())
    broker_of(app).subscriber("sync")(sync_greet)
    broker_of(app).subscriber("sessions")(sessions)

    async with TestNatsBroker(broker_of(app)) as broker, TestApp(app):
        await broker.publish("bob", "sync")
        await broker.publish(None, "sessions")

    assert events == ["sync bob Session", "same session: False"]


# --- lifecycle -------------------------------------------------------------------------------------------


@asynccontextmanager
async def lifespan() -> AsyncIterator[None]:
    events.append(f"app lifespan: started, connected={DI.connected}")
    yield
    events.append(f"app lifespan: stopping, connected={DI.connected}")


async def test_app_lifespan_and_hooks_run_inside_connected_clients() -> None:
    app = FastStream(NatsBroker(), lifespan=lifespan)
    setup(app)
    broker_of(app).subscriber("greet")(greet)

    @app.on_startup
    async def on_startup() -> None:
        events.append(f"on_startup: connected={DI.connected}")

    @app.after_shutdown
    async def after_shutdown() -> None:
        events.append(f"after_shutdown: connected={DI.connected}")

    async with TestNatsBroker(broker_of(app)), TestApp(app):
        pass

    assert events == [
        "database: connected",
        "app lifespan: started, connected=True",
        "on_startup: connected=True",
        "after_shutdown: connected=True",
        "app lifespan: stopping, connected=True",
        "database: disconnected",
    ]


async def loop(shutdown: Shutdown, tasks: BackgroundTasks) -> None:
    events.append(f"shutdown set: {shutdown.is_set()}")


async def test_shutdown_is_set_and_tasks_stop_before_disconnect() -> None:
    deps = Dependencies()
    app = make_app(deps)
    broker_of(app).subscriber("loop")(loop)

    async with TestNatsBroker(broker_of(app)) as broker, TestApp(app):
        await broker.publish(None, "loop")
        shutdown = deps.clients[Shutdown]

    assert events == ["shutdown set: False"]
    assert isinstance(shutdown, Shutdown)
    assert shutdown.is_set()


async def broken(kafka: Broken) -> None: ...


async def test_failed_connect_fails_startup_and_forgets_clients() -> None:
    deps = Dependencies()
    app = make_app(deps)
    broker_of(app).subscriber("broken")(broken)

    with pytest.raises(RuntimeError, match=r"nuke-di clients failed to start: Error occurred connecting client Broken"):
        async with TestNatsBroker(broker_of(app)), TestApp(app):
            pass

    assert not deps.connected
    assert deps.connect_clients == []


async def test_container_connected_before_startup_is_left_alone() -> None:
    deps = Dependencies()
    deps.resolve(Database)
    app = make_app(deps)
    broker_of(app).subscriber("greet")(greet)

    async with deps:
        with pytest.raises(RuntimeError, match=r"the container is already connected"):
            async with TestNatsBroker(broker_of(app)), TestApp(app):
                pass
        assert deps.connected

    assert events == ["database: connected", "database: disconnected"]


async def test_subscribers_of_other_brokers_are_not_connected() -> None:
    deps = Dependencies()
    app = make_app(deps)
    broker_of(app).subscriber("ping")(ping)
    NatsBroker().subscriber("greet")(greet)

    async with TestNatsBroker(broker_of(app)), TestApp(app):
        pass

    assert events == []


async def ping() -> None: ...


async def test_asgi_app() -> None:
    deps = Dependencies()
    app = AsgiFastStream(NatsBroker())
    setup(app, deps)
    broker_of(app).subscriber("greet")(greet)

    async with TestNatsBroker(broker_of(app)) as broker, TestApp(app):
        await broker.publish(6, "greet")

    assert events[1:-1] == ["Hello, user-6!"]


async def nested_name(user_id: int, db: Database) -> str:
    return await db.fetch_user(user_id)


async def nested(name: Annotated[str, Depends(nested_name)]) -> None:
    # No client of its own: only its dependency is rewritten
    events.append(f"Hello, nested {name}!")


async def test_app_per_container_on_one_broker() -> None:
    # E.g. a module-level broker and an app factory per test
    broker = NatsBroker()
    broker.subscriber("greet")(greet)
    broker.subscriber("nested")(nested)
    first, second = Dependencies(), Dependencies()
    first_app = make_app(first, broker)
    second_app = make_app(second, broker)

    with second.override(Database, FakeDatabase()):
        for app in (first_app, second_app, first_app):
            async with TestNatsBroker(broker) as test_broker, TestApp(app):
                await test_broker.publish(1, "greet")
                await test_broker.publish(2, "nested")

    assert [event for event in events if event.startswith("Hello")] == [
        "Hello, user-1!",
        "Hello, nested user-2!",
        "Hello, alice!",
        "Hello, nested alice!",
        "Hello, user-1!",
        "Hello, nested user-2!",
    ]
    # One decorator per broker, however many apps were set up on it
    assert len(broker.config.fd_config.call_decorators) == 1


# --- misuse ----------------------------------------------------------------------------------------------


async def unstarted(users: UserService) -> None: ...


async def test_broker_without_the_app_explains() -> None:
    app = make_app(Dependencies())
    broker_of(app).subscriber("unstarted")(unstarted)

    expected = r"UserService is not connected: start the app with its lifespan, e\.g\. `async with TestApp\(app\)`"
    with pytest.raises(RuntimeError, match=expected):
        async with TestNatsBroker(broker_of(app)) as broker:
            await broker.publish(None, "unstarted")


async def late(users: UserService) -> None: ...


async def test_subscriber_added_after_startup_explains() -> None:
    app = make_app(Dependencies())
    broker = broker_of(app)
    broker.subscriber("ping")(ping)

    async with TestNatsBroker(broker) as test_broker, TestApp(app):
        subscriber = broker.subscriber("late")
        subscriber(late)
        await subscriber.start()

        expected = r"UserService was not started with the app: declare its subscriber on a broker of the app before"
        with pytest.raises(RuntimeError, match=expected):
            await test_broker.publish(None, "late")


async def test_apps_sharing_a_function_run_one_at_a_time() -> None:
    first_broker, second_broker = NatsBroker(), NatsBroker()
    first_broker.subscriber("greet")(greet)
    second_broker.subscriber("greet")(greet)
    first_app = make_app(Dependencies(), first_broker)
    second_app = make_app(Dependencies(), second_broker)

    expected = r"UserService is filled for another app that is running; apps that share a handler function run one"
    async with TestNatsBroker(first_broker), TestApp(first_app):
        with pytest.raises(RuntimeError, match=expected):
            async with TestNatsBroker(second_broker), TestApp(second_app):
                pass  # pragma: no cover


async def shared_user(user_id: int, db: Database) -> str:
    return await db.fetch_user(user_id)


async def shared(user: Annotated[str, Depends(shared_user)]) -> None: ...


async def test_function_shared_with_fastapi_is_refused() -> None:
    fastapi_app = FastAPI()
    fastapi_setup(fastapi_app, Dependencies())
    fastapi_app.get("/shared")(shared_fastapi)
    app = make_app(Dependencies())
    broker_of(app).subscriber("shared")(shared)

    expected = r"shared_user takes clients in both FastAPI and FastStream handlers: .* its own function"
    with pytest.raises(TypeError, match=expected):
        async with TestNatsBroker(broker_of(app)), TestApp(app):
            pass  # pragma: no cover


async def shared_fastapi(user: Annotated[str, FastAPIDepends(shared_user)]) -> str:
    return user


def test_setup_twice() -> None:
    app = make_app(Dependencies())

    with pytest.raises(TypeError, match=r"setup\(\) was already called for this app"):
        setup(app)
