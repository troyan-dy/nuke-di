from collections.abc import Callable
from typing import Any


class NotSingletonClient:
    """
    Base class for dependencies managed by `Dependencies`.

    A new instance is created for every consumer that declares it.
    """

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: Callable[[Any], Any]) -> Any:
        """
        Explain a client where pydantic expects a field type, e.g. in an endpoint of a FastAPI router without
        `ClientRoute`; pydantic is not imported, and a model that allows arbitrary types keeps working.
        """
        try:
            return handler(source)
        except Exception as exc:
            raise TypeError(
                f"{cls.__name__} is a nuke-di client, not a pydantic type. FastAPI fills it only in the routes "
                f"of an app set up with setup(app) and of a ClientRouter, both from nuke_di.fastapi; not in "
                f"websocket endpoints, nor in dependencies given to app.include_router()"
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
