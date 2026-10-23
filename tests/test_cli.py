import datetime
import enum
import re
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Optional

import pytest

from nuke_di import Client, InvalidSignatureError, Option, UsageError
from nuke_di.cli import HelpRequested, build_parser, parse_parameters


class Db(Client):
    """A client: the container fills it, the command line does not."""


class Color(enum.Enum):
    RED = "red"
    GREEN = "green"


def run(func: Callable[..., Any], *argv: str) -> dict[str, Any]:
    return parse_parameters(build_parser(func), list(argv))


def help_of(func: Callable[..., Any]) -> str:
    return build_parser(func, prog="python -m app.jobs.sync").format_help()


@pytest.mark.parametrize(
    ("annotation", "text", "expected"),
    [
        (str, "abc", "abc"),
        (int, "42", 42),
        (float, "1.5", 1.5),
        (Path, "data/x.csv", Path("data/x.csv")),
        (datetime.date, "2026-10-01", datetime.date(2026, 10, 1)),
        (datetime.datetime, "2026-10-01T12:30:00", datetime.datetime(2026, 10, 1, 12, 30)),
        (Color, "GREEN", Color.GREEN),
    ],
)
def test_scalar(annotation: Any, text: str, expected: Any) -> None:
    # The annotation is a variable on purpose: the parser reads it at runtime
    async def entry(option: annotation) -> None:  # pyright: ignore[reportInvalidTypeForm]
        ...

    assert run(entry, "--option", text) == {"option": expected}


def test_skips_clients_and_variadic_arguments() -> None:
    async def entry(db: Db, day: int, *args: Any, **kwargs: Any) -> None: ...

    assert run(entry, "--day", "1") == {"day": 1}


def test_client_inside_annotated_is_skipped() -> None:
    async def entry(db: Annotated[Db, "meta"]) -> None: ...

    assert run(entry) == {}


def test_underscores_become_dashes() -> None:
    async def entry(date_from: int) -> None: ...

    assert run(entry, "--date-from", "1") == {"date_from": 1}


def test_defaults_are_left_to_the_function() -> None:
    async def entry(
        day: int = 3,
        flag: bool = False,
        ids: list[int] | None = None,
    ) -> None: ...

    assert run(entry) == {}


def test_required_without_default() -> None:
    async def entry(day: int) -> None: ...

    with pytest.raises(UsageError, match="the following arguments are required: --day") as exc_info:
        run(entry)

    assert exc_info.value.usage.startswith("usage:")


def test_bool_flag() -> None:
    async def entry(dry_run: bool = False) -> None: ...

    assert run(entry, "--dry-run") == {"dry_run": True}
    assert run(entry, "--no-dry-run") == {"dry_run": False}


def test_bool_short_sets_true() -> None:
    async def entry(dry_run: Annotated[bool, Option(short="n")] = False) -> None: ...

    assert run(entry, "-n") == {"dry_run": True}


def test_optional_is_unwrapped() -> None:
    async def entry(first: int | None = None, second: Optional[int] = None) -> None:  # noqa: UP045 - both spellings
        ...

    assert run(entry, "--first", "1", "--second", "2") == {"first": 1, "second": 2}


def test_annotated_inside_optional() -> None:
    async def entry(
        day: Annotated[int, Option(short="d")] | None = None,
    ) -> None: ...

    assert run(entry, "-d", "5") == {"day": 5}


def test_list_is_repeated_option() -> None:
    async def entry(ids: list[int] | None = None) -> None: ...

    assert run(entry, "--ids", "1", "--ids", "2") == {"ids": [1, 2]}


def test_list_without_default_is_required() -> None:
    async def entry(ids: list[Color]) -> None: ...

    assert run(entry, "--ids", "RED") == {"ids": [Color.RED]}
    with pytest.raises(UsageError, match="required: --ids"):
        run(entry)


def test_short_option() -> None:
    async def entry(day: Annotated[int, Option(short="d")]) -> None: ...

    assert run(entry, "-d", "1") == {"day": 1}


@pytest.mark.parametrize(
    ("annotation", "text", "message"),
    [
        (int, "x", "argument --option: invalid int value: 'x'"),
        (datetime.date, "x", "argument --option: invalid date value: 'x'"),
        (datetime.datetime, "x", "argument --option: invalid datetime value: 'x'"),
        (Color, "red", r"argument --option: invalid choice: 'red' \(choose from RED, GREEN\)"),
    ],
)
def test_invalid_value(annotation: Any, text: str, message: str) -> None:
    # The annotation is a variable on purpose: the parser reads it at runtime
    async def entry(option: annotation) -> None:  # pyright: ignore[reportInvalidTypeForm]
        ...

    with pytest.raises(UsageError, match=message):
        run(entry, "--option", text)


