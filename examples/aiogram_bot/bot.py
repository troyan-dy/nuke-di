import asyncio
import logging
import os
import sys

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from nuke_di.aiogram import setup

from aiogram_bot.clients import UserService

router = Router(name="users")


@router.message(CommandStart())
async def start(message: Message, users: UserService) -> None:
    sender = message.from_user
    assert sender is not None
    greeting = await users.greet(sender.id, sender.first_name)
    await message.answer(greeting)


@router.message(Command("stats"))
async def stats(message: Message, users: UserService) -> None:
    await message.answer(await users.stats())


dp = Dispatcher()
dp.include_router(router)
setup(dp)  # clients connect before the startup handlers, disconnect after the shutdown handlers


@dp.startup()
async def announce(bot: Bot, users: UserService) -> None:
    # A startup handler takes clients too, connected already
    stats = await users.stats()
    print(f"startup: bot {bot.id}, {stats}")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(levelname)s %(name)s: %(message)s")
    await dp.start_polling(Bot(os.environ["BOT_TOKEN"]))


if __name__ == "__main__":
    asyncio.run(main())
