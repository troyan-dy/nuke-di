from collections.abc import Callable
from typing import Any

from nuke_di.utils import sname


class NotSingletonClient:
    """
    Base class for dependencies managed by `Dependencies`.

    A new instance is created for every consumer that declares it.
    """

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: Callable[[Any], Any]) -> Any:
        """
        Explain a client where pydantic expects a field type, e.g. in a FastAPI route declared without
        nuke_di.fastapi; pydantic is not imported, and a model that allows arbitrary types keeps working.
        """
        try:
            return handler(source)
        except Exception as exc:
            raise TypeError(
                f"{sname(cls)} is a nuke-di client, not a pydantic type. A pydantic model takes it only with "
                f"arbitrary_types_allowed; FastAPI fills it only as a plain type hint, not as an optional, in the "
                f"routes declared through nuke_di.fastapi: see docs/guide/fastapi.md in the nuke-di repository"
            ) from exc

    async def connect(self) -> None:
        """
        Override this method to run an action after `__init__`.
        """

    async def disconnect(self) -> None:
        """
        Override this method to run an action before the object is dropped.
        """


class Client(NotSingletonClient):
    """
    Singleton client.

    It is cached on resolution, so every consumer receives the same instance.
    """
