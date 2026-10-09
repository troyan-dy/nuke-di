from nuke_di import Client, Dependencies, InvalidSignatureError

from startup_failures.clients import Postgres


class Cache(Client):
    def __init__(self, url: str) -> None:  # a setting without a default: not a client, nothing to inject
        self.url = url


class Audit(Client):
    def __init__(self, pg) -> None:  # type: ignore[no-untyped-def]  # the type hint is missing
        self.pg = pg


class Orders(Client):
    def __init__(self, pg: Postgres, payments: "Payments") -> None:
        self.pg = pg
        self.payments = payments


class Payments(Client):
    def __init__(self, orders: Orders) -> None:  # Orders needs Payments, Payments needs Orders
        self.orders = orders


class Checkout(Client):
    def __init__(self, pg: Postgres, cache: Cache) -> None:
        self.pg = pg
        self.cache = cache


def main() -> None:
    for root in (Checkout, Audit, Orders):
        deps = Dependencies()
        try:
            deps.resolve(root)  # fails here: connect() is never reached
        except InvalidSignatureError as exc:  # CircularDependencyError is one too
            print(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
