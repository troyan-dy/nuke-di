"""
The shapes of the trees: wide, deep and mixed, each of a given number of clients.
"""

from collections.abc import Mapping
from types import MappingProxyType, ModuleType
from typing import Final, Protocol

from nuke_di import Client
from suite.trees import Tree, client, new_module


class Shape(Protocol):
    def __call__(self, size: int, strings: bool = False) -> Tree: ...


def wide(size: int, strings: bool = False) -> Tree:
    """
    One root that declares `size - 1` clients with no dependencies: a tree two clients deep.
    """
    module = new_module(strings)
    numbers = range(size - 1)
    leaves = [client(f"Wide{number}", module=module) for number in numbers]
    root = client("WideRoot", leaves, module=module)
    return Tree(root, [*leaves, root], module)


def deep(size: int, strings: bool = False) -> Tree:
    """
    A chain of `size` clients: a tree `size` clients deep.
    """
    module = new_module(strings)
    clients: list[type[Client]] = []
    for number in range(size):
        below = clients[-1:]
        clients.append(client(f"Deep{number}", below, module=module))
    return Tree(clients[-1], clients, module)


def mixed(size: int, strings: bool = False) -> Tree:
    """
    A pyramid of `size` clients, 1, 2, 4, ... wide from the top; every client depends on two or three of the
    row below, so the rows share their dependencies like a diamond. About log2(size) clients deep.
    """
    module = new_module(strings)
    clients: list[type[Client]] = []
    below: list[type[Client]] = []
    for level, width in reversed(list(enumerate(_widths(size)))):
        below = _row(level, width, below, module)
        clients.extend(below)
    return Tree(below[0], clients, module)


def _widths(size: int) -> list[int]:
    """
    The widths of the rows of a pyramid of `size` clients from the top: 1, 2, 4, ..., then what is left.
    """
    widths = []
    total = 0
    width = 1
    while total + width <= size:
        widths.append(width)
        total += width
        width *= 2
    if total < size:
        widths.append(size - total)
    return widths


def _row(
    level: int,
    width: int,
    below: list[type[Client]],
    module: ModuleType | None,
) -> list[type[Client]]:
    """
    One row of the pyramid: client `number` depends on the clients `2 * number` to `2 * number + 2` of the row
    `below`, round the row and each once.
    """
    row = []
    for number in range(width):
        dependencies = list(dict.fromkeys(_picked(below, 2 * number)))
        row.append(client(f"Mixed{level}_{number}", dependencies, module=module))
    return row


def _picked(below: list[type[Client]], first: int) -> list[type[Client]]:
    """
    The three clients of the row `below` from `first` on, round the row; none for the bottom row.
    """
    if not below:
        return []
    width = len(below)
    return [below[(first + offset) % width] for offset in range(3)]


SHAPES: Final[Mapping[str, Shape]] = MappingProxyType({"wide": wide, "deep": deep, "mixed": mixed})
