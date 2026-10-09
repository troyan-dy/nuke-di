"""
A mypy plugin that checks the client trees of a program while mypy checks its types.

Enable it in the mypy configuration, `plugins = ["nuke_di.mypy"]` under `[tool.mypy]` of `pyproject.toml`. At every
`resolve()`, `inject()`, `@job` and `@worker` it walks the `__init__` of every client the call would build and reports
what the container would raise, with the same message, so a broken tree fails `mypy` instead of the process.

Only what mypy keeps in its cache is read: a module checked from the cache has its classes and their types, not their
definitions, and a check must say the same with a cold cache and a warm one.
"""

from collections import Counter, defaultdict
from collections.abc import Callable
from typing import NamedTuple

from mypy.errorcodes import ErrorCode
from mypy.messages import format_type_bare
from mypy.nodes import (
    ARG_NAMED,
    ARG_NAMED_OPT,
    ARG_OPT,
    ARG_POS,
    ARG_STAR,
    ArgKind,
    Context,
    FuncDef,
    MypyFile,
    TypeInfo,
)
from mypy.options import Options
from mypy.plugin import CheckerPluginInterface, ClassDefContext, FunctionContext, MethodContext, Plugin
from mypy.types import (
    AnyType,
    CallableType,
    Instance,
    NoneType,
    ProperType,
    Type,
    TypeAliasType,
    TypeOfAny,
    UnboundType,
    UnionType,
    get_proper_type,
)

CLIENT = "nuke_di.types.NotSingletonClient"
CONTAINER = "nuke_di.core.Dependencies"
ENTRYPOINTS = frozenset({"nuke_di.entrypoint.job", "nuke_di.entrypoint.worker"})
# What `job(hooks=...)` and `worker(hooks=...)` return: the decorator call is a call of its `__call__`
DECORATOR = "nuke_di.entrypoint.EntrypointDecorator.__call__"
DATACLASS = "nuke_di.dataclass.client_dataclass"
# The key of a client's `TypeInfo.metadata`, which the cache keeps
METADATA = "nuke_di"

# mypy 2.4 asks plugins for the dependencies of a module; before, the checker collects them in `module_refs`
INDIRECT_DEPS_HOOK = hasattr(Plugin, "get_additional_indirect_deps")

NUKE_DI = ErrorCode("nuke-di", "The container cannot build the clients of a call", "nuke-di")


class _Argument(NamedTuple):
    """
    One argument of an `__init__` after `self`, as the container reads it.
    """

    name: str
    # None: no type hint
    hint: Type | None
    positional_only: bool
    has_default: bool


