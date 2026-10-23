"""
Trees of no-op clients: the classes a benchmark resolves, made the way a user writes them.
"""

import itertools
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, make_dataclass
from types import ModuleType
from typing import cast

from nuke_di import Client, NotSingletonClient


@dataclass
class Tree:
    root: type[Client]
    # Every client of the tree, dependencies before their consumers; the first one is a leaf
    clients: list[type[Client]]
    # The module of the classes when their type hints are strings, see client(); None for real type hints
    module: ModuleType | None = None

    def discard(self) -> None:
        """
        Unregister the module, so the trees built per sample do not pile up in `sys.modules`; the generated
        `__init__` keeps a reference to its globals, which is all `get_type_hints()` needs afterwards.
        """
        if self.module is not None:
            sys.modules.pop(self.module.__name__)


def client(
    name: str,
    deps: Sequence[type[NotSingletonClient]] = (),
    base: type = Client,
    module: ModuleType | None = None,
) -> type[Client]:
    """
    A client class whose `__init__` takes `deps` by type hint, as a user would write it.

    With `module`, the type hints are the names of the dependencies as strings, which is what
    `from __future__ import annotations` makes of every annotation, and the class belongs to `module`:
    `dataclass` builds `__init__` with the globals of the class's module, which is where `get_type_hints()`
    evaluates the strings later, so the module holds every class of the tree under its name.
    """
    if module is None:
        fields = [(f"dep{number}", dependency) for number, dependency in enumerate(deps)]
        made = make_dataclass(name, fields, bases=(base,), eq=False, repr=False)
    else:
        made = _in_module(name, deps, base, module)
    return cast(type[Client], made)


def _in_module(
    name: str,
    deps: Sequence[type[NotSingletonClient]],
    base: type,
    module: ModuleType,
) -> type:
    """
    The class of client(), its type hints the names of `deps`, made in `module` and kept there under `name`.
    """
    strings = [(f"dep{number}", dependency.__name__) for number, dependency in enumerate(deps)]
    if sys.version_info >= (3, 12):
        made = make_dataclass(
            name,
            strings,
            bases=(base,),
            eq=False,
            repr=False,
            module=module.__name__,
        )
    else:
        # 3.11 has no `module` argument and takes the module from the namespace
        made = make_dataclass(
            name,
            strings,
            bases=(base,),
            eq=False,
            repr=False,
            namespace={"__module__": module.__name__},
        )
    setattr(module, name, made)
    return made


_modules = itertools.count()


def new_module(strings: bool) -> ModuleType | None:
    """
    Where the classes of one tree are made: nowhere for real type hints; for string hints a module of their
    own, registered in `sys.modules` as the module of a real code base is, because `dataclass` finds the
    globals of `__init__` there, and wireup imports the module of a class to evaluate its hints.
    """
    if not strings:
        return None
    module = ModuleType(f"benchmarks.strings{next(_modules)}")
    sys.modules[module.__name__] = module
    return module


@contextmanager
def built(build: Callable[[], Tree]) -> Iterator[Tree]:
    """
    The tree `build` makes, discarded after the block.
    """
    tree = build()
    try:
        yield tree
    finally:
        tree.discard()


def shape_label(shape: str, strings: bool) -> str:
    """
    The Shape column of a tree: `wide, strings` for the string-annotation variant.
    """
    return f"{shape}, strings" if strings else shape
