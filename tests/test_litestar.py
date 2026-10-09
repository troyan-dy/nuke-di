# String annotations on purpose: every handler below goes through evaluating them
from __future__ import annotations

import warnings
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Annotated, Any, TypeVar

import litestar.di
import pytest
from litestar import Controller, Litestar, Router, WebSocket, asgi, get, websocket, websocket_listener
from litestar.di import Provide
from litestar.handlers import WebsocketListener
from litestar.response.base import ASGIResponse
from litestar.testing import TestClient
from litestar.types import Receive, Scope, Send

from nuke_di import DI, BackgroundTasks, Client, Dependencies, NotSingletonClient, Shutdown
from nuke_di.integration.testing import check
from nuke_di.litestar import _LITESTAR, ClientPlugin

if TYPE_CHECKING:
    from decimal import Decimal

T = TypeVar("T")

if hasattr(litestar.di, "NamedDependency"):
    from litestar.di import NamedDependency
else:  # Litestar before 2.23
    from litestar.params import Dependency

    NamedDependency = Annotated[T, Dependency()]  # type: ignore[misc]

events: list[str] = []


@pytest.fixture(autouse=True)
def clear_events() -> Iterator[None]:
    events.clear()
    # Litestar 2.23+ warns about a dependency matched by name without a marker, and about its old markers;
    # nuke-di must cause neither
    with warnings.catch_warnings():
        warnings.filterwarnings("error", message=r".*Inferred dependency field")
        warnings.filterwarnings(
            "error", message=r".*(DependencyKwarg|deprecated function 'Dependency'|skip_validation)"
        )
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


class Session(NotSingletonClient):
    pass


class Broken(Client):
    async def connect(self) -> None:
        raise OSError("unreachable")


def make_app(deps: Dependencies, handlers: list[Any], **kwargs: Any) -> Litestar:
    return Litestar(handlers, plugins=[ClientPlugin(deps)], **kwargs)


# --- handlers --------------------------------------------------------------------------------------------


@get("/users/{user_id:int}")
async def get_user(user_id: int, users: UserService) -> str:
    return await users.greet(user_id)


def test_handler_gets_client_by_type_hint() -> None:
    deps = Dependencies()
    app = make_app(deps, [get_user])

    with TestClient(app) as client:
        assert events == ["database: connected"]
        assert client.get("/users/42").text == "Hello, user-42!"

    assert events == ["database: connected", "database: disconnected"]


def test_override_before_startup() -> None:
    deps = Dependencies()
    app = make_app(deps, [get_user])

    with deps.override(Database, FakeDatabase()), TestClient(app) as client:
        assert client.get("/users/1").text == "Hello, alice!"


def test_global_container_by_default() -> None:
    app = Litestar([get_user], plugins=[ClientPlugin()])

    with DI.override(Database, FakeDatabase()), TestClient(app) as client:
        assert client.get("/users/1").text == "Hello, alice!"


async def test_handler_stays_callable_directly() -> None:
    make_app(Dependencies(), [get_user])

    assert await get_user.fn(1, UserService(FakeDatabase())) == "Hello, alice!"


def test_clients_stay_out_of_openapi() -> None:
    app = make_app(Dependencies(), [get_user])

    parameters = app.openapi_schema.to_schema()["paths"]["/users/{user_id}"]["get"]["parameters"]
    assert [parameter["name"] for parameter in parameters] == ["user_id"]


@get("/sessions", sync_to_thread=False)
def sessions(first: Session, second: Session) -> bool:
    return first is second


def test_sync_handler_and_not_singleton_client_per_argument() -> None:
    app = make_app(Dependencies(), [sessions])

    with TestClient(app) as client:
        assert client.get("/sessions").json() is False


@get("/billing")
async def two_names(users: UserService, billing: Billing, service: UserService) -> bool:
    return users is service


def test_one_client_under_two_names() -> None:
    app = make_app(Dependencies(), [two_names])

    with TestClient(app) as client:
        assert client.get("/billing").json() is True


async def current_user(user_id: int, db: Database) -> str:
    return await db.fetch_user(user_id)


@get("/me", dependencies={"user": Provide(current_user)})
async def me(user: NamedDependency[str], users: UserService) -> str:
    return f"{user}: {await users.greet(2)}"


def test_provider_gets_clients() -> None:
    app = make_app(Dependencies(), [me])

    with TestClient(app) as client:
        assert client.get("/me?user_id=7").text == "user-7: Hello, user-2!"


async def app_audit(db: Database) -> str:
    events.append("app audit")
    return "app"


async def router_audit(db: Database) -> str:
    events.append("router audit")
    return "router"


