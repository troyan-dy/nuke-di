class NotSingletonClient:
    """
    Base class for dependencies managed by `Dependencies`.

    A new instance is created for every consumer that declares it.
    """

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
