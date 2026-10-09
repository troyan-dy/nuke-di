"""
The mypy plugin of nuke_di: mypy, run in this process, reports what the container raises, with the same message.
"""

import contextlib
import importlib
import re
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from nuke_di import Dependencies, InvalidSignatureError
from tests.test_readme_translations import document, split

api = pytest.importorskip("mypy.api")

# One error of mypy: the file, the line and the message without its error code
ERROR = re.compile(r"^(?P<file>[\w/]+\.py):(?P<line>\d+): (?:error|note): (?P<message>.+?)(?:  \[[\w-]+\])?$")

# Every case of "When the tree cannot be built", and trees the container builds
CLIENTS = """
from collections.abc import Callable
from typing import Any, Generic, Literal, Protocol, TypeVar

from nuke_di import Client, NotSingletonClient, client_dataclass

T = TypeVar("T")


class Database(Client):
    pass


class Repository(Protocol):
    async def get(self) -> int: ...


class UsesRepository(Client):
    def __init__(self, repo: Repository) -> None:
        self.repo = repo


class Checkout(Client):
    def __init__(self, users: UsesRepository) -> None:
        self.users = users


class Chicken(Client):
    def __init__(self, egg: "Egg") -> None:
        self.egg = egg


class Egg(Client):
    def __init__(self, chicken: Chicken) -> None:
        self.chicken = chicken


class Nest(Client):
    def __init__(self, chicken: Chicken) -> None:
        self.chicken = chicken


class NoHint(Client):
    def __init__(self, db) -> None:  # type: ignore[no-untyped-def]
        self.db = db


class Untyped(Client):
    def __init__(self, db):  # type: ignore[no-untyped-def]
        self.db = db


class OptionalClient(Client):
    def __init__(self, db: Database | None) -> None:
        self.db = db


class PositionalOnly(Client):
    def __init__(self, db: Database, /) -> None:
        self.db = db


class Batches(Client):
    def __init__(self, sizes: list[int]) -> None:
        self.sizes = sizes


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


class Callbacks(Client):
    def __init__(self, on_item: Callable[[int], str]) -> None:
        self.on_item = on_item


class Outer:
    class Inner(Client):
        pass

    class Plain:
        pass


class UsesNestedPlain(Client):
    def __init__(self, plain: Outer.Plain) -> None:
        self.plain = plain


class Dunder(Client):
    def __init__(self, __db: Database) -> None:
        self.db = __db


class Repo(Client, Generic[T]):
    pass


class UsesGeneric(Client):
    def __init__(self, repo: Repo[int]) -> None:
        self.repo = repo


class UsesBareGeneric(Client):
    def __init__(self, repo: Repo) -> None:  # type: ignore[type-arg]
        self.repo = repo


class Nested(Client):
    def __init__(self, inner: Outer.Inner | None) -> None:
        self.inner = inner


class Fine(Client):
    def __init__(
        self, db: Database, inner: Outer.Inner, retries: int = 3, *args: object, cache: Database | None = None
    ) -> None:
        self.db, self.inner = db, inner


class Fresh(NotSingletonClient):
    def __init__(self, db: Database) -> None:
        self.db = db


class Twice(Client):
    def __init__(self, first: Fresh, second: Fresh, fine: Fine) -> None:
        self.first, self.second, self.fine = first, second, fine


@client_dataclass
class Plain:
    db: Database


@client_dataclass(frozen=True)
class Dataclass(Client):
    db: Database
    plain: Plain
    retries: int = 3


class Inherits(Fine):
    pass


class SelfInArgs(Client):
    def __init__(*args: Any, **kwargs: Any) -> None:
        pass
"""

# Roots the container fails on, then roots it builds
FAILING = [
    "Checkout",
    "UsesRepository",
    "Nest",
    "Chicken",
    "NoHint",
    "Untyped",
    "OptionalClient",
    "PositionalOnly",
    "Batches",
    "Either",
    "MaybeCount",
    "Anything",
    "Mode",
    "Callbacks",
    "Nested",
    "UsesNestedPlain",
    "UsesGeneric",
]
BUILDING = [
    "Database",
    "Fine",
    "Fresh",
    "Twice",
    "Dataclass",
    "Inherits",
    "SelfInArgs",
    "Outer.Inner",
    "Dunder",
    "UsesBareGeneric",
]


