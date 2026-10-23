"""
The checks of the calls that build clients, `resolve()`, `inject()`, `@job` and `@worker`, and the type
`inject()` returns.
"""

from collections import defaultdict
from types import MappingProxyType
from typing import Final

from mypy.nodes import ARG_NAMED, ARG_NAMED_OPT, ARG_OPT, ARG_POS, ARG_STAR, ArgKind
from mypy.plugin import FunctionContext, MethodContext
from mypy.types import CallableType, ProperType, Type, TypeOfAny, get_proper_type

from nuke_di.mypy.hints import any_of, client
from nuke_di.mypy.names import INDIRECT_DEPS_HOOK
from nuke_di.mypy.tree import Tree

# What a positional argument after a client argument becomes: passed by keyword, a positional one would land on
# the client's place
_BY_KEYWORD: Final = MappingProxyType({ARG_POS: ARG_NAMED, ARG_OPT: ARG_NAMED_OPT})


class _Calls:
    """
    The hooks of the calls that build clients, sharing the modules each module's calls walked.
    """

    def __init__(self, walked: defaultdict[str, set[str]]) -> None:
        self._walked = walked

    def check_resolve(self, ctx: MethodContext) -> Type:
        # A class named in the call: `type[Database]` may hold a subclass with another `__init__`
        called = first_argument(ctx)
        # A class that is no client fails the bound of `resolve()`, and mypy calls no hook for that call
        if isinstance(called, CallableType) and called.is_type_obj():
            self._tree(ctx, root=None).walk(called.type_object())
        return ctx.default_return_type

    def check_inject(self, ctx: MethodContext) -> Type:
        func = first_argument(ctx)
        # A class is injected through its class annotations, not its `__init__`: left to the container
        if not isinstance(func, CallableType) or func.is_type_obj():
            return ctx.default_return_type
        clients = self._check_function(ctx, func)
        narrowed = None if clients is None else without_clients(func, clients)
        return ctx.default_return_type if narrowed is None else narrowed

    def check_entrypoint(self, ctx: MethodContext | FunctionContext) -> Type:
        # `job(hooks=...)` has no function yet: its decorator is checked when it is applied
        func = first_argument(ctx)
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
        # Every argument has a type hint before any client is resolved, as
        # `nuke_di._container.signatures.client_hints` checks
        _check_annotations(tree, func, name)
        clients = _walk_clients(tree, func)
        return None if tree.failed else clients

    def _tree(self, ctx: MethodContext | FunctionContext, root: str | None) -> Tree:
        if INDIRECT_DEPS_HOOK:
            modules = self._walked[ctx.api.path]
        else:
            modules = ctx.api.module_refs  # type: ignore[attr-defined]
        return Tree(ctx.api, ctx.context, root, modules)


def first_argument(ctx: MethodContext | FunctionContext) -> ProperType | None:
    # The types of the arguments given for the first formal argument
    given = ctx.arg_types[0] if ctx.arg_types else []
    return get_proper_type(given[0]) if given else None


def without_clients(func: CallableType, clients: list[int]) -> CallableType | None:
    """
    The type of `partial(func, **clients)`: `func` without the client arguments.

    An argument after a client one is passed by keyword: a positional one would land on the client's place.
    """
    if _positional_only_client(func, clients):
        # A positional-only client: the partial fails when it is called, the result keeps the type of `inject()`
        return None
    kept = _kept(func, clients)
    return func.copy_modified(
        arg_kinds=[kind for _, kind in kept],
        arg_names=[func.arg_names[index] for index, _ in kept],
        arg_types=[func.arg_types[index] for index, _ in kept],
    )


def _positional_only_client(func: CallableType, clients: list[int]) -> bool:
    for index in clients:
        positional = func.arg_kinds[index] in _BY_KEYWORD
        if positional and func.arg_names[index] is None:
            return True
    return False


def _kept(func: CallableType, clients: list[int]) -> list[tuple[int, ArgKind]]:
    """
    The arguments of `func` that are not clients, each with its kind once the clients are left out.
    """
    first = min(clients, default=len(func.arg_kinds))
    kept = []
    for index, kind in enumerate(func.arg_kinds):
        if index < first:
            kept.append((index, kind))
        elif index not in clients and kind != ARG_STAR:
            # Not `*args` after a client: every value it would take lands on the client's place first
            kept.append((index, _BY_KEYWORD.get(kind, kind)))
    return kept


def _check_annotations(tree: Tree, func: CallableType, name: str) -> None:
    for index, kind in enumerate(func.arg_kinds):
        unannotated = any_of(func.arg_types[index], TypeOfAny.unannotated)
        if unannotated and not kind.is_star():
            argument = func.arg_names[index] or f"#{index}"
            tree.fail(f'Argument "{argument}" of "{name}" has no type hint', path=False)


def _walk_clients(tree: Tree, func: CallableType) -> list[int]:
    """
    Walk the clients of the arguments of `func`, but `*args` and `**kwargs`; their indexes.
    """
    clients = []
    for index, kind in enumerate(func.arg_kinds):
        found = None if kind.is_star() else client(func.arg_types[index])
        if found is not None:
            clients.append(index)
            tree.walk(found)
    return clients