class NukeDIPlugin(Plugin):
    def __init__(self, options: Options) -> None:
        super().__init__(options)
        # The modules of the clients the calls of a module walked, by the path of the module
        self._walked: defaultdict[str, set[str]] = defaultdict(set)

    def get_method_hook(self, fullname: str) -> Callable[[MethodContext], Type] | None:
        if fullname == DECORATOR:
            return self._check_entrypoint
        cls, _, method = fullname.rpartition(".")
        if method not in {"resolve", "inject"} or not self._is_container(cls):
            return None
        return self._check_resolve if method == "resolve" else self._check_inject

    def get_function_hook(self, fullname: str) -> Callable[[FunctionContext], Type] | None:
        return self._check_entrypoint if fullname in ENTRYPOINTS else None

    def get_base_class_hook(self, fullname: str) -> Callable[[ClassDefContext], None] | None:
        return _record_arguments if self._subclasses(fullname, CLIENT) else None

    def get_class_decorator_hook(self, fullname: str) -> Callable[[ClassDefContext], None] | None:
        return _record_dataclass if fullname == DATACLASS else None

    def get_additional_indirect_deps(self, file: MypyFile) -> set[str]:
        """
        The modules of the clients the calls of `file` walked.

        A module is checked again when a module it depends on changes its interface, and a call depends on every
        client of its tree, not only on the ones it imports: `Checkout -> Profiles -> Postgres` breaks in the module
        of `Postgres`.
        """
        return self._walked.pop(file.path, set())

    def _is_container(self, fullname: str) -> bool:
        # `fullname` names the class of the object, a subclass of `Dependencies` included
        return self._subclasses(fullname, CONTAINER)

    def _subclasses(self, fullname: str, base: str) -> bool:
        """
        Whether `fullname` names a class that is `base` or a subclass of it.
        """
        node = self.lookup_fully_qualified(fullname)
        return node is not None and isinstance(node.node, TypeInfo) and node.node.has_base(base)

    def _tree(self, ctx: MethodContext | FunctionContext, root: str | None) -> "_Tree":
        if INDIRECT_DEPS_HOOK:
            modules = self._walked[ctx.api.path]
        else:
            modules = ctx.api.module_refs  # type: ignore[attr-defined]
        return _Tree(ctx.api, ctx.context, root, modules)

    def _check_resolve(self, ctx: MethodContext) -> Type:
        # A class named in the call: `type[Database]` may hold a subclass with another `__init__`
        cls = _first_argument(ctx)
        # A class that is no client fails the bound of `resolve()`, and mypy calls no hook for that call
        if isinstance(cls, CallableType) and cls.is_type_obj():
            self._tree(ctx, root=None).walk(cls.type_object())
        return ctx.default_return_type

    def _check_inject(self, ctx: MethodContext) -> Type:
        func = _first_argument(ctx)
        # A class is injected through its class annotations, not its `__init__`: left to the container
        if not isinstance(func, CallableType) or func.is_type_obj():
            return ctx.default_return_type
        clients = self._check_function(ctx, func)
        narrowed = None if clients is None else _without_clients(func, clients)
        return ctx.default_return_type if narrowed is None else narrowed

    def _check_entrypoint(self, ctx: MethodContext | FunctionContext) -> Type:
        # `job(hooks=...)` has no function yet: its decorator is checked when it is applied
        func = _first_argument(ctx)
        if isinstance(func, CallableType) and not func.is_type_obj():
            self._check_function(ctx, func)
        return ctx.default_return_type

    def _check_function(self, ctx: MethodContext | FunctionContext, func: CallableType) -> list[int] | None:
        """
        Check what `inject(func)` reads; the indexes of the client arguments of `func`, or None after an error.
        """
        # A bound method is "handle of Service" to mypy, `handle` to the container
        name = (func.name or "function").partition(" of ")[0]
        tree = self._tree(ctx, root=name)
        arguments = [
            (index, hint)
            for index, (kind, hint) in enumerate(zip(func.arg_kinds, func.arg_types, strict=True))
            if not kind.is_star()
        ]
        # Every argument has a type hint before any client is resolved, as `Dependencies._inspect` checks
        for index, hint in arguments:
            if _any_of(hint, TypeOfAny.unannotated):
                tree.fail(f'Argument "{func.arg_names[index] or f"#{index}"}" of "{name}" has no type hint', path=False)
        clients = []
        for index, hint in arguments:
            client = _client(hint)
            if client is not None:
                clients.append(index)
                tree.walk(client)
        return None if tree.failed else clients


def plugin(version: str) -> type[Plugin]:
    return NukeDIPlugin


def _record_arguments(ctx: ClassDefContext) -> None:
    """
    Keep the argument names of a client's own `__init__`, positional-only ones included.

    The type of a function has no name for a positional-only argument, and the definition that has it is not in the
    cache; the metadata of the class is.
    """
    init = ctx.cls.info.names.get("__init__")
    if init is not None and isinstance(init.node, FuncDef):
        names = [argument.variable.name for argument in init.node.arguments]
        ctx.cls.info.metadata.setdefault(METADATA, {})["arguments"] = names


def _record_dataclass(ctx: ClassDefContext) -> None:
    """
    Mark a class of `client_dataclass`: a client at runtime even when it does not subclass `Client`.
    """
    ctx.cls.info.metadata.setdefault(METADATA, {})["client"] = True


def _first_argument(ctx: MethodContext | FunctionContext) -> ProperType | None:
    return get_proper_type(ctx.arg_types[0][0]) if ctx.arg_types and ctx.arg_types[0] else None