def mypy(root: Path, *files: str, cache: bool = False) -> list[tuple[str, int, str]]:
    """
    Run mypy with the plugin on `files` of `root`: (file, line, message) of every error and note.
    """
    (root / "mypy.ini").write_text("[mypy]\nplugins = nuke_di.mypy\nwarn_unused_ignores = False\n")
    options = [
        "--config-file",
        str(root / "mypy.ini"),
        "--cache-dir",
        str(root / ".mypy_cache") if cache else "/dev/null",
    ]
    stdout, stderr, _ = run(root, [*options, "--no-error-summary", *files])
    assert not stderr
    found = []
    for line in stdout.splitlines():
        match = ERROR.match(line)
        assert match is not None, line
        found.append((match["file"], int(match["line"]), match["message"]))
    return found


def run(root: Path, arguments: list[str]) -> tuple[str, str, int]:
    """
    `mypy.api.run()` in `root`, keeping the recursion limit, which mypy raises for the rest of the process.
    """
    limit = sys.getrecursionlimit()
    try:
        with contextlib.chdir(root):
            result: tuple[str, str, int] = api.run(arguments)
            return result
    finally:
        sys.setrecursionlimit(limit)


def write(root: Path, **modules: str) -> None:
    for name, source in modules.items():
        # The first line of a module is line 1 of the errors
        (root / f"{name}.py").write_text(textwrap.dedent(source).lstrip("\n"))


def runtime_error(module: Any, root: str) -> str | None:
    cls = module
    for part in root.split("."):
        cls = getattr(cls, part)
    try:
        Dependencies().resolve(cls)
    except InvalidSignatureError as exc:
        return str(exc)
    return None


def test_plugin_reports_what_the_container_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roots = [*FAILING, *BUILDING]
    calls = "from nuke_di import DI\nimport clients\n" + "".join(f"DI.resolve(clients.{root})\n" for root in roots)
    write(tmp_path, clients=CLIENTS, calls=calls)

    errors = mypy(tmp_path, "calls.py", "clients.py")

    monkeypatch.syspath_prepend(str(tmp_path))
    module = importlib.import_module("clients")
    for line, root in enumerate(roots, start=3):
        reported = [message for file, at, message in errors if (file, at) == ("calls.py", line)]
        expected = runtime_error(module, root)
        # The container stops at the first error, the plugin reports every one
        assert reported[:1] == ([] if expected is None else [expected]), root
    assert {file for file, _, _ in errors} == {"calls.py"}


