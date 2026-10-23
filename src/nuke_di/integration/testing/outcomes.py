"""
What a case of the contract observes: the events of its clients and handlers, and the errors the app raised.
"""

import inspect
from collections.abc import Callable

# Calls the handler once through the app and raises what the handler raised; what it returns is awaited if
# it is awaitable, and ignored otherwise
Send = Callable[[], object]

# What the clients and handlers of the running case did; the cases run one at a time
_events: list[str] = []


async def _send(send: Send) -> None:
    outcome = send()
    if inspect.isawaitable(outcome):
        await outcome


def _expect(events: list[str], expected: list[str]) -> None:
    if events != expected:
        raise AssertionError(f"expected the events {expected}, got {events}")


def _failed_start(error: BaseException) -> bool:
    # Not the whole message: its wording past the prefix belongs to the container
    message = str(error)
    return (
        isinstance(error, RuntimeError)
        and message.startswith("nuke-di clients failed to start")
        and "Broken" in message
        and "unreachable" in message
    )


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
        linked = (error.__cause__, error.__context__)
        pending.extend(other for other in linked if other is not None)
    return found
