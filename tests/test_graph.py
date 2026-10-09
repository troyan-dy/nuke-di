from unittest.mock import AsyncMock

import pytest

from nuke_di import Client, Dependencies, Graph, Node, NotSingletonClient


class Postgres(Client):
    pass


class Redis(Client):
    pass


class Payments(Client):
    def __init__(self, pg: Postgres) -> None:
        self.pg = pg


class Checkout(Client):
    def __init__(self, pg: Postgres, redis: Redis, payments: Payments) -> None:
        self.pg = pg
        self.redis = redis
        self.payments = payments


class Session(NotSingletonClient):
    pass


class Orders(Client):
    def __init__(self, session: Session) -> None:
        self.session = session


class Reports(Client):
    def __init__(self, session: Session) -> None:
        self.session = session


def by_name(graph: Graph) -> dict[str, Node]:
    return {node.name: node for node in graph.nodes}


def test_graph_is_empty_before_resolve() -> None:
    assert Dependencies().graph().nodes == ()


def test_graph_lists_the_resolved_clients_in_resolution_order() -> None:
    deps = Dependencies()
    deps.resolve(Checkout)

    assert [node.name for node in deps.graph().nodes] == ["Postgres", "Redis", "Payments", "Checkout"]


def test_node_knows_its_class_layer_and_singleton() -> None:
    deps = Dependencies()
    deps.resolve(Checkout)
    nodes = by_name(deps.graph())

    assert nodes["Postgres"].cls is Postgres
    assert nodes["Postgres"].singleton is True
    assert nodes["Postgres"].replacement is None
    assert [nodes[name].layer for name in ("Postgres", "Redis", "Payments", "Checkout")] == [0, 0, 1, 2]


def test_dependencies_are_keyed_by_init_argument_and_shared_between_consumers() -> None:
    deps = Dependencies()
    deps.resolve(Checkout)
    nodes = by_name(deps.graph())

    assert list(nodes["Checkout"].dependencies) == ["pg", "redis", "payments"]
    assert nodes["Checkout"].dependencies["pg"] is nodes["Postgres"]
    assert nodes["Payments"].dependencies["pg"] is nodes["Postgres"]
    assert nodes["Postgres"].dependencies == {}


def test_not_singleton_client_gets_one_node_per_instance() -> None:
    deps = Dependencies()
    deps.resolve(Orders)
    deps.resolve(Reports)
    graph = deps.graph()

    sessions = [node for node in graph.nodes if node.cls is Session]
    assert len(sessions) == 2
    assert sessions[0] is not sessions[1]
    assert all(node.singleton is False for node in sessions)
    nodes = by_name(graph)
    assert nodes["Orders"].dependencies["session"] is not nodes["Reports"].dependencies["session"]


def test_replacement_node_shows_the_expected_class_and_the_object_in_its_place() -> None:
    deps = Dependencies()
    pg = deps.mock(Postgres)
    deps.resolve(Payments)
    nodes = by_name(deps.graph())

    assert nodes["Postgres"].cls is Postgres
    assert nodes["Postgres"].replacement is pg
    assert nodes["Postgres"].layer is None
    assert nodes["Postgres"].singleton is True
    assert nodes["Payments"].dependencies["pg"] is nodes["Postgres"]
    # A Replacement is never connected, so the consumer does not sit above it
    assert nodes["Payments"].layer == 0


async def test_graph_is_allowed_while_connected() -> None:
    deps = Dependencies()
    deps.resolve(Payments)

    async with deps:
        assert [node.name for node in deps.graph().nodes] == ["Postgres", "Payments"]


def test_graph_is_empty_after_flush() -> None:
    deps = Dependencies()
    deps.resolve(Checkout)
    deps.flush()

    assert deps.graph().nodes == ()


def test_mermaid_groups_the_layers_and_points_from_dependency_to_consumer() -> None:
    deps = Dependencies()
    deps.resolve(Checkout)

    assert deps.graph().to_mermaid() == (
        "graph BT\n"
        "  subgraph layer0 [layer 0]\n"
        "    Postgres\n"
        "    Redis\n"
        "  end\n"
        "  subgraph layer1 [layer 1]\n"
        "    Payments\n"
        "  end\n"
        "  subgraph layer2 [layer 2]\n"
        "    Checkout\n"
        "  end\n"
        "  Postgres --> Payments\n"
        "  Postgres --> Checkout\n"
        "  Redis --> Checkout\n"
        "  Payments --> Checkout\n"
    )


def test_mermaid_draws_a_replacement_outside_the_layers_with_a_dashed_border() -> None:
    deps = Dependencies()
    deps.mock(Postgres, AsyncMock())
    deps.resolve(Payments)

    assert deps.graph().to_mermaid() == (
        "graph BT\n"
        '  Postgres["Postgres: AsyncMock"]\n'
        "  style Postgres stroke-dasharray: 5 5\n"
        "  subgraph layer0 [layer 0]\n"
        "    Payments\n"
        "  end\n"
        "  Postgres --> Payments\n"
    )


