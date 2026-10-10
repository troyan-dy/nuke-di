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
            raise TypeError(_not_a_pydantic_type(cls)) from exc

    @classmethod
    def __get_pydantic_json_schema__(cls, schema: Any, handler: Callable[[Any], Any]) -> Any:
        """
        Explain a client where pydantic fails to give it a JSON schema, which a model that allows arbitrary
        types has none of, e.g. in the arguments of an MCP tool declared without nuke_di.mcp; a generator that
        tolerates such types keeps its own answer.
        """
        try:
            return handler(schema)
        except Exception as exc:
            raise TypeError(_not_a_pydantic_type(cls)) from exc

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


def _not_a_pydantic_type(cls: type) -> str:
    return (
        f"{sname(cls)} is a nuke-di client, not a pydantic type. A pydantic model takes it only with "
        f"arbitrary_types_allowed, and has no JSON schema for it. A framework fills it only as a plain type hint, "
        f"not as an optional, through its nuke-di integration: e.g. in a FastAPI route declared through "
        f"nuke_di.fastapi, or in an MCP tool added after setup() of nuke_di.mcp or nuke_di.fastmcp. See "
        f"https://github.com/troyan-dy/nuke-di#documentation"
    )
