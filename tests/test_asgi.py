import inspect
import subprocess
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, get_type_hints

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

from nuke_di import Client, Dependencies, Shutdown
from nuke_di.asgi import _ASGI, Lifespan, lifespan
from nuke_di.integration.testing import Send, check

events: list[str] = []


@pytest.fixture(autouse=True)
def clear_events() -> Iterator[None]:
    events.clear()
    yield
    events.clear()


class Database(Client):
    async def connect(self) -> None:
        events.append("database: connected")

    async def disconnect(self) -> None:
        events.append("database: disconnected")

    def fetch(self) -> str:
        return "alice"


class UserService(Client):
    def __init__(self, db: Database) -> None:
        self.db = db

    def greet(self) -> str:
        return f"Hello, {self.db.fetch()}!"


class FakeDatabase(Database):
    def fetch(self) -> str:
        return "tester"


def make_app(clients: Lifespan) -> Starlette:
    async def greet(request: Request) -> Response:
        return PlainTextResponse(clients.get(UserService).greet())

    async def db(request: Request) -> Response:
        return PlainTextResponse(clients.get(Database).fetch())

    return Starlette(routes=[Route("/greet", greet), Route("/db", db)], lifespan=clients)


def test_handlers_take_the_listed_clients_while_the_app_runs() -> None:
    clients = lifespan(Dependencies(), UserService, Database)

    with TestClient(make_app(clients)) as client:
        assert client.get("/greet").text == "Hello, alice!"
        # One container: the Database of the handler is the one UserService got
        assert clients.get(UserService).db is clients.get(Database)

    assert events == ["database: connected", "database: disconnected"]


def test_a_client_not_listed_is_refused_even_when_it_is_connected() -> None:
    # Database connects as a dependency of UserService, but the list is what the handlers may take
    clients = lifespan(Dependencies(), UserService)

    with TestClient(make_app(clients)) as client, pytest.raises(RuntimeError) as exc:
        client.get("/db")

    assert str(exc.value) == "Database is not a client of this lifespan: list it in `lifespan(container, ...)`"


def test_a_handler_without_the_lifespan_is_told_to_start_the_app() -> None:
    clients = lifespan(Dependencies(), UserService)

    with pytest.raises(RuntimeError) as exc:
        TestClient(make_app(clients)).get("/greet")

    assert str(exc.value) == (
        "UserService is not connected: start the app with its lifespan, e.g. `with TestClient(app)` in Starlette "
        "or `async with app.test_app()` in Quart"
    )
    assert events == []


def test_after_shutdown_the_clients_are_gone() -> None:
    clients = lifespan(Dependencies(), Database)
    with TestClient(make_app(clients)):
        pass

    with pytest.raises(RuntimeError, match="Database is not connected"):
        clients.get(Database)


def test_override_before_startup_replaces_a_client() -> None:
    container = Dependencies()
    clients = lifespan(container, UserService)
    app = make_app(clients)

    with container.override(Database, FakeDatabase()), TestClient(app) as client:
        assert client.get("/greet").text == "Hello, tester!"

    # A Replacement is never connected
    assert events == []


def test_every_startup_resolves_fresh_clients() -> None:
    clients = lifespan(Dependencies(), UserService)
    app = make_app(clients)
    seen = []

    for _ in range(2):
        with TestClient(app) as client:
            assert client.get("/greet").text == "Hello, alice!"
            seen.append(clients.get(UserService))

    assert seen[0] is not seen[1]
    assert events == ["database: connected", "database: disconnected"] * 2


def test_the_apps_own_lifespan_runs_inside_and_keeps_its_state() -> None:
    clients = lifespan(Dependencies(), UserService)

    @asynccontextmanager
    async def app_lifespan(app: Starlette) -> AsyncIterator[dict[str, str]]:
        async with clients(app):
            events.append(f"startup: {clients.get(UserService).greet()}")
            yield {"version": "1.0"}
            events.append("shutdown")

    async def version(request: Request) -> Response:
        return PlainTextResponse(f"{request.state.version} {clients.get(UserService).greet()}")

    app = Starlette(routes=[Route("/version", version)], lifespan=app_lifespan)
    with TestClient(app) as client:
        assert client.get("/version").text == "1.0 Hello, alice!"

    assert events == ["database: connected", "startup: Hello, alice!", "shutdown", "database: disconnected"]


