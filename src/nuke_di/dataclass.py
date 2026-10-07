import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar, dataclass_transform

from nuke_di.types import Client, NotSingletonClient

C = TypeVar("C")
CT = TypeVar("CT", bound=NotSingletonClient)


@dataclass_transform()
def client_dataclass(_cls: type | None = None, **dataclass_kwargs: Any) -> Callable[[type[C]], type[CT]]:
    """
    Decorator that makes a class a `Client` subclass and a dataclass.

    Accepts the same keyword arguments as `dataclasses.dataclass`.
    """

    def decorator(cls: type[C]) -> type[CT]:
        if not issubclass(cls, Client):
            attrs = {name: value for name, value in cls.__dict__.items() if name not in {"__dict__", "__weakref__"}}
            new_cls = type(cls.__name__, (cls, Client), attrs)
        else:
            new_cls = cls

        return dataclass(**dataclass_kwargs)(new_cls)

    if inspect.isclass(_cls):
        return decorator(_cls)

    return decorator