def test_unknown_argument() -> None:
    async def entry() -> None: ...

    with pytest.raises(UsageError, match="unrecognized arguments: --day 1"):
        run(entry, "--day", "1")


def test_abbreviations_are_not_accepted() -> None:
    async def entry(date: int) -> None: ...

    with pytest.raises(UsageError, match="unrecognized arguments: --da"):
        run(entry, "--date", "1", "--da", "2")


def test_help_is_requested() -> None:
    async def entry() -> None: ...

    with pytest.raises(HelpRequested):
        run(entry, "--help")


async def documented(
    db: Db,
    day: Annotated[datetime.date, Option(help="Day to sync, 100%", short="d")],
    color: Color = Color.RED,
    ids: list[int] | None = None,
    dry_run: Annotated[bool, Option(help="Only print")] = False,
    tags: list[str] = ["a", "b"],  # noqa: B006, WPS404 - never mutated, a list default in the help
) -> None:
    """Copy one day of changes."""


def test_help_text_shows_the_usage_and_the_docstring() -> None:
    text = help_of(documented)

    assert text.startswith("usage: python -m app.jobs.sync")
    assert "Copy one day of changes." in text


def test_help_text_shows_the_options() -> None:
    text = help_of(documented)

    # Python 3.13 shows the metavar once: "-d, --day DAY"
    assert re.search(r"-d( DAY)?, --day DAY", text)
    assert "Day to sync, 100%" in text
    assert "--color {RED,GREEN}" in text
    assert "--dry-run, --no-dry-run" in text
    assert "--db" not in text


def test_help_text_shows_the_defaults() -> None:
    text = help_of(documented)

    assert "(default: RED)" in text
    # None means "not given", showing it is noise
    assert "(default: None)" not in text
    assert "Only print (default: False)" in text
    assert "(default: a b)" in text


@pytest.mark.parametrize(
    "annotation",
    [
        dict[str, int],
        tuple[int, int],
        list[bool],
        list[list[int]],
        list,
        int | str,
        object,
        type[int],
    ],
)
def test_unsupported_type(annotation: Any) -> None:
    # The annotation is a variable on purpose: the parser reads it at runtime
    async def entry(option: annotation) -> None:  # pyright: ignore[reportInvalidTypeForm]
        ...

    with pytest.raises(InvalidSignatureError, match='Argument "option" of "entry" has an unsupported type'):
        build_parser(entry)


def test_unannotated_argument() -> None:
    async def entry(option) -> None:  # type: ignore[no-untyped-def]
        ...

    with pytest.raises(InvalidSignatureError, match='Argument "option" of "entry" has no type hint'):
        build_parser(entry)


def test_positional_only_parameter() -> None:
    async def entry(day: int, /) -> None: ...

    with pytest.raises(InvalidSignatureError, match='Argument "day" of "entry" is positional-only'):
        build_parser(entry)


def test_option_on_client() -> None:
    async def entry(db: Annotated[Db, Option(help="no")]) -> None: ...

    with pytest.raises(InvalidSignatureError, match='Argument "db" of "entry" is a client'):
        build_parser(entry)


@pytest.mark.parametrize("short", ["", "dd", "-", "_"])
def test_invalid_short(short: str) -> None:
    async def entry(day: Annotated[int, Option(short=short)]) -> None: ...

    with pytest.raises(InvalidSignatureError, match="one letter or digit"):
        build_parser(entry)


@pytest.mark.parametrize(
    "func_source",
    [
        "async def entry(help: int) -> None: ...",
        "async def entry(day: Annotated[int, Option(short='h')]) -> None: ...",
        "async def entry(a: Annotated[int, Option(short='x')], b: Annotated[int, Option(short='x')]) -> None: ...",
    ],
)
def test_clashing_flags(func_source: str) -> None:
    namespace: dict[str, Any] = {"Annotated": Annotated, "Option": Option}
    exec(func_source, namespace)  # noqa: S102, WPS421 - fixed test sources

    with pytest.raises(InvalidSignatureError, match="conflicting option string"):
        build_parser(namespace["entry"])
