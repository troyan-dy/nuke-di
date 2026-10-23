"""
The signature rewrite: the client arguments of a function, and of the dependencies it uses, annotated with the
framework's marker of a getter for the instance resolved on startup, so the framework's own DI fills them.

See docs/adr/0003-fastapi-signature-rewrite.md.
"""

import inspect
import weakref
from collections.abc import Callable
from typing import Annotated, Any, cast

from nuke_di.core import Dependencies
from nuke_di.integration.binding import Binding, DependsFramework, client_of
from nuke_di.integration.hints import _depends, _hint_class, _hints, _signature
from nuke_di.naming import sname
from nuke_di.types import NotSingletonClient


def bind(
    call: Callable[..., Any] | None,
    container: Dependencies,
    framework: DependsFramework,
) -> list[Binding]:
    """
    Rewrite the signature of `call` and of the dependencies it uses, so the framework fills its clients;
    return the clients of all of them.
    """
    if inspect.isclass(call):
        init: Callable[..., Any] = call.__init__
    elif inspect.isfunction(call):
        init = call
    else:
        # Bound methods, callable objects and the `None` of `Depends()`: no signature to replace in place
        return []

    bound = _bound(call, container, framework)
    if bound is not None:
        return bound

    # The signature as written, not the one a binding to another container replaced it with
    signature = call.__dict__.get("__nuke_di_signature__") or _signature(call)
    if signature is None:
        return []
    rewrite = _Rewrite(container, framework, _hints(init, signature))
    for argument in signature.parameters.values():
        rewrite.add(argument)

    rewrite.reachable.extend(rewrite.own)
    if rewrite.own:
        _install(call, signature, rewrite)
    # Otherwise there is nothing to rewrite here; the clients of its dependencies are found again on the next
    # declaration
    return rewrite.reachable


def _bound(
    call: Callable[..., Any],
    container: Dependencies,
    framework: DependsFramework,
) -> list[Binding] | None:
    """
    The clients `call` was bound with already, or `None` when it is to be bound now.
    """
    # `__dict__`, not getattr(): a subclass must not look bound because its base class is
    marks = call.__dict__
    owner = marks.get("__nuke_di_owner__")
    if owner is None:
        return None
    if owner[1] is not framework:
        raise TypeError(
            f"{sname(call)} takes clients in both {owner[1].name} and {framework.name} handlers: nuke-di rewrites "
            f"its signature for one framework, so give each framework its own function"
        )
    if owner[0]() is container or not framework.per_container:
        # Bound already, e.g. a route of an included router copied by an older FastAPI. With
        # `per_container=True` a function bound to another container is bound again: the handlers declared
        # before keep the dependencies they captured
        return cast(list[Binding], marks["__nuke_di_bindings__"])
    return None


class _Rewrite:
    """
    The arguments of one signature as the framework is to read them, and the clients found on the way.
    """

    def __init__(self, container: Dependencies, framework: DependsFramework, hints: dict[str, Any]) -> None:
        self.container = container
        self.framework = framework
        self.hints = hints
        self.arguments: list[inspect.Parameter] = []
        # The clients of the function itself, and those of the dependencies it uses
        self.own: list[Binding] = []
        self.reachable: list[Binding] = []

    def add(self, argument: inspect.Parameter) -> None:
        hint = self.hints.get(argument.name, argument.annotation)
        client = client_of(hint, self.framework.depends)
        if client is None:
            self._add_dependency(argument, hint)
        else:
            self._add_client(argument, client)

    def _add_dependency(self, argument: inspect.Parameter, hint: Any) -> None:
        depends = _depends(argument, hint, self.framework.depends)
        if depends is not None:
            # `Annotated[Auth, Depends()]` depends on the annotated class
            dependency = depends.dependency or _hint_class(hint)
            self.reachable.extend(bind(dependency, self.container, self.framework))
        self.arguments.append(argument.replace(annotation=hint))

    def _add_client(self, argument: inspect.Parameter, client: type[NotSingletonClient]) -> None:
        binding = Binding(client, self.container, self.framework)
        self.own.append(binding)
        make_getter = self.framework._getter
        getter = binding.get if make_getter is None else make_getter(binding)
        marker = self.framework.make_depends(getter)
        self.arguments.append(argument.replace(annotation=Annotated[client, marker]))


def _install(call: Callable[..., Any], signature: inspect.Signature, rewrite: _Rewrite) -> None:
    # Annotations are evaluated already: the framework would resolve strings against the wrong module otherwise
    # A class returns an instance of itself, not what its __init__ is annotated with
    if inspect.isclass(call):
        return_annotation = signature.empty
    else:
        return_annotation = rewrite.hints.get("return", signature.return_annotation)
    rewritten = signature.replace(parameters=rewrite.arguments, return_annotation=return_annotation)
    if inspect.isclass(call):
        call.__signature__ = _ClassSignature(call, rewritten)  # type: ignore[attr-defined]
    else:
        call.__signature__ = rewritten  # type: ignore[attr-defined]
    call.__nuke_di_owner__ = (weakref.ref(rewrite.container), rewrite.framework)  # type: ignore[attr-defined]
    call.__nuke_di_signature__ = signature  # type: ignore[attr-defined]
    call.__nuke_di_bindings__ = rewrite.reachable  # type: ignore[attr-defined]


class _ClassSignature:
    """
    The rewritten signature of one class, hidden from its subclasses, which inherit class attributes.
    """

    def __init__(self, owner: type, signature: inspect.Signature) -> None:
        self.owner = owner
        self.signature = signature

    def __get__(self, instance: object, owner: type) -> inspect.Signature | None:
        # `None` makes inspect.signature() compute the signature as usual
        return self.signature if instance is None and owner is self.owner else None
