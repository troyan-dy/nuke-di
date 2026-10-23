"""
The cases of the contract, each a fresh app with a handler of its own; the docstring of a case is the note of
its failure.
"""

from typing import Annotated, Any

from nuke_di.core import Dependencies
from nuke_di.integration import DependsFramework, Framework
from nuke_di.integration.testing.clients import Broken, Greeter, ReplacementStore, Store
from nuke_di.integration.testing.outcomes import _chain, _events, _expect, _failed_start, _send


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
