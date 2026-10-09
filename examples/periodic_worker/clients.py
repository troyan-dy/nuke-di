import time

from nuke_di import Client


class Sessions(Client):
    """
    Sessions with an expiry time; an in-memory stand-in for a Redis or SQL table.
    """

    def __init__(self) -> None:
        self._expires_at: dict[str, float] = {}

    async def connect(self) -> None:
        # Demo data: session-1 expires in 0.5 seconds, session-2 in 1.5 and so on
        now = time.monotonic()
        for n in range(1, 6):
            self.add(f"session-{n}", ttl=n - 0.5, now=now)
        print(f"sessions: connected, {len(self)} sessions")

    async def disconnect(self) -> None:
        print("sessions: disconnected")

    def add(self, session_id: str, ttl: float, now: float | None = None) -> None:
        self._expires_at[session_id] = (time.monotonic() if now is None else now) + ttl

    async def delete_expired(self) -> list[str]:
        now = time.monotonic()
        expired = [session_id for session_id, expires_at in self._expires_at.items() if expires_at <= now]
        for session_id in expired:
            del self._expires_at[session_id]
        return expired

    def __len__(self) -> int:
        return len(self._expires_at)
