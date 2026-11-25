from nuke_di import entrypoint

from testing.clients import Queue, Signups


@entrypoint
async def signups(queue: Queue, users: Signups) -> None:
    email = await queue.get()
    user_id = await users.register(email)
    print(f"signups: registered {email} as user {user_id}")
