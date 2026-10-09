import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar, cast, dataclass_transform, overload

from nuke_di.types import Client

C = TypeVar("C")


@overload
@dataclass_transform()
def client_dataclass(_cls: type[C]) -> type[C]: ...


@overload
@dataclass_transform()
def client_dataclass(_cls: None = None, **dataclass_kwargs: Any) -> Callable[[type[C]], type[C]]: ...


@dataclass_transform()
def client_dataclass(_cls: type[C] | None = None, **dataclass_kwargs: Any) -> type[C] | Callable[[type[C]], type[C]]:
    """
    Decorator that makes a class a `Client` subclass and a dataclass.

    Accepts the same keyword arguments as `dataclasses.dataclass`. Typed as an identity decorator, so a class
    that does not subclass `Client` itself is a client at runtime only: write `class Checkout(Client)` for the
    type checkers to see it.
    """

    def decorator(cls: type[C]) -> type[C]:
        if not issubclass(cls, Client):
            attrs = {name: value for name, value in cls.__dict__.items() if name not in {"__dict__", "__weakref__"}}
            # A type checker cannot order the methods of `type[C]` and `Client`: the bases are plain types to it
            bases = cast(tuple[type, ...], (cls, Client))
            new_cls = type(cls.__name__, bases, attrs)
        else:
            new_cls = cls

        return cast(type[C], dataclass(**dataclass_kwargs)(new_cls))

    if inspect.isclass(_cls):
        return decorator(_cls)

    return decorator
