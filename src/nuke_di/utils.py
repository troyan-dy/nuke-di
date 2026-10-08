import inspect
from collections.abc import Callable
from typing import Any


def sname(obj: Any) -> str:
    try:
        return obj.__name__  # type: ignore[no-any-return]
    except AttributeError:
        return sname(obj.__class__)


def isa(*types: type) -> Callable[[Any], bool]:
    return lambda o: inspect.isclass(o) and issubclass(o, types)
