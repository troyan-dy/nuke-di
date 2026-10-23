"""
The arguments of a client's `__init__`, as much of its signature as the container needs, and what is wrong with one.
"""

import inspect
from collections.abc import Callable
from typing import Any, Final, NamedTuple

from nuke_di._container.hints import optional_client, type_name
from nuke_di.naming import qualname
from nuke_di.types import isnotsingleton

# `*args` and `**kwargs`: never filled by the container
VARIADIC: Final = frozenset((inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD))


class Argument(NamedTuple):
    """
    One argument of an `__init__`, as much of its signature as the container needs.
    """

    name: str
    positional_only: bool
    has_default: bool


def init_arguments(init: Callable[..., Any]) -> list[Argument]:
    """
    The arguments of `init` after `self`, without `*args` and `**kwargs`.
    """
    # A plain function is read from its code object, which is what `inspect.signature` does at thirty times the cost;
    # a decorated one (`__wrapped__`), a declared signature or a C function go through `inspect.signature`
    if inspect.isfunction(init) and "__wrapped__" not in init.__dict__ and "__signature__" not in init.__dict__:
        code = init.__code__
        # `self` is the first argument, unless the method takes it through `*args`
        start = 1 if code.co_argcount or not code.co_flags & inspect.CO_VARARGS else 0
        # The defaults are the last positional arguments'; the keyword-only arguments' are kept by name
        first_default = code.co_argcount - len(init.__defaults__ or ())
        return [
            Argument(
                name,
                index < code.co_posonlyargcount,
                index >= first_default if index < code.co_argcount else name in (init.__kwdefaults__ or ()),
            )
            for index, name in enumerate(code.co_varnames[: code.co_argcount + code.co_kwonlyargcount])
            if index >= start
        ]
    return _signature_arguments(init)


def argument_problem(argument: Argument, hint: Any) -> str | None:
    """
    Why the container cannot fill `argument`, whose type hint is `hint`; None for a client, and for an argument
    with a default, which is not the container's to fill.
    """
    if isnotsingleton(hint):
        return "is positional-only, a client is passed by keyword" if argument.positional_only else None
    if argument.has_default:
        # Not ours to fill: the default stays
        return None
    if hint is None:
        return "has no type hint"
    client = optional_client(hint)
    if client is not None:
        return f"is {qualname(client)} | None, a client cannot be optional"
    return f"is {type_name(hint)}, which is not a client"


def _signature_arguments(init: Callable[..., Any]) -> list[Argument]:
    signature_arguments = list(inspect.signature(init).parameters.values())[1:]
    return [
        Argument(argument.name, argument.kind is argument.POSITIONAL_ONLY, argument.default is not argument.empty)
        for argument in signature_arguments
        if argument.kind not in VARIADIC
    ]
