import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar, cast, dataclass_transform, overload

from nuke_di.types import Client

ClassT = TypeVar("ClassT")

# The attributes of a class that the client class made from it gets of its own
_NOT_COPIED = frozenset(("__dict__", "__weakref__"))


@overload
@dataclass_transform()
def client_dataclass(_cls: type[ClassT]) -> type[ClassT]: ...


@overload
@dataclass_transform()
def client_dataclass(
    _cls: None = None,
    **dataclass_kwargs: Any,
) -> Callable[[type[ClassT]], type[ClassT]]: ...


@dataclass_transform()
def client_dataclass(
    _cls: type[ClassT] | None = None, **dataclass_kwargs: Any
) -> type[ClassT] | Callable[[type[ClassT]], type[ClassT]]:
    """
    Decorator that makes a class a `Client` subclass and a dataclass.

    Accepts the same keyword arguments as `dataclasses.dataclass`. Typed as an identity decorator, so a class
    that does not subclass `Client` itself is a client at runtime only: write `class Checkout(Client)` for the
    type checkers to see it.
    """

    def decorator(target: type[ClassT]) -> type[ClassT]:
        return cast(type[ClassT], dataclass(**dataclass_kwargs)(_as_client(target)))

    if inspect.isclass(_cls):
        return decorator(_cls)

    return decorator


def _as_client(target: type) -> type:
    """
    `target` if it is a client already, otherwise a client class made of it.
    """
    if issubclass(target, Client):
        return target
    attributes = target.__dict__.items()
    own = {name: attribute for name, attribute in attributes if name not in _NOT_COPIED}
    # A type checker cannot order the methods of `type[ClassT]` and `Client`: the bases are plain types to it
    bases = cast(tuple[type, ...], (target, Client))
    return type(target.__name__, bases, own)
