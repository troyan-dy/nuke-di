"""
What the plugin knows of nuke-di, and the names of classes and type hints as the container writes them.
"""

from collections import Counter
from collections.abc import Iterable

from mypy.errorcodes import ErrorCode
from mypy.messages import format_type_bare
from mypy.nodes import TypeInfo
from mypy.options import Options
from mypy.plugin import Plugin
from mypy.types import Instance, Type, TypeAliasType, get_proper_type

CLIENT = "nuke_di.types.NotSingletonClient"
CONTAINER = "nuke_di.core.Dependencies"
ENTRYPOINTS = frozenset(("nuke_di.entrypoint.job", "nuke_di.entrypoint.worker"))
# What `job(hooks=...)` and `worker(hooks=...)` return: the decorator call is a call of its `__call__`
DECORATOR = "nuke_di.entrypoint.EntrypointDecorator.__call__"
DATACLASS = "nuke_di.dataclass.client_dataclass"
# The key of a client's `TypeInfo.metadata`, which the cache keeps
METADATA = "nuke_di"

# mypy 2.4 asks plugins for the dependencies of a module; before, the checker collects them in `module_refs`
INDIRECT_DEPS_HOOK = hasattr(Plugin, "get_additional_indirect_deps")

NUKE_DI = ErrorCode("nuke-di", "The container cannot build the clients of a call", "nuke-di")


def class_name(type_info: TypeInfo) -> str:
    """
    The name of a class as the container shows it: its qualified name without the module, and without the
    `@line` that mypy adds to a class local to a function.
    """
    qualname = type_info.fullname.removeprefix(f"{type_info.module_name}.")
    parts = (part.partition("@")[0] for part in qualname.split("."))
    return ".".join(parts)


def display_names(classes: Iterable[TypeInfo]) -> dict[TypeInfo, str]:
    """
    The names of `classes`, as `Dependencies._names` gives them: in full, with the module, for the classes that
    share a name.
    """
    names = {type_info: class_name(type_info) for type_info in classes}
    counts = Counter(names.values())
    return {type_info: _display_name(type_info, counts) for type_info in names}


def _display_name(type_info: TypeInfo, counts: Counter[str]) -> str:
    name = class_name(type_info)
    return f"{type_info.module_name}.{name}" if counts[name] > 1 else name


def type_statement(hint: Type) -> str | None:
    """
    The name of the alias of a `type` statement that the hint is: an alias object at runtime, not the class it
    stands for.
    """
    if isinstance(hint, TypeAliasType) and hint.alias is not None and hint.alias.python_3_12_type_alias:
        return hint.alias.name
    return None


def type_name(hint: Type, options: Options) -> str:
    """
    A type hint as the container writes it: a class by its qualified name, `Outer.Inner`.
    """
    alias = type_statement(hint)
    if alias is not None:
        return alias
    proper = get_proper_type(hint)
    if isinstance(proper, Instance) and proper.type.is_newtype:
        # The repr of a NewType, which the container falls back to, has the module
        module = proper.type.module_name
        return f"{module}.{class_name(proper.type)}"
    if isinstance(proper, Instance) and not proper.args:
        return class_name(proper.type)
    return format_type_bare(hint, options)
