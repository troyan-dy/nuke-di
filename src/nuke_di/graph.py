from __future__ import annotations

from dataclasses import dataclass, field

from nuke_di.utils import sname


@dataclass(frozen=True, eq=False)
class Node:
    """
    One client of a Graph: a resolved instance, or a Replacement standing in for a class.

    Nodes compare by identity: a `NotSingletonClient` has one node per instance, all with the same name.
    """

    # The class the consumers asked for; for a Replacement, the class it stands in for
    cls: type
    # `Client` rather than `NotSingletonClient`
    singleton: bool
    # The Layer the client connects in; None for a Replacement, which is never connected
    layer: int | None
    # The object registered with `mock()` or `override()` in place of `cls`; None for a real client
    replacement: object | None
    # The clients of the `__init__` arguments, by argument name
    dependencies: dict[str, Node] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return sname(self.cls)


@dataclass(frozen=True)
class Graph:
    """
    Every client a container has resolved, with who depends on whom and the Layer of each: a snapshot.
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
                lines.append(f'  {ids[node]}["{node.name}: {sname(node.replacement)}"]')
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
        A Mermaid id per node: the name, numbered from the second node that shares it.
        """
        seen: dict[str, int] = {}
        ids: dict[Node, str] = {}
        for node in self.nodes:
            count = seen[node.name] = seen.get(node.name, 0) + 1
            ids[node] = node.name if count == 1 else f"{node.name}_{count}"
        return ids
