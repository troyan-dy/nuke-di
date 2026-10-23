"""
The plugin: the hooks mypy calls, and the arguments of the clients recorded for the cache.
"""

from collections import defaultdict
from collections.abc import Callable

from mypy.nodes import FuncDef, MypyFile, TypeInfo
from mypy.options import Options
from mypy.plugin import ClassDefContext, FunctionContext, MethodContext, Plugin
from mypy.types import Type

from nuke_di.mypy.calls import _Calls
from nuke_di.mypy.names import CLIENT, CONTAINER, DATACLASS, DECORATOR, ENTRYPOINTS, METADATA


class NukeDIPlugin(Plugin):
    def __init__(self, options: Options) -> None:
        super().__init__(options)
        # The modules of the clients the calls of a module walked, by the path of the module
        self._walked: defaultdict[str, set[str]] = defaultdict(set)
        self._calls = _Calls(self._walked)

    def get_method_hook(self, fullname: str) -> Callable[[MethodContext], Type] | None:
        if fullname == DECORATOR:
            return self._calls.check_entrypoint
        # `owner` names the class of the object, a subclass of `Dependencies` included
        owner, _, method = fullname.rpartition(".")
        if method not in {"resolve", "inject"} or not self._subclasses(owner, CONTAINER):
            return None
        return self._calls.check_resolve if method == "resolve" else self._calls.check_inject

    def get_function_hook(self, fullname: str) -> Callable[[FunctionContext], Type] | None:
        return self._calls.check_entrypoint if fullname in ENTRYPOINTS else None

    def get_base_class_hook(self, fullname: str) -> Callable[[ClassDefContext], None] | None:
        return _record_arguments if self._subclasses(fullname, CLIENT) else None

    def get_class_decorator_hook(self, fullname: str) -> Callable[[ClassDefContext], None] | None:
        return _record_dataclass if fullname == DATACLASS else None

    def get_additional_indirect_deps(self, file: MypyFile) -> set[str]:  # noqa: WPS110
        """
        The modules of the clients the calls of `file` walked.

        A module is checked again when a module it depends on changes its interface, and a call depends on every
        client of its tree, not only on the ones it imports: `Checkout -> Profiles -> Postgres` breaks in the module
        of `Postgres`.
        """
        return self._walked.pop(file.path, set())

    def _subclasses(self, fullname: str, base: str) -> bool:
        """
        Whether `fullname` names a class that is `base` or a subclass of it.
        """
        node = self.lookup_fully_qualified(fullname)
        type_info = None if node is None else node.node
        return isinstance(type_info, TypeInfo) and type_info.has_base(base)


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
