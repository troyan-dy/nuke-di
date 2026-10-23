"""
The client arguments of a handler, a filter or a startup handler, as aiogram reads its signature.
"""

import functools
import inspect
from collections.abc import Mapping
from typing import Any, ForwardRef, TypeAlias, get_type_hints

from nuke_di.aiogram.framework import _SIGNATURE
from nuke_di.integration import client_of
from nuke_di.types import NotSingletonClient

# A client argument: its name and its client
_ClientArgument: TypeAlias = tuple[str, type[NotSingletonClient]]


def _client_arguments(call: Any) -> list[_ClientArgument]:
    """
    The arguments of `call` whose type hint is a client, as aiogram reads them: through `functools.wraps`, and
    without the arguments a `functools.partial` binds.
    """
    target = inspect.unwrap(call)
    function = _function_of(target)
    if function is None:
        return []
    try:
        arguments = inspect.signature(target, **_SIGNATURE).parameters
    except (TypeError, ValueError):  # pragma: no cover - aiogram cannot read such a signature either
        return []
    return _clients_among(arguments, _hints(function, arguments))


def _function_of(target: Any) -> Any:
    """
    The function whose type hints the arguments of `target` have, or `None` for a class.
    """
    function = inspect.unwrap(target.func) if isinstance(target, functools.partial) else target
    if inspect.ismethod(function):
        return function.__func__
    if inspect.isfunction(function):
        return function
    if inspect.isclass(function) or not callable(function):
        # aiogram's class-based handlers take what they need from `self.data`
        return None
    # A callable object, e.g. a `Filter`
    return type(function).__call__


def _clients_among(
    names: Mapping[str, Any],
    hints: dict[str, Any],
) -> list[_ClientArgument]:
    found = []
    for name in names:
        client = client_of(hints.get(name))
        if client is not None:
            found.append((name, client))
    return found


def _hints(function: Any, arguments: Mapping[str, inspect.Parameter]) -> dict[str, Any]:
    """
    The type hints of `function`, without those that do not evaluate: a name imported under TYPE_CHECKING, or any
    other error. aiogram does not read type hints, it passes by name, so it copes with them.
    """
    try:
        return get_type_hints(function, include_extras=True)
    except Exception:
        # Whatever a string annotation raises when it is evaluated
        hints = {}
        for name, argument in arguments.items():
            hint = _hint(function, argument.annotation)
            if hint is not None:
                hints[name] = hint
        return hints


class _Annotation:
    """
    One annotation of a function, for get_type_hints() to evaluate alone.
    """

    def __init__(self, function: Any, annotation: str) -> None:
        self.__annotations__ = {"hint": annotation}
        self.__globals__ = getattr(function, "__globals__", {})


def _hint(function: Any, annotation: Any) -> Any:
    if isinstance(annotation, ForwardRef):
        annotation = annotation.__forward_arg__
    if not isinstance(annotation, str):
        return annotation
    try:
        return get_type_hints(_Annotation(function, annotation), include_extras=True)["hint"]
    except Exception:
        return None
