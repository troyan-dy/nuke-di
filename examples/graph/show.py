from nuke_di import Dependencies

from graph.api import place_order


def main() -> None:
    deps = Dependencies()
    deps.inject(place_order)  # builds the tree and connects nothing
    graph = deps.graph()

    for node in sorted(graph.nodes, key=lambda node: node.layer or 0):
        needs = ", ".join(dependency.name for dependency in node.dependencies.values()) or "-"
        print(f"layer {node.layer}  {node.name:<16} needs {needs}")
    print()
    print(graph.to_mermaid(), end="")


if __name__ == "__main__":
    main()
