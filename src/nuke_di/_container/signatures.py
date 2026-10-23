"""
What the container reads from signatures: the clients an `__init__` takes, and the type hints of an injected
function.
"""

import inspect
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, get_type_hints

from nuke_di._container.arguments import VARIADIC, argument_problem, init_arguments
from nuke_di._container.messages import hints_error, names, signature_error
from nuke_di.errors import InvalidSignatureError
from nuke_di.naming import sname
from nuke_di.types import NotSingletonClient, isnotsingleton

if TYPE_CHECKING:
    from nuke_di.core import Dependencies


def client_arguments(
    container: "Dependencies",
    client_class: type[NotSingletonClient],
) -> dict[str, type[NotSingletonClient]]:
    """
    The arguments of `client_class.__init__` to fill with clients; fail on any other required argument.

    Read once per class in the process: the result depends on `__init__` alone, so it is kept on the class
    beside the `__init__` it was read from, and read again when the class gets another `__init__`.
    """
    init = client_class.__init__
    if init is object.__init__:
        # `inspect.signature` parses the text signature of a slot wrapper on every call
        return {}

    # A subclass that inherits `__init__` finds its base class's entry and shares it; one that redefines
    # `__init__` fails the check below and stores its own
    entry: tuple[Callable[..., Any], dict[str, type[NotSingletonClient]]] | None = getattr(
        client_class, "__nuke_di_arguments__", None
    )
    if entry is not None and entry[0] is init:
        return entry[1]

    try:
        hints = get_type_hints(init)
    except NameError as exc:
        raise _init_hints_error(container, client_class, exc) from exc
    arguments = _read_arguments(container, client_class, init, hints)
    # Only a success is kept: a type hint that fails to evaluate must fail on the next resolve too
    try:
        client_class.__nuke_di_arguments__ = (init, arguments)  # type: ignore[attr-defined]
    except AttributeError:
        # A metaclass that forbids setting attributes: the class is read again on the next resolve
        return arguments
    return arguments


def client_hints(container: "Dependencies", func: Callable[..., Any]) -> dict[str, Any]:
    """
    The type hints of the arguments of `func`; fail on an argument without one.
    """
    # get_type_hints silently skips unannotated arguments, so they are looked up in the signature
    for argument in inspect.signature(func).parameters.values():
        if argument.kind in VARIADIC:
            continue
        if argument.annotation is inspect.Parameter.empty:
            raise InvalidSignatureError(f'Argument "{argument.name}" of "{sname(func)}" has no type hint')

    try:
        hints: dict[str, Any] = get_type_hints(func)
    except NameError as exc:
        raise hints_error(container, sname(func), exc) from exc
    hints.pop("return", None)
    return hints


def _read_arguments(
    container: "Dependencies",
    client_class: type[NotSingletonClient],
    init: Callable[..., Any],
    hints: dict[str, Any],
) -> dict[str, type[NotSingletonClient]]:
    clients: dict[str, type[NotSingletonClient]] = {}
    for argument in init_arguments(init):
        hint: Any = hints.get(argument.name)
        # The common case first, without a call: a client passed by keyword
        if isnotsingleton(hint) and not argument.positional_only:
            clients[argument.name] = hint
            continue
        problem = argument_problem(argument, hint)
        if problem is not None:
            raise signature_error(container, client_class, argument.name, problem)
    return clients


def _init_hints_error(
    container: "Dependencies",
    client_class: type[NotSingletonClient],
    exc: NameError,
) -> InvalidSignatureError:
    owner = names(container, client_class)[client_class]
    return hints_error(container, f"{owner}.__init__", exc)
