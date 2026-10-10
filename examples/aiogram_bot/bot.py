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
    assert message.from_user is not None
    await message.answer(await users.greet(message.from_user.id, message.from_user.first_name))


@router.message(Command("stats"))
async def stats(message: Message, users: UserService) -> None:
    await message.answer(await users.stats())


dp = Dispatcher()
dp.include_router(router)
setup(dp)  # clients connect before the startup handlers, disconnect after the shutdown handlers


@dp.startup()
async def announce(bot: Bot, users: UserService) -> None:
    # A startup handler takes clients too, connected already
    print(f"startup: bot {bot.id}, {await users.stats()}")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(levelname)s %(name)s: %(message)s")
    await dp.start_polling(Bot(os.environ["BOT_TOKEN"]))


if __name__ == "__main__":
    asyncio.run(main())
