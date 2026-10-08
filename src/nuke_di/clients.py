import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from nuke_di.logs import fields
from nuke_di.types import Client

logger = logging.getLogger(__name__)


class Shutdown(Client):
    """
    The request for a Run to stop.

    A Run sets it on the first termination signal. Outside a Run nothing sets it.
    """

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def is_set(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()

    def set(self) -> None:
        self._event.set()


class BackgroundTasks(Client):
    """
    Supervises background tasks: logs their failures and cancels them on disconnect.
    """

    def __init__(self) -> None:
        # Strong references, so a running task is never garbage collected
        self._tasks: set[asyncio.Task[Any]] = set()
        self._watchers: list[Callable[[BaseException], object]] = []
        self._stopping = False

    def spawn(self, coro: Coroutine[Any, Any, Any], *, name: str | None = None) -> asyncio.Task[Any]:
        if self._stopping:
            coro.close()
            raise RuntimeError("BackgroundTasks is stopping, no new tasks are accepted")

        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._on_done)
        return task

    def watch(self, callback: Callable[[BaseException], object]) -> None:
        """
        Call `callback` with the exception of every task that fails.
        """
        self._watchers.append(callback)

    async def stop(self) -> None:
        """
        Cancel every running task and wait until all of them finish.
        """
        self._stopping = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def disconnect(self) -> None:
        await self.stop()

    def _on_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return

        exc = task.exception()
        if exc is None:
            return

        logger.error("Background task %s failed", task.get_name(), exc_info=exc, extra=fields())
        for callback in self._watchers:
            callback(exc)
