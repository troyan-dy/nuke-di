# String annotations on purpose: every endpoint below goes through evaluating them
from __future__ import annotations

import asyncio
import copy
import gc
import inspect
import weakref
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Annotated, Any

import fastapi.routing
import pytest
from fastapi import APIRouter, Depends, FastAPI, Header, WebSocket
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, TypeAdapter

from nuke_di import DI, BackgroundTasks, Client, ConnectError, Dependencies, NotSingletonClient, Shutdown
from nuke_di.fastapi import ClientRoute, ClientRouter, setup

if TYPE_CHECKING:
    from decimal import Decimal

events: list[str] = []

# FastAPI 0.14x applies included routers lazily instead of copying their routes
LAZY_INCLUDES = hasattr(fastapi.routing, "_IncludedRouter")


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


class Pooled(NotSingletonClient):
    async def connect(self) -> None:
        events.append("pooled: connected")


class Broken(Client):
    async def connect(self) -> None:
        raise OSError("unreachable")


class Unbuildable(Client):
    def __init__(self, retries: int) -> None:
        self.retries = retries


class CustomRoute(APIRoute):
    pass


def make_app(deps: Dependencies, **kwargs: Any) -> FastAPI:
    app = FastAPI(**kwargs)
    setup(app, deps)
    return app


# --- endpoints -------------------------------------------------------------------------------------------


async def greet(user_id: int, users: UserService) -> str:
    return await users.greet(user_id)


def test_endpoint_gets_client_by_type_hint() -> None:
    deps = Dependencies()
    app = make_app(deps)
    app.get("/users/{user_id}")(greet)

    with TestClient(app) as client:
        events.append("serving")
        assert client.get("/users/42").json() == "Hello, user-42!"

    assert events == ["database: connected", "serving", "database: disconnected"]
    assert deps.clients == {}


def sync_greet(users: UserService) -> str:
    return type(users).__name__


def test_sync_endpoint() -> None:
    app = make_app(Dependencies())
    app.get("/")(sync_greet)

    with TestClient(app) as client:
        assert client.get("/").json() == "UserService"


async def documented(user_id: int, users: Annotated[UserService, "the users"]) -> str:
    return await users.greet(user_id)


def test_annotated_client_without_depends() -> None:
    app = make_app(Dependencies())
    app.get("/users/{user_id}")(documented)

    with TestClient(app) as client:
        assert client.get("/users/1").json() == "Hello, user-1!"


def test_clients_stay_out_of_openapi() -> None:
    app = make_app(Dependencies())
    app.get("/users/{user_id}")(greet)

    parameters = app.openapi()["paths"]["/users/{user_id}"]["get"]["parameters"]

    assert [parameter["name"] for parameter in parameters] == ["user_id"]


async def greet_directly() -> None:
    assert await greet(7, UserService(FakeDatabase())) == "Hello, alice!"


async def test_endpoint_stays_callable_directly() -> None:
    app = make_app(Dependencies())
    app.get("/users/{user_id}")(greet)

    await greet_directly()


async def two_sessions(first: Session, second: Session, users: UserService) -> bool:
    return first is not second


def test_not_singleton_client_per_argument() -> None:
    app = make_app(Dependencies())
    app.get("/")(two_sessions)

    with TestClient(app) as client:
        assert client.get("/").json() is True


# --- dependencies ----------------------------------------------------------------------------------------


async def current_user(users: UserService) -> AsyncIterator[str]:
    try:
        yield await users.greet(1)
    except ValueError as exc:
        events.append(f"current_user saw {exc}")
        raise


async def me(user: Annotated[str, Depends(current_user)]) -> str:
    return user


async def me_by_default(user: str = Depends(current_user)) -> str:
    return user


async def fails(user: Annotated[str, Depends(current_user)]) -> str:
    raise ValueError("boom")


def test_dependency_with_yield_gets_client() -> None:
    app = make_app(Dependencies())
    app.get("/annotated")(me)
    app.get("/default")(me_by_default)
    app.get("/fails")(fails)

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/annotated").json() == "Hello, user-1!"
        assert client.get("/default").json() == "Hello, user-1!"
        assert client.get("/fails").status_code == 500

    assert "current_user saw boom" in events


async def app_audit(db: Database) -> None:
    events.append("app audit")


async def router_audit(db: Database) -> None:
    events.append("router audit")


async def route_audit(db: Database) -> None:
    events.append("route audit")


async def ping() -> str:
    return "pong"


