"""
How the container reads `__init__`: once per class, from the code object where it can.
"""

import inspect
import logging
from functools import wraps
from typing import Any

import pytest

from nuke_di import Client, Dependencies, InvalidSignatureError, client_dataclass

ENTRY = "__nuke_di_arguments__"


class Database(Client):
    pass


class Cache(Client):
    pass


class Repository(Client):
    def __init__(self, db: Database) -> None:
        self.db = db


class Inherits(Repository):
    pass


class Redefines(Repository):
    def __init__(self, cache: Cache) -> None:
        self.cache = cache


class KeywordOnly(Client):
    def __init__(self, *, db: Database, retries: int = 3, cache: Cache) -> None:
        self.db, self.retries, self.cache = db, retries, cache


class SelfInArgs(Client):
    db: Database

    def __init__(*args: Any, db: Database) -> None:
        args[0].db = db


class PositionalOnlyDefault(Client):
    def __init__(self, label: str = "main", /, *, db: Database) -> None:
        self.label, self.db = label, db


def passthrough(init: Any) -> Any:
    @wraps(init)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> None:
        init(self, *args, **kwargs)

    return wrapper


class Decorated(Client):
    @passthrough
    def __init__(self, db: Database, retries: int = 3) -> None:
        self.db, self.retries = db, retries


class DeclaredSignature(Client):
    def __init__(self, **kwargs: Any) -> None:
        self.db = kwargs["db"]


DeclaredSignature.__init__.__annotations__ = {"db": Database, "return": None}
DeclaredSignature.__init__.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
    [
        inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        inspect.Parameter("db", inspect.Parameter.KEYWORD_ONLY),
    ]
)


class ClassSignature(Client):
    # `inspect.signature(ClassSignature)` would report it; the container reads `__init__` itself
    __signature__ = inspect.Signature([inspect.Parameter("other", inspect.Parameter.KEYWORD_ONLY)])

    def __init__(self, db: Database) -> None:
        self.db = db


@client_dataclass(kw_only=True)
class DataclassClient:
    db: Database
    retries: int = 3


def test_arguments_are_read_once_per_class() -> None:
    first = Dependencies().resolve(Repository)
    entry = vars(Repository)[ENTRY]

    second = Dependencies().resolve(Repository)

    assert vars(Repository)[ENTRY] is entry
    assert entry == (Repository.__init__, {"db": Database})
    assert isinstance(first.db, Database) and isinstance(second.db, Database) and first is not second


def test_inherited_init_shares_the_entry_of_the_base_class() -> None:
    Dependencies().resolve(Repository)
    client = Dependencies().resolve(Inherits)

    assert isinstance(client.db, Database)
    assert ENTRY not in vars(Inherits)
    assert Inherits.__nuke_di_arguments__ is vars(Repository)[ENTRY]  # type: ignore[attr-defined]


def test_class_that_forbids_attributes_is_read_every_time() -> None:
    class Frozen(type):
        def __setattr__(cls, name: str, value: object) -> None:
            raise AttributeError(f"{cls.__name__} is read-only")

    class Sealed(Client, metaclass=Frozen):
        def __init__(self, db: Database) -> None:
            self.db = db

    for _ in range(2):
        assert isinstance(Dependencies().resolve(Sealed).db, Database)
    assert ENTRY not in vars(Sealed)


def test_redefined_init_gets_its_own_entry() -> None:
    Dependencies().resolve(Repository)
    client = Dependencies().resolve(Redefines)

    assert isinstance(client.cache, Cache)
    assert vars(Redefines)[ENTRY] == (Redefines.__init__, {"cache": Cache})
    assert vars(Repository)[ENTRY] == (Repository.__init__, {"db": Database})


def test_replaced_init_is_read_again() -> None:
    class Patched(Client):
        def __init__(self, db: Database) -> None:
            self.db = db

    assert isinstance(Dependencies().resolve(Patched).db, Database)

    def init(self: Any, cache: Cache) -> None:
        self.cache = cache

    Patched.__init__ = init  # type: ignore[method-assign, assignment]
    client = Dependencies().resolve(Patched)

    assert isinstance(client.cache, Cache)  # type: ignore[attr-defined]
    assert vars(Patched)[ENTRY] == (init, {"cache": Cache})


def test_failed_type_hints_are_not_cached() -> None:
    class Uses(Client):
        def __init__(self, local: "Local") -> None:  # type: ignore[name-defined]  # noqa: F821
            self.local = local

    for _ in range(2):
        with pytest.raises(InvalidSignatureError, match=r"^Cannot evaluate the type hints of \"Uses.__init__\""):
            Dependencies().resolve(Uses)

    assert ENTRY not in vars(Uses)


def test_failed_signature_is_not_cached() -> None:
    class NoHint(Client):
        def __init__(self, db) -> None:  # type: ignore[no-untyped-def]
            self.db = db

    for _ in range(2):
        with pytest.raises(InvalidSignatureError, match=r"has no type hint"):
            Dependencies().resolve(NoHint)  # type: ignore[nuke-di]

    assert ENTRY not in vars(NoHint)


def test_class_without_init_takes_no_arguments() -> None:
    assert isinstance(Dependencies().resolve(Database), Database)
    assert ENTRY not in vars(Database)


def test_keyword_only_arguments() -> None:
    client = Dependencies().resolve(KeywordOnly)

    assert (type(client.db), client.retries, type(client.cache)) == (Database, 3, Cache)


def test_self_through_args() -> None:
    assert isinstance(Dependencies().resolve(SelfInArgs).db, Database)


def test_positional_only_default_is_left_alone() -> None:
    client = Dependencies().resolve(PositionalOnlyDefault)

    assert (client.label, type(client.db)) == ("main", Database)


def test_decorated_init_is_read_through_wrapped() -> None:
    client = Dependencies().resolve(Decorated)

    assert (type(client.db), client.retries) == (Database, 3)


def test_declared_signature_of_init_is_honoured() -> None:
    assert isinstance(Dependencies().resolve(DeclaredSignature).db, Database)


def test_signature_of_the_class_is_ignored() -> None:
    assert isinstance(Dependencies().resolve(ClassSignature).db, Database)


def test_keyword_only_dataclass() -> None:
    client = Dependencies().resolve(DataclassClient)  # type: ignore[type-var]

    assert (type(client.db), client.retries) == (Database, 3)


def test_future_annotations() -> None:
    namespace: dict[str, Any] = {"Client": Client, "Database": Database}
    exec(  # noqa: S102 - a module written with string annotations, as real code is
        "from __future__ import annotations\n"
        "class Service(Client):\n"
        "    def __init__(self, db: Database, retries: int = 3) -> None:\n"
        "        self.db, self.retries = db, retries\n",
        namespace,
    )
    service = namespace["Service"]

    client = Dependencies().resolve(service)

    assert (type(client.db), client.retries) == (Database, 3)
    assert vars(service)[ENTRY] == (service.__init__, {"db": Database})


def test_resolve_logs_only_when_debug_is_enabled(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="nuke_di.core"):
        Dependencies().resolve(Repository)
    assert caplog.records == []

    with caplog.at_level(logging.DEBUG, logger="nuke_di.core"):
        Dependencies().resolve(Repository)
    assert [record.getMessage() for record in caplog.records] == [
        'Resolving dependency "Repository"',
        'Resolving dependency "Database"',
    ]
    assert caplog.records[0].client == "Repository"  # type: ignore[attr-defined]
