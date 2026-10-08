import argparse
import datetime
import enum
import inspect
import types
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, NoReturn, Union, get_args, get_origin, get_type_hints

from nuke_di.errors import InvalidSignatureError
from nuke_di.types import NotSingletonClient
from nuke_di.utils import isa, sname

isnotsingleton = isa(NotSingletonClient)

Converter = Callable[[str], Any]


@dataclass(frozen=True)
class Option:
    """
    Command-line metadata of an entrypoint Parameter, given through `Annotated[T, Option(...)]`.
    """

    help: str | None = None
    # One letter or digit, e.g. "d" for "-d"
    short: str | None = None


class UsageError(Exception):
    """
    The command line does not match the Parameters of the entrypoint.
    """

    def __init__(self, message: str, usage: str) -> None:
        super().__init__(message)
        self.usage = usage


class HelpRequested(Exception):  # noqa: N818 - not an error, argparse already printed the help
    """
    `--help` was given and printed.
    """


class Parser(argparse.ArgumentParser):
    """
    Raises instead of exiting the process, so that the Run decides the exit code.
    """

    def error(self, message: str) -> NoReturn:
        raise UsageError(message, self.format_usage())

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        # Only the help action exits: errors go through `error()`
        raise HelpRequested


def build_parser(func: Callable[..., Any], prog: str | None = None) -> Parser:
    """
    Build the parser of the Parameters of `func`: its annotated arguments that are not clients.
    """
    parser = Parser(prog=prog, description=inspect.getdoc(func), allow_abbrev=False)
    hints = get_type_hints(func, include_extras=True)

    for param in inspect.signature(func).parameters.values():
        if param.kind in {param.VAR_POSITIONAL, param.VAR_KEYWORD}:
            continue
        if param.name not in hints:
            raise _signature_error(func, param, "has no type hint")

        hint, option = _strip_annotated(hints[param.name], None)
        if isnotsingleton(hint):
            if option is not None:
                raise _signature_error(func, param, "is a client and cannot have an Option")
            continue
        if param.kind is param.POSITIONAL_ONLY:
            raise _signature_error(func, param, "is positional-only, a Parameter is passed by keyword")

        _add_option(parser, func, param, *_strip_optional(hint, option))

    return parser


def parse(parser: argparse.ArgumentParser, argv: Sequence[str]) -> dict[str, Any]:
    """
    Return the Parameters given on the command line; the ones left out keep the defaults of the function.
    """
    return vars(parser.parse_args(argv))


def _add_option(
    parser: argparse.ArgumentParser,
    func: Callable[..., Any],
    param: inspect.Parameter,
    hint: Any,
    option: Option | None,
) -> None:
    option = option or Option()
    flags = ["--" + param.name.replace("_", "-")]
    if option.short is not None:
        if len(option.short) != 1 or not option.short.isalnum():
            raise _signature_error(func, param, f"has Option(short={option.short!r}), expected one letter or digit")
        flags.insert(0, "-" + option.short)

    kwargs: dict[str, Any] = {
        "dest": param.name,
        "default": argparse.SUPPRESS,
        "required": param.default is param.empty,
        "help": _help(option, param),
    }

    if hint is bool:
        kwargs["action"] = argparse.BooleanOptionalAction
    elif get_origin(hint) is list and len(get_args(hint)) == 1 and get_args(hint)[0] is not bool:
        kwargs |= {"action": "append", **_converter(func, param, get_args(hint)[0])}
    else:
        kwargs |= _converter(func, param, hint)

    try:
        parser.add_argument(*flags, **kwargs)
    except argparse.ArgumentError as exc:
        raise _signature_error(func, param, f"clashes with another option: {exc}") from exc


def _converter(func: Callable[..., Any], param: inspect.Parameter, hint: Any) -> dict[str, Any]:
    if hint in (str, int, float, Path):
        return {"type": hint}
    if hint in (datetime.date, datetime.datetime):
        return {"type": _from_isoformat(hint)}
    if get_origin(hint) is None and isinstance(hint, type) and issubclass(hint, enum.Enum):
        return {"type": _by_name(hint), "metavar": "{" + ",".join(member.name for member in hint) + "}"}
    raise _signature_error(func, param, f"has an unsupported type {hint!r}")


def _from_isoformat(cls: type[datetime.date]) -> Converter:
    def convert(value: str) -> datetime.date:
        return cls.fromisoformat(value)

    # argparse names the type in its error: "invalid date value: 'x'"
    convert.__name__ = cls.__name__
    return convert


def _by_name(cls: type[enum.Enum]) -> Converter:
    def convert(value: str) -> enum.Enum:
        try:
            return cls[value]
        except KeyError:
            names = ", ".join(member.name for member in cls)
            raise argparse.ArgumentTypeError(f"invalid choice: {value!r} (choose from {names})") from None

    return convert


def _help(option: Option, param: inspect.Parameter) -> str | None:
    parts = [] if option.help is None else [option.help]
    if param.default is not param.empty:
        parts.append(f"(default: {_show(param.default)})")
    # argparse expands %-formatting in help texts
    return " ".join(parts).replace("%", "%%") or None


def _show(value: Any) -> str:
    if isinstance(value, enum.Enum):
        return value.name
    if isinstance(value, list | tuple):
        return " ".join(map(_show, value))
    return str(value)


def _strip_annotated(hint: Any, option: Option | None) -> tuple[Any, Option | None]:
    if get_origin(hint) is Annotated:
        hint, *metadata = get_args(hint)
        option = next((item for item in metadata if isinstance(item, Option)), option)
    return hint, option


def _strip_optional(hint: Any, option: Option | None) -> tuple[Any, Option | None]:
    if get_origin(hint) in {Union, types.UnionType}:
        args = [arg for arg in get_args(hint) if arg is not type(None)]
        if len(args) == 1:
            return _strip_annotated(args[0], option)
    return hint, option


def _signature_error(func: Callable[..., Any], param: inspect.Parameter, reason: str) -> InvalidSignatureError:
    return InvalidSignatureError(f'Argument "{param.name}" of "{sname(func)}" {reason}')
