import pathlib

from nuke_di import Dependencies

from graph.api import place_order


def test_dependencies(di: Dependencies) -> None:
    di.inject(place_order)

    needs = {node.name: [dependency.name for dependency in node.dependencies.values()] for node in di.graph().nodes}

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


def test_repositories_share_one_postgres(di: Dependencies) -> None:
    di.inject(place_order)

    nodes = {node.name: node for node in di.graph().nodes}

    assert nodes["OrderRepository"].dependencies["pg"] is nodes["UserRepository"].dependencies["pg"]


def test_readme_diagram_is_current(di: Dependencies) -> None:
    di.inject(place_order)
    readme = (pathlib.Path(__file__).parent / "README.md").read_text()

    assert f"```mermaid\n{di.graph().to_mermaid()}```" in readme
