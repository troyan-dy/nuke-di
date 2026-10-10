from aiogram import Bot
from nuke_di import DI

from aiogram_bot.bot import dp
from aiogram_bot.clients import Database
from aiogram_bot.fake_telegram import FakeTelegram, message


class FakeDatabase(Database):
    async def add_visit(self, user_id: int) -> int:
        return 99

    async def count_users(self) -> int:
        return 1


async def test_start_greets_through_the_replaced_database() -> None:
    telegram = FakeTelegram(dp, [])
    bot = Bot("42:TEST", session=telegram)
    # Replaced before the startup, which resolves the clients; a Replacement is never connected
    with DI.override(Database, FakeDatabase()):
        await dp.emit_startup(bot=bot)
        try:
            await dp.feed_update(bot, message("/start", first_name="Tester"))
            await dp.feed_update(bot, message("/stats", update_id=2))
        finally:
            await dp.emit_shutdown(bot=bot)

    assert telegram.sent == ["Hello, Tester! This is your visit 99.", "1 users so far"]


async def test_polling_from_startup_to_shutdown() -> None:
    telegram = FakeTelegram(dp, [message("/start", update_id=1), message("/start", update_id=2)])

    await dp.start_polling(Bot("42:TEST", session=telegram), handle_as_tasks=False, handle_signals=False)

    assert telegram.sent == ["Hello, Ada! This is your visit 1.", "Hello, Ada! This is your visit 2."]
