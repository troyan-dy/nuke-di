"""
The public integration kit: the contract check against an integration written on it, and against integrations
that break the contract. The integrations of this package run the check in their own test files.
"""

import inspect
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any, get_args, get_origin

import pytest

from nuke_di import Client, Dependencies
from nuke_di import integration as integration_package
from nuke_di.integration import Binding, DependsFramework, Framework, bind, running, unique
from nuke_di.integration.testing import Send, check

# --- an integration on the kit, and integrations that break the contract ------------------------------------


class Toy:
    """
    A framework of one handler with `Depends`-like markers, for an integration written on the kit.
    """

    def __init__(self) -> None:
        self.handler: Callable[..., Any] | None = None


class ToyDepends:
    def __init__(self, dependency: Callable[..., Any]) -> None:
        self.dependency = dependency


TOY = DependsFramework(
    name="Toy",
    depends=ToyDepends,
    make_depends=ToyDepends,
    not_started="{client} was not started with the app",
    not_connected="{client} is not connected: start the toy",
)


async def toy_call(call: Callable[..., Any]) -> Any:
    """
    What a framework does with a rewritten signature: fill every `Annotated[..., ToyDepends(...)]`.
    """
    kwargs = {}
    for name, argument in inspect.signature(call).parameters.items():
        kwargs[name] = await toy_call(toy_marker(argument.annotation).dependency)
    return await call(**kwargs)


def toy_marker(annotation: Any) -> ToyDepends:
    extras = get_args(annotation)[1:]
    return next(extra for extra in extras if isinstance(extra, ToyDepends))


@asynccontextmanager
async def toy_run(toy: tuple[Toy, Dependencies], lifespan: bool) -> AsyncIterator[Send]:
    app, container = toy
    assert app.handler is not None
    handler = app.handler
    if lifespan:
        async with running(container, unique(bind(handler, container, TOY))):
            yield lambda: toy_call(handler)
    else:
        bind(handler, container, TOY)
        yield lambda: toy_call(handler)


def toy_app(container: Dependencies, handler: Callable[..., Any]) -> tuple[Toy, Dependencies]:
    app = Toy()
    app.handler = handler
    return app, container


async def test_an_integration_on_the_kit_keeps_the_contract() -> None:
    await check(TOY, toy_app, toy_run)


def notes(group: ExceptionGroup[Exception]) -> list[str]:
    first_notes = [error.__notes__[0] for error in group.exceptions]
    return [note.split(":")[0].removeprefix("Toy integration, case ") for note in first_notes]


async def test_check_reports_every_broken_case() -> None:
    @asynccontextmanager
    async def run_without_container(toy: tuple[Toy, Dependencies], lifespan: bool) -> AsyncIterator[Send]:
        # Forgets to connect the container: every handler fails as if the lifespan never ran
        app, container = toy
        assert app.handler is not None
        handler = app.handler
        bind(handler, container, TOY)
        yield lambda: toy_call(handler)

    with pytest.raises(ExceptionGroup, match="the Toy integration breaks the nuke-di contract") as excinfo:
        await check(TOY, toy_app, run_without_container)

    assert notes(excinfo.value) == ["handler", "dependency", "override", "failed_connect"]


async def test_check_reports_a_handler_that_runs_without_the_lifespan() -> None:
    @asynccontextmanager
    async def run_always_connected(toy: tuple[Toy, Dependencies], lifespan: bool) -> AsyncIterator[Send]:
        async with toy_run(toy, True) as send:
            yield send

    with pytest.raises(ExceptionGroup) as excinfo:
        await check(TOY, toy_app, run_always_connected)

    assert notes(excinfo.value) == ["not_connected"]
    assert "the handler ran without the lifespan" in str(excinfo.value.exceptions[0])


async def test_check_reports_a_framework_that_never_calls_the_handler() -> None:
    @asynccontextmanager
    async def run_nothing(toy: tuple[Toy, Dependencies], lifespan: bool) -> AsyncIterator[Send]:
        yield lambda: None

    with pytest.raises(ExceptionGroup) as excinfo:
        await check(TOY, toy_app, run_nothing)

    assert notes(excinfo.value) == ["handler", "dependency", "override", "not_connected", "failed_connect"]
    assert "expected the events ['store: connected', 'Hello, store!', 'store: disconnected'], got []" in str(
        excinfo.value.exceptions[0]
    )
    assert "the app started; expected a RuntimeError" in str(excinfo.value.exceptions[-1])


async def test_check_reports_a_wrong_error_and_a_container_left_resolved() -> None:
    @asynccontextmanager
    async def run_carelessly(toy: tuple[Toy, Dependencies], lifespan: bool) -> AsyncIterator[Send]:
        app, container = toy
        assert app.handler is not None
        if not lifespan:
            raise ValueError("no lifespan, no app")
        bindings = bind(app.handler, container, TOY)
        if "broken" not in inspect.signature(app.handler).parameters:
            async with toy_run(toy, lifespan) as send:
                yield send
            return
        # Resolves the clients, then gives up before connect() and leaves them in the container
        for binding in bindings:
            container.resolve(binding.client_class)
        raise RuntimeError("nuke-di clients failed to start: Broken.connect() raised OSError: unreachable")

    with pytest.raises(ExceptionGroup) as excinfo:
        await check(TOY, toy_app, run_carelessly)

    assert notes(excinfo.value) == ["not_connected", "failed_connect"]
    assert "got ValueError('no lifespan, no app')" in str(excinfo.value.exceptions[0])
    assert "the container was left connected or not flushed" in str(excinfo.value.exceptions[1])


