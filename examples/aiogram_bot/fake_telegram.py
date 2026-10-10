import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import GetMe, GetUpdates, SendMessage, TelegramMethod
from aiogram.types import Chat, Message, Update, User


def message(text: str, user_id: int = 7, first_name: str = "Ada", update_id: int = 1) -> Update:
    """
    An update with a message from a user in a private chat, as Telegram sends it.
    """
    user = {"id": user_id, "is_bot": False, "first_name": first_name}
    chat = {"id": user_id, "type": "private"}
    return Update.model_validate(
        {
            "update_id": update_id,
            "message": {"message_id": update_id, "date": 0, "chat": chat, "from": user, "text": text},
        }
    )


class FakeTelegram(BaseSession):
    """
    The Bot API in the process: getUpdates hands out `updates` and, once they are all handled, stops the
    polling of `dp`; sendMessage prints the message and keeps it in `sent`. No token, no network.
    """

    def __init__(self, dp: Dispatcher, updates: list[Update]) -> None:
        super().__init__()
        self.dp = dp
        self.updates = updates
        self.sent: list[str] = []

    async def make_request(self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None) -> Any:  # noqa: ASYNC109
        if isinstance(method, GetMe):
            return User(id=bot.id, is_bot=True, first_name="Demo", username="demo_bot")
        if isinstance(method, GetUpdates):
            if self.updates:
                updates, self.updates = self.updates, []
                return updates
            # Nothing more to come: stop the polling, as Ctrl+C would, and wait as a long poll does
            self.stopping = asyncio.create_task(self.dp.stop_polling())
            await asyncio.Event().wait()
        if isinstance(method, SendMessage):
            print(f"bot -> chat {method.chat_id}: {method.text}")
            self.sent.append(method.text)
            return Message(
                message_id=len(self.sent), date=datetime.now(UTC), chat=Chat(id=int(method.chat_id), type="private")
            )
        raise NotImplementedError(type(method).__name__)

    async def stream_content(self, *args: Any, **kwargs: Any) -> AsyncGenerator[bytes, None]:
        raise NotImplementedError
        yield b""

    async def close(self) -> None:
        pass