def _without_clients(func: CallableType, clients: list[int]) -> CallableType | None:
    """
    The type of `partial(func, **clients)`: `func` without the client arguments.

    An argument after a client one is passed by keyword: a positional one would land on the client's place.
    """
    if any(func.arg_kinds[index] in {ARG_POS, ARG_OPT} and func.arg_names[index] is None for index in clients):
        # A positional-only client: the partial fails when it is called, the result keeps the type of `inject()`
        return None
    kinds: list[ArgKind] = []
    names: list[str | None] = []
    types: list[Type] = []
    keyword = False
    for index, (kind, name, hint) in enumerate(zip(func.arg_kinds, func.arg_names, func.arg_types, strict=True)):
        if index in clients:
            keyword = True
            continue
        if keyword and kind == ARG_STAR:
            # `*args` after a client: every value it would take lands on the client's place first
            continue
        if keyword and kind == ARG_POS:
            kind = ARG_NAMED
        elif keyword and kind == ARG_OPT:
            kind = ARG_NAMED_OPT
        kinds.append(kind)
        names.append(name)
        types.append(hint)
    return func.copy_modified(arg_kinds=kinds, arg_names=names, arg_types=types)


class _Tree:
    """
    The clients one call would build, walked in the order of the container, each class once.
    """

    def __init__(self, api: CheckerPluginInterface, context: Context, root: str | None, modules: set[str]) -> None:
        self.api = api
        self.context = context
        # The function `inject()` starts from, named first in every path
        self.root = root
        # Where the walked clients and their `__init__` are defined, the dependencies of the module of the call
        self.modules = modules
        # The clients whose arguments are being walked, outermost first
        self.path: list[TypeInfo] = []
        # The clients walked so far, the ones the container would have built by the next error
        self.walked: dict[str, TypeInfo] = {}
        self.failed = False

    def walk(self, info: TypeInfo) -> None:
        if info.fullname in self.walked:
            return
        if info in self.path:
            self.walked[info.fullname] = info
            self.fail(f"Circular dependency: {self._path(info)}", path=False)
            return
        self.path.append(info)
        self.modules.add(info.module_name)
        for argument in _arguments(info, self.modules):
            self._check_argument(info, argument)
        self.path.pop()
        self.walked[info.fullname] = info

    def _check_argument(self, info: TypeInfo, argument: _Argument) -> None:
        """
        The argument as `Dependencies._read_arguments` reads it, with its messages.
        """
        hint = argument.hint
        client = None if hint is None else _client(hint)
        if client is not None:
            if argument.positional_only:
                self._fail_argument(info, argument, "is positional-only, a client is passed by keyword")
            else:
                self.walk(client)
        elif argument.has_default:
            return
        elif hint is None:
            self._fail_argument(info, argument, "has no type hint")
        elif (optional := _optional_client(hint)) is not None:
            self._fail_argument(info, argument, f"is {_name(optional)} | None, a client cannot be optional")
        elif _surely_no_client(hint):
            self._fail_argument(info, argument, f"is {self._type_name(hint)}, which is not a client")

    def _type_name(self, hint: Type) -> str:
        """
        A type hint as the container writes it: a class by its qualified name, `Outer.Inner`.
        """
        alias = _type_statement(hint)
        if alias is not None:
            return alias
        proper = get_proper_type(hint)
        if isinstance(proper, Instance) and proper.type.is_newtype:
            # The repr of a NewType, which the container falls back to, has the module
            return f"{proper.type.module_name}.{_name(proper.type)}"
        if isinstance(proper, Instance) and not proper.args:
            return _name(proper.type)
        return format_type_bare(hint, self.api.options)

    def _fail_argument(self, info: TypeInfo, argument: _Argument, reason: str) -> None:
        self.fail(f'Argument "{argument.name}" of "{self._names(info)[info]}.__init__" {reason}')

    def fail(self, message: str, *, path: bool = True) -> None:
        self.failed = True
        suffix = f" (resolving {self._path()})" if path else ""
        self.api.fail(f"{message}{suffix}", self.context, code=NUKE_DI)

    def _path(self, *more: TypeInfo) -> str:
        classes = [*self.path, *more]
        names = self._names(*classes)
        path = [names[info] for info in classes]
        return " -> ".join(path if self.root is None else [self.root, *path])

    def _names(self, *more: TypeInfo) -> dict[TypeInfo, str]:
        """
        The names of the clients walked and of `more`, as `Dependencies._names` gives them: in full, with the module,
        for the classes that share a name.
        """
        classes = {*self.walked.values(), *self.path, *more}
        names = {info: _name(info) for info in classes}
        shared = {name for name, count in Counter(names.values()).items() if count > 1}
        return {info: f"{info.module_name}.{name}" if name in shared else name for info, name in names.items()}