def test_app_router_and_route_dependencies_get_clients() -> None:
    deps = Dependencies()
    app = make_app(deps, dependencies=[Depends(app_audit)])
    router = ClientRouter(container=deps, dependencies=[Depends(router_audit)])
    router.get("/ping", dependencies=[Depends(route_audit)])(ping)
    app.include_router(router, prefix="/r")

    with TestClient(app) as client:
        assert client.get("/r/ping").json() == "pong"

    assert sorted(events[1:-1]) == ["app audit", "route audit", "router audit"]


def test_dependency_overrides_still_apply() -> None:
    app = make_app(Dependencies())
    app.get("/")(me)
    app.dependency_overrides[current_user] = lambda: "overridden"

    with TestClient(app) as client:
        assert client.get("/").json() == "overridden"


# --- container -------------------------------------------------------------------------------------------


def test_override_before_startup() -> None:
    deps = Dependencies()
    app = make_app(deps)
    app.get("/users/{user_id}")(greet)

    with deps.override(Database, FakeDatabase()), TestClient(app) as client:
        assert client.get("/users/1").json() == "Hello, alice!"

    assert "database: connected" not in events


def test_global_container_by_default() -> None:
    app = FastAPI()
    setup(app)
    app.get("/users/{user_id}")(greet)

    assert app.router.route_class is ClientRoute
    with TestClient(app) as client:
        assert client.get("/users/3").json() == "Hello, user-3!"
        assert DI.connected
    assert not DI.connected


def test_router_on_global_container_by_default() -> None:
    app = FastAPI()
    setup(app)
    router = ClientRouter()
    router.get("/users/{user_id}")(greet)
    app.include_router(router)

    assert router.route_class is ClientRoute
    with TestClient(app) as client:
        assert client.get("/users/4").json() == "Hello, user-4!"


def test_plain_router_with_client_route() -> None:
    app = FastAPI()
    setup(app)
    router = APIRouter(route_class=ClientRoute)
    router.get("/users/{user_id}")(greet)
    app.include_router(router)

    with TestClient(app) as client:
        assert client.get("/users/5").json() == "Hello, user-5!"


async def outer_audit(db: Database) -> None:
    events.append("outer audit")


async def include_audit(db: Database) -> None:
    events.append("include audit")


def test_nested_router_dependencies_get_clients() -> None:
    deps = Dependencies()
    app = make_app(deps)
    inner = ClientRouter(container=deps)
    inner.get("/ping")(ping)
    outer = ClientRouter(container=deps, dependencies=[Depends(outer_audit)])
    outer.include_router(inner, prefix="/in", dependencies=[Depends(include_audit)])
    app.include_router(outer, prefix="/out")

    with TestClient(app) as client:
        assert client.get("/out/in/ping").json() == "pong"

    assert sorted(events[1:-1]) == ["include audit", "outer audit"]


def test_router_route_class_must_match_its_container() -> None:
    with pytest.raises(TypeError, match=r"route_class=CustomRoute does not fill clients"):
        ClientRouter(route_class=CustomRoute)

    other = ClientRouter(container=Dependencies()).route_class
    assert not issubclass(other, ClientRoute)
    with pytest.raises(TypeError, match=r"route_class=ContainerRoute does not fill clients"):
        ClientRouter(route_class=other)


def test_app_include_router_dependencies_get_clients() -> None:
    deps = Dependencies()
    app = make_app(deps)
    router = ClientRouter(container=deps)
    router.get("/ping")(ping)
    app.include_router(router, dependencies=[Depends(include_audit)])

    with TestClient(app) as client:
        assert client.get("/ping").json() == "pong"

    assert events[1:-1] == ["include audit"]


def test_router_of_another_container_is_refused() -> None:
    app = make_app(Dependencies())
    module_router = ClientRouter()  # bound to DI, e.g. declared in a module
    outer = ClientRouter(container=Dependencies())

    expected = r"the router fills clients from another container than this app"
    with pytest.raises(TypeError, match=expected):
        app.include_router(module_router)
    with pytest.raises(TypeError, match=r"the router fills clients from another container than this router"):
        outer.include_router(module_router)


async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    events.append("app: startup")
    yield
    events.append("app: shutdown")


def test_app_lifespan_runs_inside_connected_clients() -> None:
    app = make_app(Dependencies(), lifespan=asynccontextmanager(lifespan))
    app.get("/users/{user_id}")(greet)

    with TestClient(app):
        pass

    assert events == ["database: connected", "app: startup", "app: shutdown", "database: disconnected"]