@get("/audited")
async def audited(app_level: NamedDependency[str], router_level: NamedDependency[str]) -> str:
    return f"{app_level} {router_level}"


def test_layered_providers_get_clients() -> None:
    router = Router("/r", route_handlers=[audited], dependencies={"router_level": Provide(router_audit)})
    app = make_app(Dependencies(), [router], dependencies={"app_level": Provide(app_audit)})

    with TestClient(app) as client:
        assert client.get("/r/audited").text == "app router"

    assert sorted(events[1:-1]) == ["app audit", "router audit"]


async def provide_users() -> str:
    return "provided by the app"


@get("/provided")
async def provided(users: UserService) -> Any:
    return users


def test_dependency_of_the_same_name_wins() -> None:
    app = make_app(Dependencies(), [provided], dependencies={"users": Provide(provide_users)})

    with TestClient(app) as client:
        assert client.get("/provided").text == "provided by the app"

    assert events == []


class Auth:
    def __init__(self, user_id: int, db: Database) -> None:
        self.user_id = user_id
        self.db = db


@get("/whoami", dependencies={"auth": Provide(Auth, sync_to_thread=False)})
async def whoami(auth: NamedDependency[Auth]) -> str:
    return await auth.db.fetch_user(auth.user_id)


def test_class_provider_gets_clients() -> None:
    app = make_app(Dependencies(), [whoami])

    with TestClient(app) as client:
        assert client.get("/whoami?user_id=3").text == "user-3"


class UserController(Controller):
    path = "/controller"

    @get("/{user_id:int}")
    async def get_user(self, user_id: int, users: UserService) -> str:
        return await users.greet(user_id)


def test_nested_routers_and_controllers() -> None:
    inner = Router("/inner", route_handlers=[get_user, UserController])
    outer = Router("/outer", route_handlers=[inner])
    app = make_app(Dependencies(), [outer, UserController])

    with TestClient(app) as client:
        assert client.get("/outer/inner/users/1").text == "Hello, user-1!"
        assert client.get("/outer/inner/controller/2").text == "Hello, user-2!"
        assert client.get("/controller/3").text == "Hello, user-3!"


@websocket("/chat")
async def chat(socket: WebSocket, users: UserService) -> None:
    await socket.accept()
    user_id = int(await socket.receive_text())
    await socket.send_text(await users.greet(user_id))
    await socket.close()


def test_websocket_handler_gets_clients() -> None:
    app = make_app(Dependencies(), [chat])

    with TestClient(app) as client, client.websocket_connect("/chat") as ws:
        ws.send_text("5")
        assert ws.receive_text() == "Hello, user-5!"


# --- lifecycle -------------------------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: Litestar) -> AsyncIterator[None]:
    events.append(f"app lifespan: started, connected={DI.connected}")
    yield
    events.append(f"app lifespan: stopping, connected={DI.connected}")


def on_startup() -> None:
    events.append(f"on_startup: connected={DI.connected}")


def on_shutdown() -> None:
    events.append(f"on_shutdown: connected={DI.connected}")


def test_app_lifespan_and_hooks_run_inside_connected_clients() -> None:
    app = Litestar(
        [get_user], plugins=[ClientPlugin()], lifespan=[lifespan], on_startup=[on_startup], on_shutdown=[on_shutdown]
    )

    with TestClient(app):
        pass

    assert events == [
        "database: connected",
        "app lifespan: started, connected=True",
        "on_startup: connected=True",
        "app lifespan: stopping, connected=True",
        "on_shutdown: connected=True",
        "database: disconnected",
    ]


@get("/loop")
async def loop(shutdown: Shutdown, tasks: BackgroundTasks) -> bool:
    return shutdown.is_set()


def test_shutdown_is_set_and_app_restarts() -> None:
    deps = Dependencies()
    app = make_app(deps, [loop])

    for _ in range(2):
        with TestClient(app) as client:
            assert client.get("/loop").json() is False
            shutdown = deps.clients[Shutdown]
        assert isinstance(shutdown, Shutdown)
        assert shutdown.is_set()
        assert not deps.connected


@get("/broken")
async def broken(kafka: Broken) -> None: ...


def test_failed_connect_fails_startup_and_forgets_clients() -> None:
    deps = Dependencies()
    app = make_app(deps, [broken])

    # Litestar's test client raises the failed startup inside an exception group
    with pytest.raises(BaseExceptionGroup) as info:
        with TestClient(app):
            pass

    assert info.group_contains(RuntimeError, match=r"nuke-di clients failed to start: Broken.connect\(\) raised")

    assert not deps.connected
    assert deps.connect_clients == []


