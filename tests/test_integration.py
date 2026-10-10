"""
The public integration kit: the contract check against an integration written on it, and against integrations
that break the contract. The integrations of this package run the check in their own test files.
"""

import inspect
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, get_args

import pytest

import nuke_di.integration
from nuke_di import Dependencies
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
    for name, param in inspect.signature(call).parameters.items():
        marker = next(item for item in get_args(param.annotation)[1:] if isinstance(item, ToyDepends))
        kwargs[name] = await toy_call(marker.dependency)
    return await call(**kwargs)


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
    return [error.__notes__[0].split(":")[0].removeprefix("Toy integration, case ") for error in group.exceptions]


async def test_check_reports_every_broken_case() -> None:
    @asynccontextmanager
    async def run_without_container(toy: tuple[Toy, Dependencies], lifespan: bool) -> AsyncIterator[Send]:
        # Forgets to connect the container: every handler fails as if the lifespan never ran
        app, container = toy
        assert app.handler is not None
        handler = app.handler
        bind(handler, container, TOY)
        yield lambda: toy_call(handler)

    with pytest.raises(ExceptionGroup, match="the Toy integration breaks the nuke-di contract") as info:
        await check(TOY, toy_app, run_without_container)

    assert notes(info.value) == ["handler", "dependency", "override", "failed_connect"]


async def test_check_reports_a_handler_that_runs_without_the_lifespan() -> None:
    @asynccontextmanager
    async def run_always_connected(toy: tuple[Toy, Dependencies], lifespan: bool) -> AsyncIterator[Send]:
        async with toy_run(toy, True) as send:
            yield send

    with pytest.raises(ExceptionGroup) as info:
        await check(TOY, toy_app, run_always_connected)

    assert notes(info.value) == ["not_connected"]
    assert "the handler ran without the lifespan" in str(info.value.exceptions[0])


async def test_check_reports_a_framework_that_never_calls_the_handler() -> None:
    @asynccontextmanager
    async def run_nothing(toy: tuple[Toy, Dependencies], lifespan: bool) -> AsyncIterator[Send]:
        yield lambda: None

    with pytest.raises(ExceptionGroup) as info:
        await check(TOY, toy_app, run_nothing)

    assert notes(info.value) == ["handler", "dependency", "override", "not_connected", "failed_connect"]
    assert "expected the events ['store: connected', 'Hello, store!', 'store: disconnected'], got []" in str(
        info.value.exceptions[0]
    )
    assert "the app started; expected a RuntimeError" in str(info.value.exceptions[-1])


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
            container.resolve(binding.cls)
        raise RuntimeError("nuke-di clients failed to start: Broken.connect() raised OSError: unreachable")

    with pytest.raises(ExceptionGroup) as info:
        await check(TOY, toy_app, run_carelessly)

    assert notes(info.value) == ["not_connected", "failed_connect"]
    wrong, left = info.value.exceptions
    assert "got ValueError('no lifespan, no app')" in str(wrong)
    assert "the container was left connected or not flushed" in str(left)


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
            container.resolve(binding.cls)
        await container.connect()
        yield lambda: None  # pragma: no cover

    with pytest.raises(ExceptionGroup) as info:
        await check(TOY, toy_app, run_raw)

    assert notes(info.value) == ["failed_connect"]
    assert "expected a RuntimeError" in str(info.value.exceptions[0])


async def test_check_reports_a_system_exit_as_a_failed_case() -> None:
    @asynccontextmanager
    async def run_exiting(toy: tuple[Toy, Dependencies], lifespan: bool) -> AsyncIterator[Send]:
        if lifespan:
            raise SystemExit(3)
        async with toy_run(toy, lifespan) as send:
            yield send

    with pytest.raises(ExceptionGroup) as info:
        await check(TOY, toy_app, run_exiting)

    assert notes(info.value) == ["handler", "dependency", "override", "failed_connect"]
    assert "a SystemExit escaped the app, which a server cannot report: SystemExit(3)" in str(info.value.exceptions[0])


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


# --- the public names ------------------------------------------------------------------------------------


def test_public_names() -> None:
    assert set(nuke_di.integration.__all__) == {
        "Binding",
        "DependsFramework",
        "Framework",
        "bind",
        "client_of",
        "running",
        "unique",
        "wrap_lifespan",
    }
    assert Binding is nuke_di.integration.Binding
