from functools import partial
from typing import Any, Literal, Protocol, cast

import pytest

from nuke_di import CircularDependencyError, Client, Dependencies, InvalidSignatureError, client_dataclass


class Chicken(Client):
    def __init__(self, egg: "Egg") -> None:
        self.egg = egg


class Egg(Client):
    def __init__(self, chicken: Chicken) -> None:
        self.chicken = chicken


class Nest(Client):
    def __init__(self, chicken: Chicken) -> None:
        self.chicken = chicken


class Database(Client):
    pass


class Repository(Protocol):
    async def get(self) -> int: ...


class UsesRepository(Client):
    def __init__(self, repo: Repository) -> None:
        self.repo = repo


class Batches(Client):
    def __init__(self, sizes: list[int]) -> None:
        self.sizes = sizes


class Retries(Client):
    def __init__(self, count: int) -> None:
        self.count = count


class Either(Client):
    def __init__(self, source: Database | int) -> None:
        self.source = source


class MaybeCount(Client):
    def __init__(self, count: int | None) -> None:
        self.count = count


class Anything(Client):
    def __init__(self, value: Any) -> None:
        self.value = value


class Mode(Client):
    def __init__(self, mode: Literal["fast"]) -> None:
        self.mode = mode


class Handler(Client):
    feed: "Feed"


class Feed(Client):
    def __init__(self, handler: Handler) -> None:
        self.handler = handler


class Checkout(Client):
    def __init__(self, users: UsesRepository) -> None:
        self.users = users


class NoHint(Client):
    def __init__(self, db) -> None:  # type: ignore[no-untyped-def]
        self.db = db


class OptionalClient(Client):
    def __init__(self, db: Database | None) -> None:
        self.db = db


class PositionalOnly(Client):
    def __init__(self, db: Database, /) -> None:
        self.db = db


class WithDefaults(Client):
    def __init__(self, db: Database, retries: int = 3, label="main", cache: Database | None = None) -> None:  # type: ignore[no-untyped-def]
        self.db, self.retries, self.label, self.cache = db, retries, label, cache


class Variadic(Client):
    def __init__(self, db: Database, *args: object, **kwargs: object) -> None:
        self.db = db


@client_dataclass
class DataclassClient:
    db: Database
    retries: int = 3


async def handler(checkout: Checkout) -> None: ...


def test_cycle_names_the_path() -> None:
    with pytest.raises(CircularDependencyError, match=r"^Circular dependency: Nest -> Chicken -> Egg -> Chicken$"):
        Dependencies().resolve(Nest)


def test_cycle_is_an_invalid_signature() -> None:
    assert issubclass(CircularDependencyError, InvalidSignatureError)


def test_non_client_argument_names_type_and_path() -> None:
    expected = (
        r'^Argument "repo" of "UsesRepository.__init__" is Repository, which is not a client '
        r"\(resolving Checkout -> UsesRepository\)$"
    )
    with pytest.raises(InvalidSignatureError, match=expected):
        Dependencies().resolve(Checkout)


def test_path_starts_at_injected_function() -> None:
    with pytest.raises(InvalidSignatureError, match=r"\(resolving handler -> Checkout -> UsesRepository\)$"):
        Dependencies().inject(handler)


def test_argument_without_type_hint() -> None:
    expected = r'^Argument "db" of "NoHint.__init__" has no type hint \(resolving NoHint\)$'
    with pytest.raises(InvalidSignatureError, match=expected):
        Dependencies().resolve(NoHint)


def test_optional_client() -> None:
    expected = r'^Argument "db" of "OptionalClient.__init__" is Database \| None, a client cannot be optional'
    with pytest.raises(InvalidSignatureError, match=expected):
        Dependencies().resolve(OptionalClient)


def test_positional_only_client() -> None:
    with pytest.raises(InvalidSignatureError, match=r'^Argument "db" of "PositionalOnly.__init__" is positional-only'):
        Dependencies().resolve(PositionalOnly)


def test_unresolvable_forward_reference() -> None:
    def make() -> type[Client]:
        class Local(Client):
            pass

        class Uses(Client):
            def __init__(self, local: "Local") -> None:
                self.local = local

        return Uses

    expected = r'^Cannot evaluate the type hints of "Uses.__init__": name \'Local\' is not defined; a type hint'
    with pytest.raises(InvalidSignatureError, match=expected):
        Dependencies().resolve(make())


def test_arguments_with_defaults_are_left_alone() -> None:
    client = Dependencies().resolve(WithDefaults)

    assert isinstance(client.db, Database)
    assert (client.retries, client.label, client.cache) == (3, "main", None)


def test_variadic_arguments_are_left_alone() -> None:
    assert isinstance(Dependencies().resolve(Variadic).db, Database)


def test_dataclass_client() -> None:
    client = Dependencies().resolve(DataclassClient)  # type: ignore[type-var]

    assert isinstance(client.db, Database)


def test_failed_resolution_leaves_no_stale_path() -> None:
    dep = Dependencies()
    with pytest.raises(InvalidSignatureError):
        dep.resolve(Checkout)

    with pytest.raises(InvalidSignatureError, match=r"\(resolving NoHint\)$"):
        dep.resolve(NoHint)


def test_generic_argument_is_named_in_full() -> None:
    with pytest.raises(InvalidSignatureError, match=r'^Argument "sizes" of "Batches.__init__" is list\[int\], which'):
        Dependencies().resolve(Batches)


@pytest.mark.parametrize(
    ("cls", "name"),
    [
        (Retries, "int"),
        (Either, "Database \\| int"),
        (MaybeCount, "int \\| None"),
        (Anything, "Any"),
        (Mode, r"Literal\['fast'\]"),
    ],
)
def test_type_is_named_without_module(cls: type[Client], name: str) -> None:
    with pytest.raises(InvalidSignatureError, match=rf" is {name}, which is not a client"):
        Dependencies().resolve(cls)


def test_inject_wraps_unresolvable_forward_reference() -> None:
    async def handler(feed: "Missing") -> None:  # type: ignore[name-defined]  # noqa: F821
        pass

    with pytest.raises(InvalidSignatureError, match=r'^Cannot evaluate the type hints of "handler": name \'Missing\''):
        Dependencies().inject(handler)


def test_inject_class_is_not_part_of_a_cycle() -> None:
    # inject() reads the class annotations, Feed depends on Handler through __init__: no cycle
    injected = cast(partial[Handler], Dependencies().inject(Handler))

    assert isinstance(injected.keywords["feed"].handler, Handler)