async def watch(shutdown: Shutdown, tasks: BackgroundTasks) -> None:
    async def loop() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            events.append(f"loop: cancelled, shutdown set: {shutdown.is_set()}")
            raise

    tasks.spawn(loop())


def test_shutdown_is_set_and_tasks_stop_before_disconnect() -> None:
    app = make_app(Dependencies())
    app.get("/users/{user_id}")(greet)
    app.post("/watch")(watch)

    with TestClient(app) as client:
        client.post("/watch")

    assert events[-2:] == ["loop: cancelled, shutdown set: True", "database: disconnected"]


async def broken(broken: Broken) -> None: ...


def test_failed_connect_fails_startup_and_forgets_clients() -> None:
    deps = Dependencies()
    app = make_app(deps)
    app.get("/")(broken)

    # A SystemExit would escape the event loop of the server, so the lifespan raises a plain error
    with pytest.raises(RuntimeError, match="nuke-di clients failed to start") as info, TestClient(app):
        pass  # pragma: no cover

    assert isinstance(info.value.__cause__, ConnectError)
    assert not deps.connected
    assert deps.clients == {}


async def unbuildable(client: Unbuildable) -> None: ...


def test_failed_resolution_fails_startup_and_flushes() -> None:
    deps = Dependencies()
    app = make_app(deps)
    app.get("/users/{user_id}")(greet)
    app.get("/")(unbuildable)

    with pytest.raises(TypeError, match=r'Argument "retries" of "Unbuildable\.__init__"'), TestClient(app):
        pass  # pragma: no cover

    assert deps.clients == {}


async def test_container_connected_before_startup_is_left_alone() -> None:
    deps = Dependencies()
    app = make_app(deps)
    app.get("/users/{user_id}")(greet)
    deps.resolve(Database)
    await deps.connect()

    with pytest.raises(RuntimeError, match="already connected"), TestClient(app):
        pass  # pragma: no cover

    assert deps.connected
    await deps.disconnect()


class Pagination:
    def __init__(self, page: int = 1) -> None:
        self.page = page


async def paged(pagination: Annotated[Pagination, Depends()], users: UserService) -> int:
    return pagination.page


def test_class_dependency_is_left_to_fastapi() -> None:
    app = make_app(Dependencies())
    app.get("/")(paged)

    with TestClient(app) as client:
        assert client.get("/", params={"page": 3}).json() == 3


class Auth:
    def __init__(self, users: UserService, user_id: int = 1) -> None:
        self.users, self.user_id = users, user_id


async def whoami(auth: Annotated[Auth, Depends()]) -> str:
    return await auth.users.greet(auth.user_id)


async def whoami_by_class(auth: Annotated[Auth, Depends(Auth)]) -> str:
    return await auth.users.greet(auth.user_id)


def test_class_dependency_gets_clients() -> None:
    app = make_app(Dependencies())
    app.get("/annotated")(whoami)
    app.get("/default")(whoami_by_class)

    with TestClient(app) as client:
        assert client.get("/annotated", params={"user_id": 2}).json() == "Hello, user-2!"
        assert client.get("/default").json() == "Hello, user-1!"


async def test_failed_startup_of_another_app_keeps_the_running_one() -> None:
    deps = Dependencies()
    first, second = make_app(deps), make_app(deps)
    first.get("/users/{user_id}")(greet)

    with TestClient(first) as client:
        with pytest.raises(RuntimeError, match="already connected"), TestClient(second):
            pass  # pragma: no cover

        assert client.get("/users/1").json() == "Hello, user-1!"


def make_closure_app(deps: Dependencies) -> FastAPI:
    app = make_app(deps)

    @app.get("/")
    async def endpoint(pooled: Pooled) -> None: ...

    return app


def test_routes_of_dropped_apps_are_not_connected() -> None:
    deps = Dependencies()
    make_closure_app(deps)
    gc.collect()

    with TestClient(make_closure_app(deps)):
        pass

    assert events.count("pooled: connected") == 1


def test_container_is_garbage_collected() -> None:
    deps = Dependencies()
    app = make_app(deps)
    app.get("/users/{user_id}")(greet)
    collected = weakref.ref(deps)

    del deps, app
    gc.collect()

    assert collected() is None


async def optional(db: Database | None = None) -> None: ...


