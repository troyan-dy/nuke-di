from nuke_di import Dependencies

from hello.main import Database, UserService, handler


async def test_handler(di: Dependencies) -> None:
    di.mock(Database).fetch_user.return_value = "alice"
    injected = di.inject(handler)
    async with di:
        assert await injected(1) == "Hello, alice!"


async def test_user_service(di: Dependencies) -> None:
    di.mock(Database).fetch_user.return_value = "bob"
    users = di.resolve(UserService)
    async with di:
        assert await users.greet(2) == "Hello, bob!"
