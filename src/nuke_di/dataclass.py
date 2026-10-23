import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar, cast, dataclass_transform, overload

from nuke_di.types import Client

ClassT = TypeVar("ClassT")


@overload
@dataclass_transform()
def client_dataclass(_cls: type[ClassT]) -> type[ClassT]: ...


@overload
@dataclass_transform()
def client_dataclass(_cls: None = None, **dataclass_kwargs: Any) -> Callable[[type[ClassT]], type[ClassT]]: ...


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
        if not issubclass(target, Client):
            attrs = {name: value for name, value in target.__dict__.items() if name not in {"__dict__", "__weakref__"}}
            # A type checker cannot order the methods of `type[ClassT]` and `Client`: the bases are plain types to it
            bases = cast(tuple[type, ...], (target, Client))
            client_class = type(target.__name__, bases, attrs)
        else:
            client_class = target

        return cast(type[ClassT], dataclass(**dataclass_kwargs)(client_class))

    if inspect.isclass(_cls):
        return decorator(_cls)

    return decorator
