"""
The signature and the type hints of a function as an integration reads them, and the markers in them.
"""

import inspect
import types
from collections.abc import Callable, Iterable
from contextlib import suppress
from importlib import import_module
from importlib.util import find_spec
from typing import Annotated, Any, get_args, get_origin, get_type_hints

# Python 3.14+ evaluates annotations when they are read: this format keeps a name it cannot evaluate as a
# ForwardRef instead of raising NameError
_FORWARDREF: dict[str, Any] = (
    {"annotation_format": import_module("annotationlib").Format.FORWARDREF} if find_spec("annotationlib") else {}
)


def _signature(call: Callable[..., Any]) -> inspect.Signature | None:
    """
    The signature of `call`. One whose annotations name what cannot be evaluated keeps those names as
    ForwardRefs on Python 3.14, where reading a signature evaluates them; `None` if it cannot be read even so.
    """
    with suppress(NameError):
        return inspect.signature(call)
    try:
        return inspect.signature(call, **_FORWARDREF)
    except NameError:
        # Before Python 3.14, or not even as ForwardRefs: the framework reports it
        return None


def _hints(init: Callable[..., Any], signature: inspect.Signature) -> dict[str, Any]:
    """
    The type hints of `init`, each evaluated on its own: one that names what cannot be evaluated, e.g. a name
    imported under TYPE_CHECKING, is left as written, and the others still show their clients and markers.
    """
    with suppress(NameError):
        return get_type_hints(init, include_extras=True)

    annotations = {argument.name: argument.annotation for argument in signature.parameters.values()}
    annotations["return"] = signature.return_annotation
    hints: dict[str, Any] = {}
    for name, annotation in annotations.items():
        if annotation is not inspect.Parameter.empty:
            hints.update(_probe_hint(init, name, annotation))
    return hints


def _probe_hint(
    init: Callable[..., Any],
    name: str,
    annotation: Any,
) -> dict[str, Any]:
    """
    `{name: hint}`, the hint `annotation` evaluates to in the namespace of `init`, or `annotation` as written.
    """
    namespace = getattr(inspect.unwrap(init), "__globals__", {})
    # A function of its own for each hint, evaluated in the namespace of `init`
    probe = types.FunctionType(_probe.__code__, namespace)
    probe.__annotations__ = {name: annotation}
    try:
        return get_type_hints(probe, include_extras=True)
    except NameError:
        return {name: annotation}


def _probe() -> None:  # pragma: no cover
    """
    The code of the function `_hints()` evaluates each type hint on: it is never called.
    """


def _hint_class(hint: Any) -> Any:
    """
    The class an `Annotated[Class, ...]` hint names.
    """
    return get_args(hint)[0] if get_origin(hint) is Annotated else hint


def _depends(argument: inspect.Parameter, hint: Any, marker: type) -> Any:
    if isinstance(argument.default, marker):
        return argument.default
    if get_origin(hint) is Annotated:
        return _first_marker(get_args(hint)[1:], marker)
    return None


def _first_marker(extras: Iterable[Any], marker: type) -> Any:
    """
    The first instance of `marker` among `extras`, the metadata of an `Annotated[...]` hint, or `None`.
    """
    return next((extra for extra in extras if isinstance(extra, marker)), None)
