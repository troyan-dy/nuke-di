import inspect
from collections.abc import Callable
from typing import Any


def sname(obj: Any) -> str:
    try:
        return obj.__name__  # type: ignore[no-any-return]
    except AttributeError:
        return sname(obj.__class__)


def qualname(cls: type) -> str:
    """
    The name of a class as written in code: a nested class keeps its outer class (`Outer.Inner`), a class local
    to a function drops the function, which is not a namespace a reader looks the class up in.
    """
    return cls.__qualname__.rsplit("<locals>.", 1)[-1]


def isa(*types: type) -> Callable[[Any], bool]:
    return lambda o: inspect.isclass(o) and issubclass(o, types)
