import asyncio
import logging
import sys

from aiogram import Bot

from aiogram_bot.bot import dp
from aiogram_bot.fake_telegram import FakeTelegram, message


async def main() -> None:
    """
    Run the bot of bot.py against a fake Telegram: three messages come in, the replies are printed.
    """
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(levelname)s %(name)s: %(message)s")
    telegram = FakeTelegram(
        dp,
        [
            message("/start", user_id=7, first_name="Ada", update_id=1),
            message("/start", user_id=7, first_name="Ada", update_id=2),
            message("/start", user_id=8, first_name="Grace", update_id=3),
            message("/stats", user_id=8, first_name="Grace", update_id=4),
        ],
    )
    # One update after another, so the polling stops after the last reply
    await dp.start_polling(Bot("42:DEMO", session=telegram), handle_as_tasks=False)


if __name__ == "__main__":
    asyncio.run(main())