def _arguments(info: TypeInfo, modules: set[str]) -> list[_Argument]:
    """
    The arguments of the `__init__` of `info` after `self`, without `*args` and `**kwargs`.

    An `__init__` that mypy has no plain function type for, decorated or overloaded, is left to the container.
    """
    init = info.get_method("__init__")
    if not isinstance(init, FuncDef) or init.info.fullname == "builtins.object":
        return []
    modules.add(init.info.module_name)
    # The type has no name for a positional-only argument, the names recorded on the class do
    recorded: list[str] | None = init.info.metadata.get(METADATA, {}).get("arguments")
    names: list[str | None] = list(recorded or init.arg_names)
    kinds = init.arg_kinds
    # An `__init__` without any type hint has no type at all
    hints: list[Type | None] = [None] * len(kinds)
    if isinstance(init.type, CallableType):
        hints = [None if _any_of(hint, TypeOfAny.unannotated) else hint for hint in init.type.arg_types]
    # `self` is the first argument, unless the method takes it through `*args`
    start = 1 if kinds and not kinds[0].is_star() else 0
    arguments = []
    for index in range(start, len(kinds)):
        if kinds[index].is_star():
            continue
        name = names[index]
        # mypy has no name for `__db` either, which Python mangles to `_Client__db` and passes by keyword. Without
        # the recorded names the argument is not called positional-only: a wrong error is worse than none
        positional_only = init.arg_names[index] is None and recorded is not None
        if name is not None and name.startswith("__") and not name.endswith("__"):
            name = f"_{init.info.name.partition('@')[0].lstrip('_')}{name}"
            positional_only = False
        arguments.append(_Argument(name or f"#{index}", hints[index], positional_only, kinds[index].is_optional()))
    return arguments


def _client(hint: Type) -> TypeInfo | None:
    """
    The client class of a type hint that the container fills: a class, not an alias of one.
    """
    if _type_statement(hint) is not None:
        return None
    proper = get_proper_type(hint)
    # A NewType of a client is a function at runtime, not a class
    if not isinstance(proper, Instance) or proper.type.is_newtype or not _is_client(proper.type):
        return None
    # `Repository[int]` is a generic alias at runtime; a bare `Repository` is the class, with `Any` parameters to mypy
    if any(not _any_of(arg, TypeOfAny.from_omitted_generics) for arg in proper.args):
        return None
    return proper.type


def _type_statement(hint: Type) -> str | None:
    """
    The name of the alias of a `type` statement that the hint is: an alias object at runtime, not the class it
    stands for.
    """
    if isinstance(hint, TypeAliasType) and hint.alias is not None and hint.alias.python_3_12_type_alias:
        return hint.alias.name
    return None


def _is_client(info: TypeInfo) -> bool:
    return info.has_base(CLIENT) or any(base.metadata.get(METADATA, {}).get("client") for base in info.mro)


def _optional_client(hint: Type) -> TypeInfo | None:
    """
    The first client of a union with None, as the container names it.
    """
    hint = get_proper_type(hint)
    if not isinstance(hint, UnionType):
        return None
    items = [get_proper_type(item) for item in hint.items]
    clients = [client for item in items if (client := _client(item)) is not None]
    return clients[0] if clients and any(isinstance(item, NoneType) for item in items) else None


def _any_of(hint: Type, kind: int) -> bool:
    """
    Whether the hint is an `Any` of the given kind: `TypeOfAny.unannotated` for an argument without a type hint,
    `from_omitted_generics` for the parameters of a bare generic class.
    """
    hint = get_proper_type(hint)
    return isinstance(hint, AnyType) and hint.type_of_any == kind


def _surely_no_client(hint: Type) -> bool:
    """
    Whether mypy knows that the hint is no client: an `Any` of an error or of a module it does not follow, or a
    variable used as a type, may be one at runtime, alone or in a union.
    """
    hint = get_proper_type(hint)
    if isinstance(hint, UnionType):
        return all(_surely_no_client(item) for item in hint.items)
    if isinstance(hint, AnyType):
        return hint.type_of_any == TypeOfAny.explicit
    return not isinstance(hint, UnboundType)


def _name(info: TypeInfo) -> str:
    """
    The name of a class as the container shows it: its qualified name without the module, and without the
    `@line` that mypy adds to a class local to a function.
    """
    qualname = info.fullname.removeprefix(f"{info.module_name}.")
    return ".".join(part.partition("@")[0] for part in qualname.split("."))