def test_optional_client_is_not_filled() -> None:
    app = make_app(Dependencies())

    with pytest.raises(TypeError, match=r"Database is a nuke-di client.*not as an optional"):
        app.get("/")(optional)


def test_request_without_lifespan_explains() -> None:
    app = make_app(Dependencies())
    app.get("/users/{user_id}")(greet)

    expected = r"UserService is not connected: start the app with its lifespan, e\.g\. `with TestClient\(app\)`"
    with pytest.raises(RuntimeError, match=expected):
        TestClient(app).get("/users/1")


# --- misuse ----------------------------------------------------------------------------------------------


async def plain(users: UserService) -> None: ...


def test_router_without_route_class_explains() -> None:
    router = APIRouter()

    with pytest.raises(TypeError, match=r"UserService is a nuke-di client, not a pydantic type.*FastAPI"):
        router.get("/")(plain)


def test_function_on_two_containers() -> None:
    first, second = Dependencies(), Dependencies()
    first_app, second_app = make_app(first), make_app(second)
    first_app.get("/users/{user_id}")(greet)
    second_app.get("/users/{user_id}")(greet)

    with second.override(Database, FakeDatabase()), TestClient(first_app) as a, TestClient(second_app) as b:
        assert a.get("/users/1").json() == "Hello, user-1!"
        assert b.get("/users/1").json() == "Hello, alice!"


def test_setup_refuses_foreign_route_class() -> None:
    app = FastAPI()
    app.router.route_class = CustomRoute

    with pytest.raises(TypeError, match=r"app\.router\.route_class is CustomRoute"):
        setup(app, Dependencies())


def test_setup_keeps_derived_route_class() -> None:
    derived = type("Derived", (ClientRoute,), {})
    app = FastAPI()
    app.router.route_class = derived

    setup(app)

    assert app.router.route_class is derived


def test_setup_refuses_route_class_of_another_container() -> None:
    app = FastAPI()
    app.router.route_class = ClientRoute

    with pytest.raises(TypeError, match=r"app\.router\.route_class is ClientRoute"):
        setup(app, Dependencies())


def test_setup_twice() -> None:
    deps = Dependencies()
    app = make_app(deps)

    with pytest.raises(TypeError, match=r"setup\(\) was already called for this app"):
        setup(app, deps)


async def price() -> Decimal:
    return 3  # type: ignore[return-value]


async def priced(amount: Annotated[int, Depends(price)], users: UserService) -> int:
    return amount


def test_dependency_with_unevaluable_hints_is_left_to_fastapi() -> None:
    # `Decimal` exists only for type checkers: FastAPI ignores the return type of a dependency, so do we
    app = make_app(Dependencies())
    app.get("/")(priced)

    with TestClient(app) as client:
        assert client.get("/").json() == 3


# --- pydantic --------------------------------------------------------------------------------------------


def test_pydantic_explains_client_type() -> None:
    with pytest.raises(TypeError, match="UserService is a nuke-di client, not a pydantic type"):
        TypeAdapter(UserService)


