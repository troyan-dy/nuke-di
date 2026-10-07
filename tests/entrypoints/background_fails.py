import asyncio

from nuke_di import BackgroundTasks, worker


async def broken() -> None:
    raise RuntimeError("background boom")


@worker
async def background_fails(tasks: BackgroundTasks) -> None:
    tasks.spawn(broken(), name="broken")
    await asyncio.sleep(60)