def test_two_apps_with_their_own_containers() -> None:
    first, second = Dependencies(), Dependencies()
    first_app, second_app = make_app(first, [get_user]), make_app(second, [get_user])

    with second.override(Database, FakeDatabase()), TestClient(first_app) as a, TestClient(second_app) as b:
        assert a.get("/users/1").text == "Hello, user-1!"
        assert b.get("/users/1").text == "Hello, alice!"


def test_request_without_lifespan_explains() -> None:
    app = make_app(Dependencies(), [get_user], debug=True)

    response = TestClient(app).get("/users/1")
    assert response.status_code == 500
    assert "UserService is not connected: start the app with its lifespan, e.g. `with TestClient(app)`" in response.text


# --- misuse ----------------------------------------------------------------------------------------------


@get("/users-is-billing")
async def users_is_billing(users: Billing) -> None: ...


def test_one_name_for_two_clients_is_refused() -> None:
    expected = (
        r'Argument "users" is UserService in get_user and Billing in users_is_billing: Litestar provides '
        r"dependencies by name, so give different clients different names"
    )
    with pytest.raises(TypeError, match=expected):
        make_app(Dependencies(), [get_user, users_is_billing])


def test_plugin_twice() -> None:
    with pytest.raises(TypeError, match=r"ClientPlugin was already added to this app"):
        Litestar([get_user], plugins=[ClientPlugin(), ClientPlugin()])


@get("/reserved")
async def reserved(state: Database) -> None: ...


def test_client_under_a_reserved_name_is_refused() -> None:
    expected = r'Argument "state" of reserved is Database, but Litestar reserves the name "state": rename it'
    with pytest.raises(TypeError, match=expected):
        make_app(Dependencies(), [reserved])


@websocket_listener("/listener")
async def listener(data: str, users: UserService) -> str:
    return data


class Listener(WebsocketListener):
    path = "/listener-class"

    async def on_receive(self, data: str, users: UserService) -> str:
        return data


@pytest.mark.parametrize("handler", [listener, Listener])
def test_websocket_listener_is_refused(handler: Any) -> None:
    expected = r'Argument "users" of the websocket listener .* is UserService: a listener takes no clients'
    with pytest.raises(TypeError, match=expected):
        make_app(Dependencies(), [handler])


@get("/unevaluable")
async def unevaluable(amount: Decimal) -> None: ...


def test_handler_with_unevaluable_hints_is_left_to_litestar() -> None:
    # Litestar resolves the name through its signature namespace
    app = make_app(Dependencies(), [unevaluable], signature_namespace={"Decimal": float})

    with TestClient(app) as client:
        assert client.get("/unevaluable?amount=1").status_code == 200


@asgi("/raw")
async def raw(scope: Scope, receive: Receive, send: Send) -> None:
    await ASGIResponse(body=b"raw")(scope, receive, send)


@websocket_listener("/echo")
async def echo(data: str) -> str:
    return data


class Greeting:
    def __call__(self, users: UserService) -> str:
        return "hi"


@get("/greeting", dependencies={"greeting": Provide(Greeting(), sync_to_thread=False)})
async def greeting(greeting: NamedDependency[str]) -> str:
    return greeting


def test_handlers_without_clients_are_left_to_litestar() -> None:
    # A callable object has no signature to annotate in place: Litestar reads `users` from the query
    app = make_app(Dependencies(), [raw, echo, greeting])

    with TestClient(app) as client:
        assert client.get("/raw").text == "raw"
        assert client.get("/greeting").status_code == 400
        with client.websocket_connect("/echo") as ws:
            ws.send_text("ping")
            assert ws.receive_text() == "ping"

    assert events == []


# --- the contract ----------------------------------------------------------------------------------------


def contract_app(container: Dependencies, handler: Callable[..., Any]) -> Litestar:
    # `debug` puts the error of a handler into the response
    return make_app(container, [get("/")(handler)], debug=True)


def contract_get(client: TestClient[Litestar]) -> None:
    response = client.get("/")
    if response.is_server_error:
        raise RuntimeError(response.text)


@asynccontextmanager
async def contract_run(app: Litestar, lifespan: bool) -> AsyncIterator[Callable[[], object]]:
    if lifespan:
        with TestClient(app) as client:
            yield lambda: contract_get(client)
    else:
        yield lambda: contract_get(TestClient(app))


async def test_keeps_the_integration_contract() -> None:
    # A plain Framework: Litestar provides dependencies by name, so the case of a dependency is skipped
    await check(_LITESTAR, contract_app, contract_run)