async def test_runs_without_a_framework() -> None:
    clients = lifespan(Dependencies(), UserService, Shutdown)

    async with clients():
        assert clients.get(UserService).greet() == "Hello, alice!"
        shutdown = clients.get(Shutdown)
        assert not shutdown.is_set()

    # As a Run does: the loops are asked to stop before the clients disconnect
    assert shutdown.is_set()
    assert events == ["database: connected", "database: disconnected"]


async def test_one_lifespan_runs_one_app_at_a_time() -> None:
    clients = lifespan(Dependencies(), Database)

    async with clients():
        with pytest.raises(RuntimeError, match="the container is already connected"):
            async with clients():
                pass  # pragma: no cover

    assert events == ["database: connected", "database: disconnected"]


def test_a_client_listed_twice_is_one_client() -> None:
    clients = lifespan(Dependencies(), Database, Database)

    with TestClient(make_app(clients)) as client:
        assert client.get("/db").text == "alice"

    assert events == ["database: connected", "database: disconnected"]


class Broken(Client):
    async def connect(self) -> None:
        raise OSError("connection refused")


def test_a_failed_connect_fails_the_startup_and_flushes_the_container() -> None:
    container = Dependencies()
    clients = lifespan(container, UserService, Broken)

    with pytest.raises(RuntimeError) as exc, TestClient(make_app(clients)):
        pass  # pragma: no cover

    assert str(exc.value) == "nuke-di clients failed to start: Broken.connect() raised OSError: connection refused"
    assert not container.connected
    assert not container.clients
    with pytest.raises(RuntimeError, match="Broken is not connected"):
        clients.get(Broken)


@pytest.mark.parametrize("make", [lifespan, Lifespan])
def test_the_container_comes_first(make: Callable[..., Lifespan]) -> None:
    with pytest.raises(TypeError) as exc:
        make(Database)

    assert str(exc.value).startswith(
        "lifespan() takes the container first, then the clients, e.g. lifespan(DI, Database); got <class "
    )


@pytest.mark.parametrize("make", [lifespan, Lifespan])
@pytest.mark.parametrize("item", [str, "Database", None, Annotated[Database, "x"]])
def test_only_clients_are_listed(make: Callable[..., Lifespan], item: Any) -> None:
    with pytest.raises(TypeError) as exc:
        make(Dependencies(), Database, item)

    assert str(exc.value) == f"{item!r} is not a client: subclass Client or NotSingletonClient"


def test_imports_no_framework() -> None:
    code = "import sys, nuke_di.asgi; print(sorted(m for m in ('starlette', 'fastapi', 'quart') if m in sys.modules))"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)  # noqa: S603

    assert result.stdout.strip() == "[]"


# --- the contract ----------------------------------------------------------------------------------------


def contract_app(container: Dependencies, handler: Callable[..., Any]) -> Starlette:
    # Starlette has no DI: the endpoint takes the handler's clients from the lifespan and calls it
    hints = get_type_hints(handler)
    arguments = {name: hints[name] for name in inspect.signature(handler).parameters}
    clients = lifespan(container, *arguments.values())

    async def endpoint(request: Request) -> Response:
        await handler(**{name: clients.get(cls) for name, cls in arguments.items()})
        return PlainTextResponse("ok")

    return Starlette(routes=[Route("/", endpoint)], lifespan=clients)


@asynccontextmanager
async def contract_run(app: Starlette, lifespan: bool) -> AsyncIterator[Send]:
    # TestClient raises the error of a handler, so send() raises it too
    if lifespan:
        with TestClient(app) as client:
            yield lambda: client.get("/")
    else:
        yield lambda: TestClient(app).get("/")


async def test_keeps_the_integration_contract() -> None:
    await check(_ASGI, contract_app, contract_run)
