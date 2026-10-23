import argparse
import inspect
from collections.abc import Callable, Sequence
from typing import Any, NoReturn, get_args, get_origin, get_type_hints

from nuke_di._converters import converter, help_text, strip_annotated, strip_optional
from nuke_di.errors import InvalidSignatureError, UsageError
from nuke_di.naming import sname
from nuke_di.options import Option
from nuke_di.types import isnotsingleton


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

    def add_parameter(
        self,
        func: Callable[..., Any],
        argument: inspect.Parameter,
        hint: Any,
        option: Option | None,
    ) -> None:
        """
        Add the option of the Parameter `argument` of `func`.
        """
        option = option or Option()
        dashed = argument.name.replace("_", "-")
        flags = [f"--{dashed}"]
        if option.short is not None:
            if len(option.short) != 1 or not option.short.isalnum():
                raise _signature_error(
                    func, argument, f"has Option(short={option.short!r}), expected one letter or digit"
                )
            flags.insert(0, f"-{option.short}")

        kwargs: dict[str, Any] = {
            "dest": argument.name,
            "default": argparse.SUPPRESS,
            "required": argument.default is argument.empty,
            "help": help_text(option, argument),
        }
        kwargs |= _action(func, argument, hint)
        try:
            self.add_argument(*flags, **kwargs)
        except argparse.ArgumentError as exc:
            raise _signature_error(func, argument, f"clashes with another option: {exc}") from exc


def build_parser(func: Callable[..., Any], prog: str | None = None) -> Parser:
    """
    Build the parser of the Parameters of `func`: its annotated arguments that are not clients.
    """
    parser = Parser(prog=prog, description=inspect.getdoc(func), allow_abbrev=False)
    hints = get_type_hints(func, include_extras=True)

    for argument in inspect.signature(func).parameters.values():
        parameter = _parameter(func, argument, hints)
        if parameter is not None:
            parser.add_parameter(func, argument, *parameter)

    return parser


def parse_parameters(parser: argparse.ArgumentParser, argv: Sequence[str]) -> dict[str, Any]:
    """
    Return the Parameters given on the command line; the ones left out keep the defaults of the function.
    """
    return parser.parse_args(argv).__dict__


def _parameter(
    func: Callable[..., Any],
    argument: inspect.Parameter,
    hints: dict[str, Any],
) -> tuple[Any, Option | None] | None:
    """
    The hint and the Option of the Parameter `argument` is, or `None` for `*args`, `**kwargs` and clients.
    """
    if argument.kind in {argument.VAR_POSITIONAL, argument.VAR_KEYWORD}:
        return None
    if argument.name not in hints:
        raise _signature_error(func, argument, "has no type hint")

    hint, option = strip_annotated(hints[argument.name], None)
    if isnotsingleton(hint):
        if option is not None:
            raise _signature_error(func, argument, "is a client and cannot have an Option")
        return None
    if argument.kind is argument.POSITIONAL_ONLY:
        raise _signature_error(func, argument, "is positional-only, a Parameter is passed by keyword")
    return strip_optional(hint, option)


def _action(
    func: Callable[..., Any],
    argument: inspect.Parameter,
    hint: Any,
) -> dict[str, Any]:
    """
    The arguments of `add_argument()` that give the value of a Parameter: a flag for a bool, the option given
    once per element for a list.
    """
    if hint is bool:
        return {"action": argparse.BooleanOptionalAction}
    elements: tuple[Any, ...] = get_args(hint) if get_origin(hint) is list else ()
    appended = len(elements) == 1 and elements[0] is not bool
    target = elements[0] if appended else hint
    converted = converter(target)
    if converted is None:
        raise _signature_error(func, argument, f"has an unsupported type {target!r}")
    return {"action": "append", **converted} if appended else converted


def _signature_error(func: Callable[..., Any], argument: inspect.Parameter, reason: str) -> InvalidSignatureError:
    return InvalidSignatureError(f'Argument "{argument.name}" of "{sname(func)}" {reason}')
