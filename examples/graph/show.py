from nuke_di import Dependencies

from graph.api import place_order


def main() -> None:
    deps = Dependencies()
    deps.inject(place_order)  # builds the tree and connects nothing
    graph = deps.graph()

    # In resolution order: a client comes after its dependencies
    for node in graph.nodes:
        names = [dependency.name for dependency in node.dependencies.values()]
        needs = ", ".join(names) or "-"
        print(f"{node.name:<16} needs {needs}")
    print()
    print(graph.to_mermaid(), end="")


if __name__ == "__main__":
    main()