class Holder(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    users: UserService


def test_pydantic_arbitrary_types_still_work() -> None:
    users = UserService(Database())

    assert Holder(users=users).users is users


def test_routes_outside_the_app_are_not_connected() -> None:
    deps = Dependencies()
    app = make_app(deps)
    app.get("/users/{user_id}")(greet)
    unused = ClientRouter(container=deps)
    unused.get("/")(two_pooled)

    with TestClient(app):
        pass

    assert "pooled: connected" not in events


async def two_pooled(pooled: Pooled) -> None: ...


class Prefix:
    def __call__(self, prefix: str = "Dear") -> str:
        return prefix


async def addressed(prefix: Annotated[str, Depends(Prefix())], users: UserService) -> str:
    return f"{prefix} {await users.greet(1)}"


def test_callable_object_dependency_is_left_to_fastapi() -> None:
    app = make_app(Dependencies())
    app.get("/")(addressed)

    with TestClient(app) as client:
        assert client.get("/").json() == "Dear Hello, user-1!"


def test_app_router_include_router_is_tracked_too() -> None:
    deps = Dependencies()
    app = make_app(deps)
    router = ClientRouter(container=deps)
    router.get("/users/{user_id}")(greet)
    app.router.include_router(router, dependencies=[Depends(include_audit)])

    with TestClient(app) as client:
        assert client.get("/users/1").json() == "Hello, user-1!"

    assert "include audit" in events


def test_route_the_app_does_not_know_explains() -> None:
    deps = Dependencies()
    app = make_app(deps)
    hidden = ClientRouter(container=deps)
    hidden.get("/users/{user_id}")(greet)
    plain = APIRouter()
    plain.include_router(hidden)
    app.include_router(plain)

    with TestClient(app) as client:
        if not LAZY_INCLUDES:
            # Older FastAPI copies the routes through the route class, so nuke-di sees them
            assert client.get("/users/1").json() == "Hello, user-1!"
            return
        expected = r"UserService was not started with the app: include the router of its route .* plain APIRouter"
        with pytest.raises(RuntimeError, match=expected):
            client.get("/users/1")


def test_copy_of_a_container_has_its_own_route_class() -> None:
    deps = Dependencies()
    original = ClientRouter(container=deps).route_class

    copied = ClientRouter(container=copy.deepcopy(deps)).route_class

    assert copied is not original
    assert original.__name__ == copied.__name__ == "ContainerRoute"


def test_class_dependency_signature_has_no_return_annotation() -> None:
    app = make_app(Dependencies())
    app.get("/annotated")(whoami)

    assert inspect.signature(Auth).return_annotation is inspect.Signature.empty


async def header_user(x_user_id: Annotated[int, Header()], db: Database) -> str:
    return await db.fetch_user(x_user_id)


async def by_header(user: Annotated[str, Depends(header_user)]) -> str:
    return user


def test_dependency_with_header_and_client() -> None:
    # The shape of the README example, so the lowest supported FastAPI is checked against it
    app = make_app(Dependencies())
    app.get("/me")(by_header)

    with TestClient(app) as client:
        assert client.get("/me", headers={"X-User-Id": "7"}).json() == "user-7"


# --- websockets ------------------------------------------------------------------------------------------


async def chat(websocket: WebSocket, user: Annotated[str, Depends(current_user)], users: UserService) -> None:
    await websocket.accept()
    user_id = int(await websocket.receive_text())
    await websocket.send_text(f"{user}: {await users.greet(user_id)}")
    await websocket.close()


def test_websocket_endpoint_gets_clients() -> None:
    deps = Dependencies()
    app = make_app(deps, dependencies=[Depends(app_audit)])
    app.websocket("/chat", dependencies=[Depends(route_audit)])(chat)

    with deps.override(Database, FakeDatabase()), TestClient(app) as client, client.websocket_connect("/chat") as ws:
        ws.send_text("2")
        assert ws.receive_text() == "Hello, alice!: Hello, alice!"

    assert sorted(events) == ["app audit", "route audit"]


def test_websocket_of_included_routers_gets_clients() -> None:
    deps = Dependencies()
    app = make_app(deps)
    inner = ClientRouter(container=deps, dependencies=[Depends(router_audit)])
    inner.websocket("/chat")(chat)
    outer = ClientRouter(container=deps, dependencies=[Depends(outer_audit)])
    outer.include_router(inner, prefix="/in", dependencies=[Depends(include_audit)])
    app.include_router(outer, prefix="/out")

    with TestClient(app) as client, client.websocket_connect("/out/in/chat") as ws:
        ws.send_text("2")
        assert ws.receive_text() == "Hello, user-1!: Hello, user-2!"

    assert sorted(events[1:-1]) == ["include audit", "outer audit", "router audit"]


def test_websocket_added_without_decorator_gets_clients() -> None:
    deps = Dependencies()
    app = make_app(deps)
    router = ClientRouter(container=deps)
    router.add_api_websocket_route("/chat", chat)
    app.add_api_websocket_route("/app-chat", chat)
    app.include_router(router)

    with TestClient(app) as client:
        for path in ("/chat", "/app-chat"):
            with client.websocket_connect(path) as ws:
                ws.send_text("3")
                assert ws.receive_text() == "Hello, user-1!: Hello, user-3!"


def test_websocket_of_a_router_the_app_does_not_include_starts_nothing() -> None:
    deps = Dependencies()
    app = make_app(deps)
    app.get("/ping")(ping)
    ClientRouter(container=deps).websocket("/chat")(chat)

    with TestClient(app):
        assert events == []


async def plain_chat(websocket: WebSocket, users: UserService) -> None: ...


def test_websocket_on_plain_router_explains() -> None:
    # The route class is not used for websockets
    router = APIRouter(route_class=ClientRoute)

    with pytest.raises(TypeError, match=r"UserService is a nuke-di client, not a pydantic type"):
        router.websocket("/chat")(plain_chat)
