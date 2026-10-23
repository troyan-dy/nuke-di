from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TypeAlias

from nuke_di.naming import sname
from nuke_di.types import NotSingletonClient, isclient

# The class asked for and the client passed, per `__init__` argument of a client
_Arguments: TypeAlias = Mapping[str, tuple[type[NotSingletonClient], NotSingletonClient]]
# Of a client that takes no client
_NO_ARGUMENTS: _Arguments = MappingProxyType({})


@dataclass(frozen=True, eq=False)
class Node:
    """
    One client of a Graph: a resolved instance, or a Replacement standing in for a class.

    Nodes compare by identity: a `NotSingletonClient` has one node per instance, all with the same name.
    """

    # The class the consumers asked for; for a Replacement, the class it stands in for
    client_class: type[NotSingletonClient]
    # `Client` rather than `NotSingletonClient`
    singleton: bool
    # The object registered with `mock()` or `override()` in place of `client_class`; None for a real client
    replacement: object | None
    # The clients of the `__init__` arguments, by argument name; left out of repr(), which would otherwise
    # print the whole tree below the node, once per path that reaches a shared client
    dependencies: Mapping[str, Node] = field(default_factory=lambda: MappingProxyType({}), repr=False)

    @property
    def name(self) -> str:
        return sname(self.client_class)


@dataclass(frozen=True, eq=False)
class Graph:
    """
    Every client a container has resolved, with who depends on whom: a snapshot.

    Compares by identity, like its nodes.
    """

    # In resolution order: a client comes after its dependencies
    nodes: tuple[Node, ...] = ()

    def to_mermaid(self) -> str:
        """
        A Mermaid flowchart, bottom to top: an arrow from every dependency to its consumer, a Replacement with a
        dashed border.
        """
        ids = self._ids()
        lines = ["graph BT"]

        for node in self.nodes:
            lines.extend(_declaration(node, ids[node]))

        for node in self.nodes:
            for dependency in node.dependencies.values():
                lines.append(f"  {ids[dependency]} --> {ids[node]}")

        return "".join(f"{line}\n" for line in lines)

    def _ids(self) -> dict[Node, str]:
        """
        A unique Mermaid id per node: the name, or the name with the first free number from 2 when it is taken,
        by another instance of the same class or by a class that happens to be called like a numbered one.
        """
        names = {node.name for node in self.nodes}
        taken: set[str] = set()
        ids: dict[Node, str] = {}
        for node in self.nodes:
            ident = _free_id(node.name, taken, names)
            taken.add(ident)
            ids[node] = ident
        return ids


def snapshot(
    replacements: Mapping[type[NotSingletonClient], NotSingletonClient],
    clients: Iterable[NotSingletonClient],
    dependencies: Mapping[int, _Arguments],
) -> Graph:
    """
    The Graph of a container: its Replacements, then `clients` in resolution order, with the class asked for and the
    client passed per `__init__` argument of every client, by `id()`.
    """
    # A Replacement is one object for every class it stands in for, and may be a resolved client too,
    # so its nodes are keyed by class and the resolved clients by `id()`
    replaced = _replaced_nodes(replacements)
    resolved: dict[int, Node] = {}
    # Resolution order: a client's dependencies have a node before the client does
    for client in clients:
        arguments = dependencies.get(id(client), _NO_ARGUMENTS)
        resolved[id(client)] = _resolved_node(client, arguments, replacements, replaced, resolved)
    return Graph((*replaced.values(), *resolved.values()))


def _replaced_nodes(
    replacements: Mapping[type[NotSingletonClient], NotSingletonClient],
) -> dict[type[NotSingletonClient], Node]:
    """
    The node of every Replacement, by the class it stands in for.
    """
    return {
        client_class: Node(client_class=client_class, singleton=isclient(client_class), replacement=replacement)
        for client_class, replacement in replacements.items()
    }


def _resolved_node(
    client: NotSingletonClient,
    arguments: _Arguments,
    replacements: Mapping[type[NotSingletonClient], NotSingletonClient],
    replaced: Mapping[type[NotSingletonClient], Node],
    resolved: Mapping[int, Node],
) -> Node:
    """
    The node of a resolved client, whose dependencies have their nodes in `replaced` or `resolved` already.
    """
    edges: dict[str, Node] = {}
    for key, (client_class, dependency) in arguments.items():
        if replacements.get(client_class) is dependency:
            edges[key] = replaced[client_class]
        else:
            edges[key] = resolved[id(dependency)]
    return Node(
        client_class=type(client),
        singleton=isclient(type(client)),
        replacement=None,
        dependencies=MappingProxyType(edges),
    )


def _declaration(node: Node, node_id: str) -> tuple[str, ...]:
    """
    The lines of a Mermaid flowchart that declare `node`.
    """
    if node.replacement is None:
        label = "" if node_id == node.name else f"[{node.name}]"
        return (f"  {node_id}{label}",)
    replacement = type(node.replacement).__name__
    return (f'  {node_id}["{node.name}: {replacement}"]', f"  style {node_id} stroke-dasharray: 5 5")


def _free_id(name: str, taken: set[str], names: set[str]) -> str:
    """
    `name`, or `name` with the first free number from 2: an id no other node has taken, and no other class is
    called.
    """
    ident, number = name, 1
    # A class keeps its own name even when a numbered duplicate would claim it first
    while ident in taken or (number > 1 and ident in names):
        number += 1
        ident = f"{name}_{number}"
    return ident
