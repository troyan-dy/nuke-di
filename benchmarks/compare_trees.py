"""
The trees of benchmarks/compare.py: the shapes of benchmarks/run.py decorated for every library, and the product
page, a tree of slow connections as wide as a real service's.
"""

import sys
from collections.abc import Iterator, Mapping
from types import MappingProxyType
from typing import Final, NamedTuple

from libraries.compared import LIBRARIES
from suite.infrastructure import Sleeper
from suite.shapes import SHAPES
from suite.trees import Tree, client

from nuke_di import Client

PRODUCT_PAGE: Final = "product page: 21 clients, connect() of 10–300 ms"  # noqa: RUF001


class Node(NamedTuple):
    """
    A client of the product page: how long its connect() and disconnect() take, and the clients its `__init__`
    takes.
    """

    connect: float
    disconnect: float
    dependencies: tuple[str, ...] = ()


# The backend of an online shop's product page, a tree as wide as a real service's: the HTTP API needs four
# features, and every feature its own four connections, gRPC clients of other services, Kafka, Elasticsearch,
# Redis. Connected
# one after another, the startup is the sum, 3.37 s; along the longest chain, PaymentsClient → Checkout →
# ProductPageApi or EventsProducer → Personalization → ProductPageApi, it is 0.33 s. To stop, the longest chain is
# ProductPageApi → Checkout → PaymentsClient, 0.36 s; layer by layer, every layer waiting for its slowest client,
# it is 0.65 s, since Checkout and EventsProducer stop slowly in different layers
_PRODUCT_PAGE_ROWS: Final = (
    ("CatalogClient", 0.25, 0.01, ()),
    ("PricesClient", 0.2, 0.01, ()),
    ("StockClient", 0.2, 0.01, ()),
    ("ReviewsClient", 0.15, 0.01, ()),
    ("ProductCard", 0.05, 0.005, ("CatalogClient", "PricesClient", "StockClient", "ReviewsClient")),
    ("PaymentsClient", 0.3, 0.01, ()),
    ("DeliveryClient", 0.2, 0.01, ()),
    ("LoyaltyClient", 0.15, 0.01, ()),
    ("PromotionsClient", 0.2, 0.01, ()),
    # Finishes the orders in flight before it stops
    ("Checkout", 0.02, 0.3, ("PaymentsClient", "DeliveryClient", "LoyaltyClient", "PromotionsClient")),
    ("UsersClient", 0.2, 0.01, ()),
    ("RecommendationsClient", 0.25, 0.01, ()),
    ("FavoritesClient", 0.15, 0.01, ()),
    # Flushes the messages it has buffered
    ("EventsProducer", 0.3, 0.3, ()),
    ("Personalization", 0.02, 0.005, ("UsersClient", "RecommendationsClient", "FavoritesClient", "EventsProducer")),
    ("Elasticsearch", 0.25, 0.01, ()),
    ("Redis", 0.1, 0.01, ()),
    ("SynonymsClient", 0.15, 0.01, ()),
    ("CategoriesClient", 0.2, 0.01, ()),
    ("Search", 0.02, 0.005, ("Elasticsearch", "Redis", "SynonymsClient", "CategoriesClient")),
    ("ProductPageApi", 0.01, 0.05, ("ProductCard", "Checkout", "Personalization", "Search")),
)
PRODUCT_PAGE_TREE: Final[Mapping[str, Node]] = MappingProxyType(
    {name: Node(connect, disconnect, dependencies) for name, connect, disconnect, dependencies in _PRODUCT_PAGE_ROWS}
)


def product_page() -> type[Client]:
    """
    The classes of `PRODUCT_PAGE_TREE`, each a client whose `__init__` takes its dependencies by type hint;
    the root, ProductPageApi.
    """
    made: dict[str, type[Client]] = {}
    for name, node in PRODUCT_PAGE_TREE.items():
        dependencies = [made[dependency] for dependency in node.dependencies]
        client_class = client(name, dependencies, base=Sleeper)
        client_class.connect_seconds = node.connect  # type: ignore[attr-defined]
        client_class.disconnect_seconds = node.disconnect  # type: ignore[attr-defined]
        made[name] = client_class
    return made["ProductPageApi"]


def allow_recursion(size: int) -> None:
    # dishka, wireup and injector recurse once per level of the tree, and dependency-injector does on 3.11, which the
    # default limit stops before a chain of 1000; nuke-di does not recurse, the limit is raised for the others
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 4 * size + 1000))


def trees(sizes: list[int]) -> Iterator[tuple[str, int, Tree]]:
    """
    One tree per shape and size, decorated for every library, for the scenarios that reuse its classes.
    """
    for shape, build in SHAPES.items():
        for size in sizes:
            tree = build(size)
            for library in LIBRARIES:
                library.prepare(tree)
            yield shape, size, tree
