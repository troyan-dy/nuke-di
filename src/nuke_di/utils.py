import inspect
from collections.abc import Callable, Iterable, Iterator
from typing import Any


def iteritems(col: Any) -> Any:
    if isinstance(col, dict):
        return col.items()
    return col


def select_values(pred: Callable[[Any], bool], col: Iterable) -> Iterator:
    return (i for i in iteritems(col) if pred(i[1]))


def walk_values(prim: Callable[[Any], Any], col: Iterable) -> Iterator:
    return ((k, prim(v)) for k, v in iteritems(col))


def sname(obj: Any) -> str:
    try:
        return obj.__name__  # type: ignore[no-any-return]
    except AttributeError:
        return sname(obj.__class__)


def isa(*types: type) -> Callable[[Any], bool]:
    return lambda o: inspect.isclass(o) and issubclass(o, types)
