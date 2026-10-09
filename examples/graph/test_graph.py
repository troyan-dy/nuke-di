import pathlib

from nuke_di import Dependencies

from graph.api import place_order


def test_layers(di: Dependencies) -> None:
    di.inject(place_order)

    layers = {node.name: node.layer for node in di.graph().nodes}

    assert layers == {
        "Postgres": 0,
        "Redis": 0,
        "Kafka": 0,
        "OrderRepository": 1,
        "UserRepository": 1,
        "OrderService": 2,
        "Notifications": 2,
        "Checkout": 3,
    }


def test_repositories_share_one_postgres(di: Dependencies) -> None:
    di.inject(place_order)

    nodes = {node.name: node for node in di.graph().nodes}

    assert nodes["OrderRepository"].dependencies["pg"] is nodes["UserRepository"].dependencies["pg"]


def test_readme_diagram_is_current(di: Dependencies) -> None:
    di.inject(place_order)
    readme = (pathlib.Path(__file__).parent / "README.md").read_text()

    assert f"```mermaid\n{di.graph().to_mermaid()}```" in readme
