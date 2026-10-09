from nuke_di import Shutdown, worker

from testing.clients import Queue, Signups


@worker
async def signups(queue: Queue, users: Signups, shutdown: Shutdown) -> None:
    while not shutdown.is_set():
        email = await queue.get()
        user_id = await users.register(email)
        print(f"signups: registered {email} as user {user_id}")
    print("signups: stopped")
