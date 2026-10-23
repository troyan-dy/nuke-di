"""
The check of the contract: every case run against one integration, the cases that failed raised together.
"""

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, TypeAlias

from nuke_di.core import Dependencies
from nuke_di.integration import DependsFramework, Framework
from nuke_di.integration.testing.cases import _dependency, _failed_connect, _handler, _not_connected, _override
from nuke_di.integration.testing.outcomes import Send, _events

# A case of the contract, called with (framework, make_app, run)
_Case: TypeAlias = Callable[[Framework, Any, Any], Awaitable[None]]


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
    cases: list[_Case] = [_handler, _override, _not_connected, _failed_connect]
    if isinstance(framework, DependsFramework):
        cases.insert(1, _dependency)
    errors: list[Exception] = []
    for case in cases:
        _events.clear()
        try:
            # The cases share their clients, so they run one at a time
            await _run_case(case, framework, make_app, run)  # noqa: WPS476
        except Exception as exc:
            name = case.__name__.lstrip("_")
            exc.add_note(f"{framework.name} integration, case {name}: {case.__doc__}")
            errors.append(exc)
    if errors:
        raise ExceptionGroup(f"the {framework.name} integration breaks the nuke-di contract", errors)


async def _run_case(case: _Case, framework: Framework, make_app: Any, run: Any) -> None:
    try:
        await case(framework, make_app, run)
    except SystemExit as exc:
        # A ConnectError is a SystemExit: an integration that lets it out breaks the contract, not the check
        raise AssertionError(f"a SystemExit escaped the app, which a server cannot report: {exc!r}") from exc
