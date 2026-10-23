"""
How the command line gives the value of a Parameter: the type argparse converts a string with, the help text,
and the hint without its `Annotated[...]` and `| None`. Imported only by nuke_di.cli.
"""

import argparse
import datetime
import enum
import inspect
import types
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Union, get_args, get_origin

from nuke_di.options import Option

Converter = Callable[[str], Any]


def converter(hint: Any) -> dict[str, Any] | None:
    """
    The arguments of `add_argument()` that convert a value to `hint`, or `None` for a type the command line does
    not give.
    """
    if hint in (str, int, float, Path):
        return {"type": hint}
    if hint in (datetime.date, datetime.datetime):
        return {"type": _FromIsoformat(hint)}
    if get_origin(hint) is not None or not isinstance(hint, type):
        return None
    if issubclass(hint, enum.Enum):
        names = ",".join(member.name for member in hint)
        return {"type": _ByName(hint), "metavar": f"{{{names}}}"}
    return None


class _FromIsoformat:
    """
    Converts an ISO 8601 string to a date or a datetime.
    """

    def __init__(self, date_type: type[datetime.date]) -> None:
        self.date_type = date_type
        # argparse names the type in its error: "invalid date value: 'x'"
        self.__name__ = date_type.__name__

    def __call__(self, text: str) -> datetime.date:
        return self.date_type.fromisoformat(text)


class _ByName:
    """
    Converts the name of a member of an enum to the member.
    """

    def __init__(self, enum_type: type[enum.Enum]) -> None:
        self.enum_type = enum_type
        self.__name__ = enum_type.__name__

    def __call__(self, text: str) -> enum.Enum:
        try:
            return self.enum_type[text]
        except KeyError:
            names = ", ".join(member.name for member in self.enum_type)
            raise argparse.ArgumentTypeError(f"invalid choice: {text!r} (choose from {names})") from None


def help_text(option: Option, argument: inspect.Parameter) -> str | None:
    """
    The help of a Parameter: the help of its Option, and the default of its argument.
    """
    parts = [] if option.help is None else [option.help]
    default = argument.default
    # A default of None means "not given", showing it says nothing
    if default is not argument.empty and default is not None:
        parts.append(f"(default: {_format_default(default)})")
    # argparse expands %-formatting in help texts
    return " ".join(parts).replace("%", "%%") or None


def _format_default(default: Any) -> str:
    if isinstance(default, enum.Enum):
        return default.name
    if isinstance(default, list | tuple):
        return " ".join(map(_format_default, default))
    return str(default)


def strip_annotated(hint: Any, option: Option | None) -> tuple[Any, Option | None]:
    if get_origin(hint) is Annotated:
        hint, *metadata = get_args(hint)
        option = next((extra for extra in metadata if isinstance(extra, Option)), option)
    return hint, option


def strip_optional(hint: Any, option: Option | None) -> tuple[Any, Option | None]:
    if get_origin(hint) in {Union, types.UnionType}:
        args = [arg for arg in get_args(hint) if arg is not types.NoneType]
        if len(args) == 1:
            return strip_annotated(args[0], option)
    return hint, option