async def test_check_reports_a_connect_error_let_out() -> None:
    @asynccontextmanager
    async def run_raw(toy: tuple[Toy, Dependencies], lifespan: bool) -> AsyncIterator[Send]:
        app, container = toy
        assert app.handler is not None
        if "broken" not in inspect.signature(app.handler).parameters:
            async with toy_run(toy, lifespan) as send:
                yield send
            return
        # Connects without running(): the ConnectError, a SystemExit, escapes
        for binding in bind(app.handler, container, TOY):
            container.resolve(binding.client_class)
        await container.connect()
        yield lambda: None

    with pytest.raises(ExceptionGroup) as excinfo:
        await check(TOY, toy_app, run_raw)

    assert notes(excinfo.value) == ["failed_connect"]
    assert "expected a RuntimeError" in str(excinfo.value.exceptions[0])


async def test_check_reports_a_system_exit_as_a_failed_case() -> None:
    @asynccontextmanager
    async def run_exiting(toy: tuple[Toy, Dependencies], lifespan: bool) -> AsyncIterator[Send]:
        if lifespan:
            sys.exit(3)
        async with toy_run(toy, lifespan) as send:
            yield send

    with pytest.raises(ExceptionGroup) as excinfo:
        await check(TOY, toy_app, run_exiting)

    assert notes(excinfo.value) == ["handler", "dependency", "override", "failed_connect"]
    assert "a SystemExit escaped the app, which a server cannot report: SystemExit(3)" in str(
        excinfo.value.exceptions[0]
    )


async def test_check_skips_the_dependency_without_depends() -> None:
    plain = Framework(name="Toy", not_started=TOY.not_started, not_connected=TOY.not_connected)
    seen: list[Callable[..., Any]] = []

    def app(container: Dependencies, handler: Callable[..., Any]) -> tuple[Toy, Dependencies]:
        seen.append(handler)
        return toy_app(container, handler)

    await check(plain, app, toy_run)

    assert [handler.__qualname__.split(".")[0] for handler in seen] == [
        "_handler",
        "_override",
        "_not_connected",
        "_failed_connect",
    ]


# --- bind() ------------------------------------------------------------------------------------------------


class Greeter(Client):
    def greet(self) -> str:
        return "hello"


async def greeting(greeter: Greeter) -> str:
    return greeter.greet()


GREETING = ToyDepends(greeting)


# String annotations on purpose: `Missing` cannot be evaluated, as a name imported under TYPE_CHECKING
async def wrapped(  # type: ignore[no-untyped-def]
    missing: "Missing",  # type: ignore[name-defined]  # noqa: F821
    greeter: "Greeter",
    text: "str" = GREETING,  # type: ignore[assignment]
    untyped=None,
) -> "Missing":  # type: ignore[name-defined]  # noqa: F821
    return text


async def test_bind_evaluates_each_hint_on_its_own() -> None:
    # One hint that cannot be evaluated leaves only itself as written: the clients of the dependencies are
    # still found
    container = Dependencies()

    bindings = bind(wrapped, container, TOY)

    # The dependency's client, then its own
    assert [binding.client_class for binding in bindings] == [Greeter, Greeter]
    async with running(container, bindings):
        assert await toy_call(greeting) == "hello"
    signature = inspect.signature(wrapped)
    left_as_written = (signature.parameters["missing"].annotation, signature.return_annotation)
    assert left_as_written == ("Missing", "Missing")
    assert signature.parameters["text"].annotation is str
    assert get_origin(signature.parameters["greeter"].annotation) is Annotated


class _Unreadable(type):
    @property
    def __signature__(cls) -> inspect.Signature:
        # What Python 3.14 raises for a signature whose annotations name what does not exist, evaluated
        # when read without `from __future__ import annotations`
        raise NameError("name 'Missing' is not defined")


class Unreadable(metaclass=_Unreadable):
    def __init__(self, greeter: Greeter) -> None: ...


def test_bind_leaves_an_unreadable_signature_to_the_framework() -> None:
    assert bind(Unreadable, Dependencies(), TOY) == []


async def test_getter_replaces_what_a_marker_calls() -> None:
    async def get() -> str:
        return "from the getter"

    framework = DependsFramework(
        name="Toy",
        depends=ToyDepends,
        make_depends=ToyDepends,
        not_started=TOY.not_started,
        not_connected=TOY.not_connected,
        _getter=lambda binding: get,
    )

    async def handler(greeter: Greeter) -> object:
        return greeter

    bind(handler, Dependencies(), framework)

    assert await toy_call(handler) == "from the getter"


def test_bind_binds_a_function_again_for_another_container() -> None:
    async def handler(greeter: Greeter) -> str:
        return greeter.greet()

    first, second = Dependencies(), Dependencies()
    bound = bind(handler, first, TOY)

    assert bind(handler, first, TOY) is bound
    assert [binding.container() for binding in bind(handler, second, TOY)] == [second]


# --- running() ---------------------------------------------------------------------------------------------


async def test_running_forgets_the_bindings_when_disconnect_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def handler(greeter: Greeter) -> str:
        return greeter.greet()

    async def failing_disconnect() -> None:
        raise RuntimeError("disconnect failed")

    container = Dependencies()
    bindings = bind(handler, container, TOY)
    with pytest.raises(RuntimeError, match="disconnect failed"):
        async with running(container, bindings):
            monkeypatch.setattr(container, "disconnect", failing_disconnect)

    assert [binding.instance for binding in bindings] == [None]


# --- the public names ------------------------------------------------------------------------------------


def test_public_names() -> None:
    assert set(integration_package.__all__) == {
        "Binding",
        "DependsFramework",
        "Framework",
        "bind",
        "client_of",
        "running",
        "unique",
        "wrap_lifespan",
    }
    assert Binding is integration_package.Binding