def test_mermaid_names_the_mock_class_of_an_autospec_replacement() -> None:
    # An autospec mock reports the spec as its __class__, which would label it "Postgres: Postgres"
    deps = Dependencies()
    deps.mock(Postgres)
    deps.resolve(Payments)

    assert '  Postgres["Postgres: MagicMock"]\n' in deps.graph().to_mermaid()


def test_mermaid_numbers_the_instances_of_a_not_singleton_client() -> None:
    deps = Dependencies()
    deps.resolve(Orders)
    deps.resolve(Reports)

    assert deps.graph().to_mermaid() == (
        "graph BT\n"
        "  subgraph layer0 [layer 0]\n"
        "    Session\n"
        "    Session_2[Session]\n"
        "  end\n"
        "  subgraph layer1 [layer 1]\n"
        "    Orders\n"
        "    Reports\n"
        "  end\n"
        "  Session --> Orders\n"
        "  Session_2 --> Reports\n"
    )


def test_mermaid_of_an_empty_graph_is_the_header_alone() -> None:
    assert Dependencies().graph().to_mermaid() == "graph BT\n"


@pytest.mark.parametrize("name", ["Graph", "Node"])
def test_graph_types_are_exported(name: str) -> None:
    import nuke_di

    assert name in nuke_di.__all__


class Both(Client):
    def __init__(self, pg: Postgres, redis: Redis) -> None:
        self.pg, self.redis = pg, redis


def test_one_object_replacing_two_classes_gives_two_nodes() -> None:
    deps = Dependencies()
    shared = AsyncMock()
    deps.mock(Postgres, shared)
    deps.mock(Redis, shared)
    deps.resolve(Both)
    nodes = by_name(deps.graph())

    assert sorted(nodes) == ["Both", "Postgres", "Redis"]
    assert nodes["Both"].dependencies["pg"] is nodes["Postgres"]
    assert nodes["Both"].dependencies["redis"] is nodes["Redis"]
    assert nodes["Postgres"].replacement is shared and nodes["Redis"].replacement is shared


def test_a_resolved_client_replacing_another_class_keeps_both_nodes() -> None:
    deps = Dependencies()
    pg = deps.resolve(Postgres)
    deps.mock(Redis, pg)
    deps.resolve(Both)
    nodes = by_name(deps.graph())

    assert nodes["Postgres"].replacement is None
    assert nodes["Redis"].replacement is pg
    assert nodes["Both"].dependencies["pg"] is nodes["Postgres"]
    assert nodes["Both"].dependencies["redis"] is nodes["Redis"]


def test_a_replacement_without_consumers_is_a_node() -> None:
    deps = Dependencies()
    deps.mock(Redis)
    deps.resolve(Payments)

    assert [node.name for node in deps.graph().nodes] == ["Redis", "Postgres", "Payments"]


def test_a_mocked_not_singleton_client_is_one_shared_node() -> None:
    deps = Dependencies()
    deps.mock(Session)
    deps.resolve(Orders)
    deps.resolve(Reports)
    nodes = by_name(deps.graph())

    assert nodes["Session"].singleton is False
    assert nodes["Orders"].dependencies["session"] is nodes["Reports"].dependencies["session"]


def test_the_replacement_of_an_open_override_survives_flush() -> None:
    deps = Dependencies()
    with deps.override(Postgres) as pg:
        deps.resolve(Payments)
        deps.flush()
        nodes = by_name(deps.graph())

        assert list(nodes) == ["Postgres"]
        assert nodes["Postgres"].replacement is pg
    assert deps.graph().nodes == ()


def test_a_client_appended_to_connect_clients_by_hand_has_no_dependencies() -> None:
    deps = Dependencies()
    deps.connect_clients.append(Postgres())
    nodes = by_name(deps.graph())

    assert nodes["Postgres"].layer == 0
    assert dict(nodes["Postgres"].dependencies) == {}


def test_mermaid_ids_stay_unique_next_to_a_class_named_like_a_numbered_instance() -> None:
    class Session_2(Client):  # noqa: N801 - the point is the clash with the numbering
        pass

    class Audit(Client):
        def __init__(self, session: Session, other: Session_2) -> None:
            self.session, self.other = session, other

    deps = Dependencies()
    deps.resolve(Orders)
    deps.resolve(Audit)
    mermaid = deps.graph().to_mermaid()

    assert "    Session\n" in mermaid
    assert "    Session_2\n" in mermaid
    assert "    Session_3[Session]\n" in mermaid
    assert "  Session_3 --> Audit\n" in mermaid
    assert "  Session_2 --> Audit\n" in mermaid


def test_repr_of_a_node_does_not_recurse_into_its_dependencies() -> None:
    deps = Dependencies()
    deps.resolve(Checkout)
    nodes = by_name(deps.graph())

    assert "dependencies" not in repr(nodes["Checkout"])
    assert "Postgres" not in repr(nodes["Checkout"])


def test_dependencies_are_read_only() -> None:
    deps = Dependencies()
    deps.resolve(Payments)
    nodes = by_name(deps.graph())

    with pytest.raises(TypeError):
        nodes["Payments"].dependencies["redis"] = nodes["Postgres"]  # type: ignore[index]


def test_graphs_compare_by_identity_like_nodes() -> None:
    deps = Dependencies()

    assert deps.graph() != deps.graph()
