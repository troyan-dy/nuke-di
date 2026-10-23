import pathlib
from collections.abc import Mapping

from nuke_di import Dependencies, Node

from graph.api import place_order


def test_dependencies(di: Dependencies) -> None:
    di.inject(place_order)

    needs = {node.name: names_of(node.dependencies) for node in di.graph().nodes}

    assert needs == {
        "Postgres": [],
        "Redis": [],
        "OrderRepository": ["Postgres", "Redis"],
        "UserRepository": ["Postgres"],
        "Kafka": [],
        "OrderService": ["OrderRepository", "UserRepository", "Kafka"],
        "Notifications": ["UserRepository", "Kafka"],
        "Checkout": ["OrderService", "Notifications"],
    }


def names_of(dependencies: Mapping[str, Node]) -> list[str]:
    return [dependency.name for dependency in dependencies.values()]


def test_repositories_share_one_postgres(di: Dependencies) -> None:
    di.inject(place_order)

    nodes = {node.name: node for node in di.graph().nodes}

    assert nodes["OrderRepository"].dependencies["pg"] is nodes["UserRepository"].dependencies["pg"]


def test_readme_diagram_is_current(di: Dependencies) -> None:
    di.inject(place_order)
    readme = (pathlib.Path(__file__).parent / "README.md").read_text()

    mermaid = di.graph().to_mermaid()
    assert f"```mermaid\n{mermaid}```" in readme
