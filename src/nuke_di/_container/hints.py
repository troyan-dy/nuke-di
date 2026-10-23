"""
Type hints as the container's messages write them, and the errors that name one.
"""

import inspect
from types import NoneType, UnionType
from typing import Any, Final, Union, get_args, get_origin

from nuke_di._container.messages import state_error
from nuke_di.errors import ConnectError, InvalidSignatureError
from nuke_di.naming import qualname
from nuke_di.types import NotSingletonClient, isnotsingleton

# The origins of `X | Y` and of `Union[X, Y]`
_UNIONS: Final = frozenset((Union, UnionType))


def type_name(hint: Any) -> str:
    """
    A type hint as written in code: `int`, `list[int]`, `Database | int`, without module prefixes.
    """
    # Compared by identity: a hint may be unhashable, `resolve({})`
    if hint is NoneType:
        return "None"
    if hint is Ellipsis:
        return "..."
    if isinstance(hint, list):
        # The parameters of a Callable
        listed = ", ".join(map(type_name, hint))
        return f"[{listed}]"
    return _parameterized_name(hint)


def _parameterized_name(hint: Any) -> str:
    origin = get_origin(hint)
    if origin in _UNIONS:
        return " | ".join(map(type_name, get_args(hint)))
    if origin is not None:
        arguments = ", ".join(map(type_name, get_args(hint))) or "()"
        return f"{type_name(origin)}[{arguments}]"
    if inspect.isclass(hint):
        return qualname(hint)
    return repr(hint).replace("typing.", "")


def optional_client(hint: Any) -> type[NotSingletonClient] | None:
    if get_origin(hint) not in _UNIONS:
        return None
    members = get_args(hint)
    clients = [member for member in members if isnotsingleton(member)]
    return clients[0] if NoneType in members and clients else None


def not_a_client(client_class: Any) -> InvalidSignatureError:
    return InvalidSignatureError(f"{type_name(client_class)} is not a client: subclass Client or NotSingletonClient")


def resolve_refused(client_class: Any) -> ConnectError:
    # `type_name`, not `qualname`: `client_class` may be no class at all
    return state_error(f"resolve({type_name(client_class)})")
