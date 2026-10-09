from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from nuke_di.types import NotSingletonClient
from nuke_di.utils import sname


@dataclass(frozen=True, eq=False)
class Node:
    """
    One client of a Graph: a resolved instance, or a Replacement standing in for a class.

    Nodes compare by identity: a `NotSingletonClient` has one node per instance, all with the same name.
    """

    # The class the consumers asked for; for a Replacement, the class it stands in for
    cls: type[NotSingletonClient]
    # `Client` rather than `NotSingletonClient`
    singleton: bool
    # The Layer the client connects in; None for a Replacement, which is never connected
    layer: int | None
    # The object registered with `mock()` or `override()` in place of `cls`; None for a real client
    replacement: object | None
    # The clients of the `__init__` arguments, by argument name; left out of repr(), which would otherwise
    # print the whole tree below the node, once per path that reaches a shared client
    dependencies: Mapping[str, Node] = field(default_factory=lambda: MappingProxyType({}), repr=False)

    @property
    def name(self) -> str:
        return sname(self.cls)


@dataclass(frozen=True, eq=False)
class Graph:
    """
    Every client a container has resolved, with who depends on whom and the Layer of each: a snapshot.

    Compares by identity, like its nodes.
    """

    # In resolution order: a client comes after its dependencies
    nodes: tuple[Node, ...] = ()

    def to_mermaid(self) -> str:
        """
        A Mermaid flowchart: the layers as subgraphs, an arrow from every dependency to its consumer,
        a Replacement outside the layers with a dashed border.
        """
        ids = self._ids()
        lines = ["graph BT"]

        for node in self.nodes:
            if node.replacement is not None:
                lines.append(f'  {ids[node]}["{node.name}: {type(node.replacement).__name__}"]')
                lines.append(f"  style {ids[node]} stroke-dasharray: 5 5")

        layers = sorted({node.layer for node in self.nodes if node.layer is not None})
        for layer in layers:
            lines.append(f"  subgraph layer{layer} [layer {layer}]")
            for node in self.nodes:
                if node.layer == layer:
                    label = "" if ids[node] == node.name else f"[{node.name}]"
                    lines.append(f"    {ids[node]}{label}")
            lines.append("  end")

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
            ident, number = node.name, 1
            # A class keeps its own name even when a numbered duplicate would claim it first
            while ident in taken or (number > 1 and ident in names):
                number += 1
                ident = f"{node.name}_{number}"
            taken.add(ident)
            ids[node] = ident
        return ids