def test_clients_of_one_name_are_named_in_full(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(
        tmp_path,
        billing="""
        from nuke_di import Client


        class Database(Client):
            def __init__(self, pool: int) -> None: ...
        """,
        orders="""
        import billing
        from nuke_di import Client


        class Database(Client):
            pass


        class Root(Client):
            def __init__(self, orders: Database, billing: billing.Database) -> None: ...
        """,
        calls="""
        from nuke_di import DI
        from orders import Root

        DI.resolve(Root)
        """,
    )

    errors = mypy(tmp_path, "calls.py", "orders.py", "billing.py")

    monkeypatch.syspath_prepend(str(tmp_path))
    expected = runtime_error(importlib.import_module("orders"), "Root")
    assert expected == (
        'Argument "pool" of "billing.Database.__init__" is int, which is not a client '
        "(resolving Root -> billing.Database)"
    )
    assert errors == [("calls.py", 4, expected)]


@pytest.mark.skipif(sys.version_info < (3, 12), reason="the type statement")
def test_an_alias_of_a_type_statement_is_no_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(
        tmp_path,
        aliases="""
        from typing import reveal_type

        from nuke_di import DI, Client


        class Database(Client):
            pass


        type Alias = Database


        class UsesAlias(Client):
            def __init__(self, db: Alias) -> None: ...


        def handler(user_id: int, db: Alias) -> None: ...


        reveal_type(DI.inject(handler))
        """,
    )
    (tmp_path / "calls.py").write_text(
        "from nuke_di import DI\nfrom aliases import UsesAlias\n\nDI.resolve(UsesAlias)\n"
    )

    errors = mypy(tmp_path, "calls.py", "aliases.py")

    monkeypatch.syspath_prepend(str(tmp_path))
    module = importlib.import_module("aliases")
    assert errors == [
        ("aliases.py", 20, 'Revealed type is "def (user_id: int, db: aliases.Database)"'),
        ("calls.py", 4, runtime_error(module, "UsesAlias")),
    ]


def test_an_alias_of_a_type_statement_is_no_client_on_every_python() -> None:
    # Python 3.11 cannot parse a `type` statement, for mypy either: its alias is made by hand
    from mypy.nodes import Context, TypeAlias
    from mypy.types import AnyType, TypeAliasType, TypeOfAny

    from nuke_di import mypy as plugin

    alias = TypeAlias(
        AnyType(TypeOfAny.explicit), "app.Alias", module="app", line=-1, column=-1, python_3_12_type_alias=True
    )
    hint = TypeAliasType(alias, [])
    tree = plugin._Tree(api=None, context=Context(), root=None, modules=set())  # type: ignore[arg-type]

    assert plugin._client(hint) is None
    assert tree._type_name(hint) == "Alias"


def test_every_error_of_a_tree_is_reported_once(tmp_path: Path) -> None:
    write(
        tmp_path,
        app="""
        from nuke_di import DI, Client


        class Leaf(Client):
            def __init__(self, count: int, name) -> None: ...  # type: ignore[no-untyped-def]


        class Left(Client):
            def __init__(self, leaf: Leaf) -> None: ...


        class Right(Client):
            def __init__(self, leaf: Leaf, left: Left) -> None: ...


        class Root(Client):
            def __init__(self, left: Left, right: Right) -> None: ...


        DI.resolve(Root)
        """,
    )

    assert mypy(tmp_path, "app.py") == [
        (
            "app.py",
            20,
            'Argument "count" of "Leaf.__init__" is int, which is not a client (resolving Root -> Left -> Leaf)',
        ),
        ("app.py", 20, 'Argument "name" of "Leaf.__init__" has no type hint (resolving Root -> Left -> Leaf)'),
    ]


def test_inject_job_and_worker_start_the_path_at_the_function(tmp_path: Path) -> None:
    write(
        tmp_path,
        app="""
        from nuke_di import DI, Client, Dependencies, Shutdown, job, worker


        class Broken(Client):
            def __init__(self, count: int) -> None: ...


        class Container(Dependencies):
            pass


        class Service:
            def handle(self, broken: Broken) -> None: ...


        async def handler(user_id: int, broken: Broken) -> None: ...
        async def untyped(user_id, shutdown: Shutdown) -> None: ...  # type: ignore[no-untyped-def]
        async def untyped_broken(broken: Broken, user_id) -> None: ...  # type: ignore[no-untyped-def]


        DI.inject(handler)
        Container().inject(handler)
        DI.inject(untyped)
        DI.inject(Service().handle)
        DI.inject(untyped_broken)


        @job
        async def once(broken: Broken) -> None: ...


        @worker(hooks=[])
        async def forever(broken: Broken, shutdown: Shutdown) -> None: ...


        @job(hooks=[])
        async def fine(shutdown: Shutdown, day: int = 1) -> None: ...
        """,
    )

    path = "(resolving {} -> Broken)"
    message = 'Argument "count" of "Broken.__init__" is int, which is not a client '
    assert mypy(tmp_path, "app.py") == [
        ("app.py", 21, message + path.format("handler")),
        ("app.py", 22, message + path.format("handler")),
        ("app.py", 23, 'Argument "user_id" of "untyped" has no type hint'),
        # A bound method, as the container names it
        ("app.py", 24, message + path.format("handle")),
        # The type hints first, as the container checks them before it resolves a client
        ("app.py", 25, 'Argument "user_id" of "untyped_broken" has no type hint'),
        ("app.py", 25, message + path.format("untyped_broken")),
        ("app.py", 28, message + path.format("once")),
        ("app.py", 32, message + path.format("forever")),
    ]


def test_inject_returns_the_function_without_its_clients(tmp_path: Path) -> None:
    write(
        tmp_path,
        app="""
        from typing import reveal_type

        from nuke_di import DI, Client


        class Database(Client):
            pass


        class Broken(Client):
            def __init__(self, count: int) -> None: ...


        def first(db: Database, user_id: int, page: int = 1) -> str:
            return ""


        async def middle(user_id: int, db: Database, page: int, *rest: int, size: int = 10, **extra: str) -> str:
            return ""


        def positional_only(db: Database, /, user_id: int) -> str:
            return ""


        def broken(user_id: int, broken: Broken) -> str:
            return ""


        def positional_default(db: Database = Database(), /, user_id: int = 0) -> str:
            return ""


        reveal_type(DI.inject(first))
        reveal_type(DI.inject(middle))
        reveal_type(DI.inject(positional_only))
        reveal_type(DI.inject(broken))
        reveal_type(DI.inject(Database))
        reveal_type(DI.inject(positional_default))
        # A positional value lands on the place of the client: partial() raises TypeError
        DI.inject(first)(1)
        DI.inject(first)(user_id=1)
        DI.inject(middle)(1, page=2, size=3, x="y").close()
        DI.inject(middle)(1, 2).close()
        """,
    )

    revealed = 'Revealed type is "{}"'
    any_function = revealed.format("def (*Any, **Any) -> str")
    assert mypy(tmp_path, "app.py") == [
        ("app.py", 34, revealed.format("def (*, user_id: int, page: int =) -> str")),
        (
            "app.py",
            35,
            revealed.format(
                "def (user_id: int, *, page: int, size: int =, **extra: str) -> typing.Coroutine[Any, Any, str]"
            ),
        ),
        ("app.py", 36, any_function),
        (
            "app.py",
            37,
            'Argument "count" of "Broken.__init__" is int, which is not a client (resolving broken -> Broken)',
        ),
        ("app.py", 37, any_function),
        ("app.py", 38, revealed.format("def (*Any, **Any) -> app.Database")),
        # A positional-only client fails the call of the partial: the result keeps the type of `inject()`
        ("app.py", 39, any_function),
        ("app.py", 41, 'Too many positional arguments for "first"'),
        ("app.py", 44, 'Too many positional arguments for "middle"'),
    ]


def test_what_mypy_cannot_tell_is_left_to_the_container(tmp_path: Path) -> None:
    write(
        tmp_path,
        app="""
        from typing import Any, NewType, overload

        from nuke_di import DI, Client
        from untyped_library import Pool  # type: ignore[import-not-found]


        def make() -> type[Client]:
            return Client


        Made = make()


        class Database(Client):
            pass


        class Variable(Client):
            def __init__(self, a: Made, b: Pool | None, c: Made | None) -> None: ...  # type: ignore[valid-type]


        def wrap(func: Any) -> Any:
            return func


        class Decorated(Client):
            @wrap
            def __init__(self, count: int) -> None: ...


        class Overloaded(Client):
            @overload
            def __init__(self, count: int) -> None: ...
            @overload
            def __init__(self, count: str) -> None: ...
            def __init__(self, count: int | str) -> None: ...


        class Other:
            def resolve(self, cls: type[Variable]) -> None: ...


        def resolve_any(cls: type[Overloaded]) -> None:
            DI.resolve(cls)


        Id = NewType("Id", Database)


        class UsesNewType(Client):
            def __init__(self, db: Id) -> None: ...


        DI.resolve(Variable)
        DI.resolve(Decorated)
        DI.resolve(Overloaded)
        Other().resolve(Variable)
        DI.resolve(UsesNewType)
        DI.resolve(int)  # type: ignore[type-var]
        """,
    )

    assert mypy(tmp_path, "app.py") == [
        (
            "app.py",
            58,
            'Argument "db" of "UsesNewType.__init__" is app.Id, which is not a client (resolving UsesNewType)',
        ),
    ]


def test_a_warm_cache_reports_the_same(tmp_path: Path) -> None:
    write(
        tmp_path,
        clients="""
        from nuke_di import Client


        class Database(Client):
            pass


        class PositionalOnly(Client):
            def __init__(self, db: Database, /) -> None: ...
        """,
        calls="""
        from nuke_di import DI
        from clients import PositionalOnly

        DI.resolve(PositionalOnly)
        """,
    )
    expected = [
        (
            "calls.py",
            4,
            'Argument "db" of "PositionalOnly.__init__" is positional-only, a client is passed by keyword '
            "(resolving PositionalOnly)",
        )
    ]
    assert mypy(tmp_path, "calls.py", "clients.py", cache=True) == expected

    # Only `calls` is checked again, `clients` comes from the cache without its definitions
    (tmp_path / "calls.py").write_text((tmp_path / "calls.py").read_text() + "\nDI.resolve(PositionalOnly)\n")

    assert mypy(tmp_path, "calls.py", "clients.py", cache=True) == [*expected, ("calls.py", 6, expected[0][2])]


@pytest.mark.parametrize("hook", [True, False], ids=["indirect-deps-hook", "module-refs"])
def test_a_change_deep_in_the_tree_checks_the_call_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hook: bool
) -> None:
    # mypy before 2.4 has no hook for the dependencies of a module
    monkeypatch.setattr("nuke_di.mypy.INDIRECT_DEPS_HOOK", hook)
    leaf = """
    from nuke_di import Client


    class Leaf(Client):
        def __init__(self, count: int{}) -> None: ...
    """
    write(
        tmp_path,
        leaf=leaf.format(" = 1"),
        middle="""
        from nuke_di import Client
        from leaf import Leaf


        class Middle(Client):
            def __init__(self, leaf: Leaf) -> None: ...
        """,
        top="""
        from nuke_di import Client
        from middle import Middle


        class Top(Client):
            def __init__(self, middle: Middle) -> None: ...
        """,
        calls="""
        from nuke_di import DI
        from top import Top

        DI.resolve(Top)
        """,
    )
    files = ["calls.py", "top.py", "middle.py", "leaf.py"]
    assert mypy(tmp_path, *files, cache=True) == []

    # `calls` imports `top` only, whose interface stays the same
    write(tmp_path, leaf=leaf.format(""))

    assert mypy(tmp_path, *files, cache=True) == [
        (
            "calls.py",
            4,
            'Argument "count" of "Leaf.__init__" is int, which is not a client (resolving Top -> Middle -> Leaf)',
        )
    ]


def test_the_guide_example_prints_what_the_guide_shows(tmp_path: Path) -> None:
    text = document(page="clients")
    blocks = split(text[text.index("\n## Checking the tree with mypy\n") :])[1]
    source = next(block for block in blocks if "# tree.py" in block)
    console = next(block for block in blocks if "$ mypy tree.py" in block)
    # Without the fences: the `# tree.py` line is line 1
    (tmp_path / "tree.py").write_text("\n".join(source.splitlines()[1:-1]) + "\n")
    (tmp_path / "pyproject.toml").write_text('[tool.mypy]\nplugins = ["nuke_di.mypy"]\n')

    stdout, _, status = run(tmp_path, ["--cache-dir", "/dev/null", "tree.py"])

    assert stdout.splitlines() == console.splitlines()[2:-1]
    assert status == 1


@pytest.fixture(autouse=True)
def _forget_modules() -> Iterator[None]:
    # Every test writes its own `clients` and `app`
    yield
    for name in ("clients", "calls", "app", "orders", "billing", "aliases"):
        sys.modules.pop(name, None)
