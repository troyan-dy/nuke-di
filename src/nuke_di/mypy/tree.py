"""
The clients one call would build, walked as the container walks them, with its messages.
"""

from typing import NamedTuple

from mypy.nodes import Context, FuncDef, TypeInfo
from mypy.options import Options
from mypy.plugin import CheckerPluginInterface
from mypy.types import CallableType, Type, TypeOfAny

from nuke_di.mypy.hints import any_of, client, optional_client, surely_no_client
from nuke_di.mypy.names import METADATA, NUKE_DI, class_name, display_names, type_name


class Argument(NamedTuple):
    """
    One argument of an `__init__` after `self`, as the container reads it.
    """

    name: str
    # None: no type hint
    hint: Type | None
    positional_only: bool
    has_default: bool


class Tree:
    """
    The clients one call would build, walked in the order of the container, each class once.
    """

    def __init__(self, api: CheckerPluginInterface, context: Context, root: str | None, modules: set[str]) -> None:
        self._api = api
        self._context = context
        # The function `inject()` starts from, named first in every path
        self._root = root
        # Where the walked clients and their `__init__` are defined, the dependencies of the module of the call
        self._modules = modules
        # The clients whose arguments are being walked, outermost first
        self.path: list[TypeInfo] = []
        # The clients walked so far, the ones the container would have built by the next error
        self.walked: dict[str, TypeInfo] = {}
        self.failed = False

    def walk(self, type_info: TypeInfo) -> None:
        if type_info.fullname in self.walked:
            return
        if type_info in self.path:
            self.walked[type_info.fullname] = type_info
            self.fail(f"Circular dependency: {self._path(type_info)}", path=False)
            return
        self.path.append(type_info)
        self._modules.add(type_info.module_name)
        for argument in arguments(type_info, self._modules):
            self._check_argument(type_info, argument)
        self.path.pop()
        self.walked[type_info.fullname] = type_info

    def fail(self, message: str, *, path: bool = True) -> None:
        self.failed = True
        suffix = f" (resolving {self._path()})" if path else ""
        self._api.fail(f"{message}{suffix}", self._context, code=NUKE_DI)

    def _check_argument(self, type_info: TypeInfo, argument: Argument) -> None:
        """
        The argument as `Dependencies._read_arguments` reads it, with its messages.
        """
        found = None if argument.hint is None else client(argument.hint)
        if found is None:
            problem = argument_problem(argument, self._api.options)
            if problem is not None:
                self._fail_argument(type_info, argument, problem)
        elif argument.positional_only:
            self._fail_argument(type_info, argument, "is positional-only, a client is passed by keyword")
        else:
            self.walk(found)

    def _fail_argument(self, type_info: TypeInfo, argument: Argument, reason: str) -> None:
        names = display_names({*self.walked.values(), *self.path, type_info})
        self.fail(f'Argument "{argument.name}" of "{names[type_info]}.__init__" {reason}')

    def _path(self, *more: TypeInfo) -> str:
        classes = [*self.path, *more]
        names = display_names({*self.walked.values(), *classes})
        path = [names[type_info] for type_info in classes]
        if self._root is not None:
            path.insert(0, self._root)
        return " -> ".join(path)


def argument_problem(argument: Argument, options: Options) -> str | None:
    """
    What the container reports for an argument that is not a client, as `argument_problem()` of the container
    does; `None` for an argument it fills or leaves to its default.
    """
    hint = argument.hint
    if argument.has_default:
        return None
    if hint is None:
        return "has no type hint"
    optional = optional_client(hint)
    if optional is not None:
        return f"is {class_name(optional)} | None, a client cannot be optional"
    if surely_no_client(hint):
        return f"is {type_name(hint, options)}, which is not a client"
    return None


def arguments(type_info: TypeInfo, modules: set[str]) -> list[Argument]:
    """
    The arguments of the `__init__` of `type_info` after `self`, without `*args` and `**kwargs`.

    An `__init__` that mypy has no plain function type for, decorated or overloaded, is left to the container.
    """
    init = type_info.get_method("__init__")
    if not isinstance(init, FuncDef) or init.info.fullname == "builtins.object":
        return []
    modules.add(init.info.module_name)
    # `self` is the first argument, unless the method takes it through `*args`, which is left out as well
    after_self = enumerate(init.arg_kinds[1:], start=1)
    return [_argument_at(init, index) for index, kind in after_self if not kind.is_star()]


def _argument_at(init: FuncDef, index: int) -> Argument:
    # The type has no name for a positional-only argument, the names recorded on the class do
    recorded: list[str] | None = init.info.metadata.get(METADATA, {}).get("arguments")
    name = list(recorded or init.arg_names)[index]
    # mypy has no name for `__db` either, which Python mangles to `_Client__db` and passes by keyword. Without
    # the recorded names the argument is not called positional-only: a wrong error is worse than none
    positional_only = init.arg_names[index] is None and recorded is not None
    if name is not None and name.startswith("__") and not name.endswith("__"):
        name = _mangled(name, init)
        positional_only = False
    optional = init.arg_kinds[index].is_optional()
    return Argument(name or f"#{index}", _hint_at(init, index), positional_only, optional)


def _hint_at(init: FuncDef, index: int) -> Type | None:
    # An `__init__` without any type hint has no type at all
    if not isinstance(init.type, CallableType):
        return None
    hint = init.type.arg_types[index]
    return None if any_of(hint, TypeOfAny.unannotated) else hint


def _mangled(name: str, init: FuncDef) -> str:
    """
    The name Python mangles `__name` to in the class of `init`.
    """
    owner = init.info.name.partition("@")[0].lstrip("_")
    return f"_{owner}{name}"
