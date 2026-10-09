"""
The contract every framework integration keeps, as one call for the integration's own tests.

See docs/guide/integrations.md.
"""

import inspect
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Annotated, Any

from nuke_di.core import Dependencies
from nuke_di.integration import DependsFramework, Framework
from nuke_di.types import Client

__all__ = ("Send", "check")

# Calls the handler once through the app and raises what the handler raised; what it returns is awaited if
# it is awaitable, and ignored otherwise
Send = Callable[[], object]


async def check(
    framework: Framework,
    make_app: Callable[[Dependencies, Callable[..., Any]], Any],
    run: Callable[[Any, bool], AbstractAsyncContextManager[Send]],
) -> None:
    """
    Check that an integration fills client arguments and runs the container with the app.

    `make_app(container, handler)` returns a new app set up with `container` that serves `handler`, an `async def`
    with no arguments but clients. `run(app, lifespan)` is an async context manager that runs the app, with
    its lifespan only when `lifespan` is true, and yields a `send()` that calls the handler once through the
    framework and raises what the handler raised. With a `DependsFramework`, a dependency of the handler, in
    `Annotated[..., framework.make_depends(dependency)]`, takes a client too.

    Raises an `ExceptionGroup` of the cases that failed, each with a note naming its case. The cases share
    their clients, so two checks do not run at once.
    """
    cases = [_handler, _override, _not_connected, _failed_connect]
    if isinstance(framework, DependsFramework):
        cases.insert(1, _dependency)
    errors: list[Exception] = []
    for case in cases:
        _events.clear()
        try:
            try:
                await case(framework, make_app, run)
            except SystemExit as exc:
                # A ConnectError is a SystemExit: an integration that lets it out breaks the contract, not the check
                raise AssertionError(f"a SystemExit escaped the app, which a server cannot report: {exc!r}") from exc
        except Exception as exc:
            exc.add_note(f"{framework.name} integration, case {case.__name__.lstrip('_')}: {case.__doc__}")
            errors.append(exc)
    if errors:
        raise ExceptionGroup(f"the {framework.name} integration breaks the nuke-di contract", errors)


# What the clients and handlers of the running case did; the cases run one at a time
_events: list[str] = []


class Store(Client):
    async def connect(self) -> None:
        _events.append("store: connected")

    async def disconnect(self) -> None:
        _events.append("store: disconnected")

    def name(self) -> str:
        return "store"


class ReplacementStore(Store):
    def name(self) -> str:
        return "replacement"


class Greeter(Client):
    def __init__(self, store: Store) -> None:
        self.store = store

    def greet(self) -> str:
        return f"Hello, {self.store.name()}!"


class Broken(Client):
    async def connect(self) -> None:
        raise OSError("unreachable")


async def _send(send: Send) -> None:
    result = send()
    if inspect.isawaitable(result):
        await result


async def _handler(framework: Framework, make_app: Any, run: Any) -> None:
    """a handler takes a client by type hint, connected for the time the app runs"""

    async def handler(greeter: Greeter) -> None:
        _events.append(greeter.greet())

    async with run(make_app(Dependencies(), handler), True) as send:
        await _send(send)
    _expect(_events, ["store: connected", "Hello, store!", "store: disconnected"])


async def _dependency(framework: Framework, make_app: Any, run: Any) -> None:
    """a dependency of a handler takes a client by type hint"""
    assert isinstance(framework, DependsFramework)

    async def greeting(greeter: Greeter) -> str:
        return greeter.greet()

    async def handler(text: Annotated[str, framework.make_depends(greeting)]) -> None:
        _events.append(text)

    async with run(make_app(Dependencies(), handler), True) as send:
        await _send(send)
    _expect(_events, ["store: connected", "Hello, store!", "store: disconnected"])


async def _override(framework: Framework, make_app: Any, run: Any) -> None:
    """override() before startup replaces a client the handler gets through another client"""

    async def handler(greeter: Greeter) -> None:
        _events.append(greeter.greet())

    container = Dependencies()
    # After the app is set up: an integration resolves the clients on startup, not before
    app = make_app(container, handler)
    with container.override(Store, ReplacementStore()):
        async with run(app, True) as send:
            await _send(send)
    _expect(_events, ["Hello, replacement!"])


async def _not_connected(framework: Framework, make_app: Any, run: Any) -> None:
    """a handler called without the app's lifespan raises the framework's "not connected" error"""

    async def handler(greeter: Greeter) -> None:
        _events.append(greeter.greet())  # pragma: no cover

    expected = framework.not_connected.format(client="Greeter")
    try:
        async with run(make_app(Dependencies(), handler), False) as send:
            await _send(send)
    except Exception as exc:
        if not any(expected in str(error) for error in _chain(exc)):
            raise AssertionError(f"expected an error with {expected!r}, got {exc!r}") from exc
    else:
        raise AssertionError(f"the handler ran without the lifespan; expected an error with {expected!r}")
    _expect(_events, [])


async def _failed_connect(framework: Framework, make_app: Any, run: Any) -> None:
    """a failed connect() fails the app's startup with a RuntimeError and leaves the container flushed"""

    async def handler(broken: Broken) -> None:
        _events.append("handler")  # pragma: no cover

    container = Dependencies()
    expected = "nuke-di clients failed to start"
    try:
        async with run(make_app(container, handler), True) as send:
            await _send(send)
    except (Exception, SystemExit) as exc:
        if not any(_failed_start(error) for error in _chain(exc)):
            raise AssertionError(f"expected a RuntimeError with {expected!r} about Broken, got {exc!r}") from exc
    else:
        raise AssertionError(f"the app started; expected a RuntimeError with {expected!r}")
    if container.connected or container.clients:
        raise AssertionError("the container was left connected or not flushed after the failed startup")
    _expect(_events, [])


def _failed_start(error: BaseException) -> bool:
    # Not the whole message: its wording past the prefix belongs to the container
    message = str(error)
    return (
        isinstance(error, RuntimeError)
        and message.startswith("nuke-di clients failed to start")
        and "Broken" in message
        and "unreachable" in message
    )


def _expect(events: list[str], expected: list[str]) -> None:
    if events != expected:
        raise AssertionError(f"expected the events {expected}, got {events}")


def _chain(exc: BaseException) -> list[BaseException]:
    """
    `exc`, its causes and contexts, and the members of exception groups among them: a framework may wrap the
    error of a handler or of a startup.
    """
    found: list[BaseException] = []
    pending = [exc]
    while pending:
        error = pending.pop()
        if any(error is seen for seen in found):
            continue
        found.append(error)
        if isinstance(error, BaseExceptionGroup):
            pending.extend(error.exceptions)
        pending.extend(item for item in (error.__cause__, error.__context__) if item is not None)
    return found
