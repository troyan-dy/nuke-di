"""
Scenes for tests/test_aiogram.py, apart from its string annotations.
"""

from aiogram.fsm.scene import Scene, on
from aiogram.types import Message

from tests.test_aiogram import Database, UserService


class Quiz(Scene, state="quiz"):
    @on.message()
    async def answer(self, message: Message, users: UserService) -> None: ...


class Survey(Scene, state="survey"):
    @on.message.enter()
    async def start(self, message: Message, db: Database) -> None: ...

    @on.message()
    async def answer(self, message: Message) -> None: ...


class Plain(Scene, state="plain"):
    @on.message()
    async def answer(self, message: Message) -> str:
        return "plain"
