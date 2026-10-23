"""
The detectors of shapes() of benchmarks/agents/evaluate.py, one per design nuke-di rejects: a provider function
(ADR-0005), bind() or a registration on a container (ADR-0005 / ADR-0008), NotSingletonClient (ADR-0006), an I/O
object made in `__init__`, and the retries of retries.py (ADR-0009).
"""

import ast
import re
from collections.abc import Iterable, Iterator, Set
from pathlib import Path
from typing import Final

from retries import retry_shapes, uses_retry_library

# The names a container goes by in the code an agent writes
CONTAINERS: Final = frozenset(("DI", "di", "deps", "container", "dependencies"))
# What a provider function hands out besides a client class of the project
PROVIDED: Final = frozenset(("AsyncClient", "ClientSession", "Pool", "Redis", "Engine", "AsyncEngine", "Connection"))
# What makes an I/O object
_IO: Final = re.compile("AsyncClient|ClientSession|create_pool")
# The methods that register something on a container
_REGISTRATIONS: Final = frozenset(("bind", "provide", "register", "provider", "factory"))


def client_classes(trees: Iterable[ast.Module]) -> set[str]:
    """
    The names of the classes that derive from Client, directly or through another class of the project.
    """
    bases = _class_bases(trees)
    clients = {"Client"}
    while True:
        derived = {class_name for class_name, parents in bases.items() if parents & clients}
        if derived <= clients:
            return clients - {"Client"}
        clients |= derived


def _class_bases(trees: Iterable[ast.Module]) -> dict[str, set[str]]:
    """
    The names of the bases of every class, without their modules.
    """
    bases: dict[str, set[str]] = {}
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                unparsed = map(ast.unparse, node.bases)
                bases[node.name] = {base_name.rpartition(".")[2] for base_name in unparsed}
    return bases


def findings(project: Path, path: Path, tree: ast.Module, provided: Set[str]) -> list[str]:
    """
    The rejected designs of one file of the project, one line each: the file, the line and what it is.
    """
    name = path.relative_to(project)
    shapes = _shapes(tree, path.name, provided)
    found = [f"{name}:{line}: {what}" for line, what in shapes]
    if uses_retry_library(path.read_text(encoding="utf-8")):
        found.append(f"{name}: a retry library (ADR-0009)")
    return found


def _shapes(
    tree: ast.Module,
    filename: str,
    provided: Set[str],
) -> Iterator[tuple[int, str]]:
    # The tests may hand out clients and retry; a conftest.py may hand out clients too
    test = filename.startswith("test_")
    if not test and filename != "conftest.py":
        yield from _provider_functions(tree, provided)
    for node in ast.walk(tree):
        yield from _registrations(node)
        yield from retry_shapes(node, test)
        yield from _io_in_init(node)


def _provider_functions(tree: ast.Module, provided: Set[str]) -> Iterator[tuple[int, str]]:
    """
    A module-level function that hands out a client or a connection: a provider (ADR-0005).
    """
    for function in tree.body:
        if isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef) and function.returns is not None:
            returned = set(re.findall(r"\w+", ast.unparse(function.returns)))
            if returned & provided:
                yield function.lineno, f"a provider function {function.name}() (ADR-0005)"


def _registrations(node: ast.AST) -> Iterator[tuple[int, str]]:
    """
    bind() (ADR-0008), NotSingletonClient (ADR-0006), and a registration on a container (ADR-0005 / ADR-0008).
    """
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == "bind":
            yield node.lineno, "bind() (ADR-0008)"
    if isinstance(node, ast.Name) and node.id == "NotSingletonClient":
        yield node.lineno, "NotSingletonClient (ADR-0006)"
    if not isinstance(node, ast.Attribute):
        return
    attribute = node.attr
    if attribute == "NotSingletonClient":
        yield node.lineno, "NotSingletonClient (ADR-0006)"
    owner = node.value
    container = owner.id if isinstance(owner, ast.Name) else None
    if attribute in _REGISTRATIONS and container in CONTAINERS:
        yield node.lineno, f"{container}.{attribute} (ADR-0005 / ADR-0008)"


def _io_in_init(node: ast.AST) -> Iterator[tuple[int, str]]:
    if not isinstance(node, ast.FunctionDef) or node.name != "__init__":
        return
    calls = [inner for inner in ast.walk(node) if isinstance(inner, ast.Call)]
    for call in calls:
        if _IO.search(ast.unparse(call.func)):
            yield call.lineno, "I/O object created in __init__"
