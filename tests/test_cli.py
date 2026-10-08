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
    pass


class Color(enum.Enum):
    RED = "red"
    GREEN = "green"


def run(func: Callable[..., Any], *argv: str) -> dict[str, Any]:
    return parse_parameters(build_parser(func), list(argv))


def help_of(func: Callable[..., Any]) -> str:
    return build_parser(func, prog="python -m app.jobs.sync").format_help()


@pytest.mark.parametrize(
    ("annotation", "value", "expected"),
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
def test_scalar(annotation: Any, value: str, expected: Any) -> None:
    async def entry(x: annotation) -> None:
        pass

    assert run(entry, "--x", value) == {"x": expected}


def test_skips_clients_and_variadic_arguments() -> None:
    async def entry(db: Db, day: int, *args: Any, **kwargs: Any) -> None:
        pass

    assert run(entry, "--day", "1") == {"day": 1}


def test_client_inside_annotated_is_skipped() -> None:
    async def entry(db: Annotated[Db, "meta"]) -> None:
        pass

    assert run(entry) == {}


def test_underscores_become_dashes() -> None:
    async def entry(date_from: int) -> None:
        pass

    assert run(entry, "--date-from", "1") == {"date_from": 1}


def test_defaults_are_left_to_the_function() -> None:
    async def entry(day: int = 3, flag: bool = False, ids: list[int] | None = None) -> None:
        pass

    assert run(entry) == {}


def test_required_without_default() -> None:
    async def entry(day: int) -> None:
        pass

    with pytest.raises(UsageError, match="the following arguments are required: --day") as exc_info:
        run(entry)

    assert exc_info.value.usage.startswith("usage:")


def test_bool_flag() -> None:
    async def entry(dry_run: bool = False) -> None:
        pass

    assert run(entry, "--dry-run") == {"dry_run": True}
    assert run(entry, "--no-dry-run") == {"dry_run": False}


def test_bool_short_sets_true() -> None:
    async def entry(dry_run: Annotated[bool, Option(short="n")] = False) -> None:
        pass

    assert run(entry, "-n") == {"dry_run": True}


def test_optional_is_unwrapped() -> None:
    async def entry(a: int | None = None, b: Optional[int] = None) -> None:  # noqa: UP045 - both spellings
        pass

    assert run(entry, "--a", "1", "--b", "2") == {"a": 1, "b": 2}


def test_annotated_inside_optional() -> None:
    async def entry(day: Annotated[int, Option(short="d")] | None = None) -> None:
        pass

    assert run(entry, "-d", "5") == {"day": 5}


def test_list_is_repeated_option() -> None:
    async def entry(ids: list[int] | None = None) -> None:
        pass

    assert run(entry, "--ids", "1", "--ids", "2") == {"ids": [1, 2]}


def test_list_without_default_is_required() -> None:
    async def entry(ids: list[Color]) -> None:
        pass

    assert run(entry, "--ids", "RED") == {"ids": [Color.RED]}
    with pytest.raises(UsageError, match="required: --ids"):
        run(entry)


def test_short_option() -> None:
    async def entry(day: Annotated[int, Option(short="d")]) -> None:
        pass

    assert run(entry, "-d", "1") == {"day": 1}


@pytest.mark.parametrize(
    ("annotation", "value", "message"),
    [
        (int, "x", "argument --x: invalid int value: 'x'"),
        (datetime.date, "x", "argument --x: invalid date value: 'x'"),
        (datetime.datetime, "x", "argument --x: invalid datetime value: 'x'"),
        (Color, "red", r"argument --x: invalid choice: 'red' \(choose from RED, GREEN\)"),
    ],
)
def test_invalid_value(annotation: Any, value: str, message: str) -> None:
    async def entry(x: annotation) -> None:
        pass

    with pytest.raises(UsageError, match=message):
        run(entry, "--x", value)


def test_unknown_argument() -> None:
    async def entry() -> None:
        pass

    with pytest.raises(UsageError, match="unrecognized arguments: --day 1"):
        run(entry, "--day", "1")


def test_abbreviations_are_not_accepted() -> None:
    async def entry(date: int) -> None:
        pass

    with pytest.raises(UsageError, match="unrecognized arguments: --da"):
        run(entry, "--date", "1", "--da", "2")


def test_help_is_requested() -> None:
    async def entry() -> None:
        pass

    with pytest.raises(HelpRequested):
        run(entry, "--help")


def test_help_text() -> None:
    async def entry(
        db: Db,
        day: Annotated[datetime.date, Option(help="Day to sync, 100%", short="d")],
        color: Color = Color.RED,
        ids: list[int] | None = None,
        dry_run: Annotated[bool, Option(help="Only print")] = False,
        tags: list[str] = ["a", "b"],  # noqa: B006 - never mutated
    ) -> None:
        """Copy one day of changes."""

    text = help_of(entry)

    assert text.startswith("usage: python -m app.jobs.sync")
    assert "Copy one day of changes." in text
    # Python 3.13 shows the metavar once: "-d, --day DAY"
    assert re.search(r"-d( DAY)?, --day DAY", text)
    assert "Day to sync, 100%" in text
    assert "--color {RED,GREEN}" in text
    assert "(default: RED)" in text
    # None means "not given", showing it is noise
    assert "(default: None)" not in text
    assert "--dry-run, --no-dry-run" in text
    assert "Only print (default: False)" in text
    assert "(default: a b)" in text
    assert "--db" not in text


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
    async def entry(x: annotation) -> None:
        pass

    with pytest.raises(InvalidSignatureError, match='Argument "x" of "entry" has an unsupported type'):
        build_parser(entry)


def test_unannotated_argument() -> None:
    async def entry(x) -> None:  # type: ignore[no-untyped-def]
        pass

    with pytest.raises(InvalidSignatureError, match='Argument "x" of "entry" has no type hint'):
        build_parser(entry)


def test_positional_only_parameter() -> None:
    async def entry(day: int, /) -> None:
        pass

    with pytest.raises(InvalidSignatureError, match='Argument "day" of "entry" is positional-only'):
        build_parser(entry)


def test_option_on_client() -> None:
    async def entry(db: Annotated[Db, Option(help="no")]) -> None:
        pass

    with pytest.raises(InvalidSignatureError, match='Argument "db" of "entry" is a client'):
        build_parser(entry)


@pytest.mark.parametrize("short", ["", "dd", "-", "_"])
def test_invalid_short(short: str) -> None:
    async def entry(day: Annotated[int, Option(short=short)]) -> None:
        pass

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
    exec(func_source, namespace)  # noqa: S102 - fixed test sources

    with pytest.raises(InvalidSignatureError, match="conflicting option string"):
        build_parser(namespace["entry"])
