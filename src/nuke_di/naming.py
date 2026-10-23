from collections import Counter
from collections.abc import Iterable
from typing import Any


def sname(target: Any) -> str:
    try:
        return target.__name__  # type: ignore[no-any-return]
    except AttributeError:
        return sname(target.__class__)


def qualname(target: type) -> str:
    """
    The name of a class as written in code: a nested class keeps its outer class (`Outer.Inner`), a class local
    to a function drops the function, which is not a namespace a reader looks the class up in.
    """
    return target.__qualname__.rsplit("<locals>.", 1)[-1]


def display_names(classes: Iterable[type]) -> dict[type, str]:
    """
    How errors, logs and timings name every class of `classes`: by `qualname()`, and in full, module and
    `__qualname__` (`app.orders.Database`, `tests.test_x.test_one.<locals>.Database`), for the classes that share
    one, so two wrappers called `Database` are told apart. A unique name stays short.
    """
    names = {client_class: qualname(client_class) for client_class in classes}
    shared = _shared(names.values())
    return {
        client_class: f"{client_class.__module__}.{client_class.__qualname__}" if name in shared else name
        for client_class, name in names.items()
    }


def _shared(names: Iterable[str]) -> set[str]:
    """
    The names of `names` that occur more than once.
    """
    occurrences = Counter(names)
    return {name for name, seen in occurrences.items() if seen > 1}


def count(number: int, noun: str) -> str:
    """
    `number` of `noun` as a record writes it: `1 client`, `3 clients`.
    """
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"
