from dataclasses import dataclass
from typing import Literal

Outcome = Literal["ok", "failed", "timed_out", "cancelled"]


@dataclass
class ClientTiming:
    """
    How one client's connect() and disconnect() went in the last connect of its container.

    A phase that never started has no duration and no outcome: e.g. a client still waiting for its dependencies when
    another client failed, or one cancelled while waiting for CONNECT_CONCURRENCY.
    """

    name: str
    # Seconds spent in connect(), not counting the wait for CONNECT_CONCURRENCY
    connect: float | None = None
    connect_outcome: Outcome | None = None
    # Seconds spent in disconnect(), not counting the wait for CONNECT_CONCURRENCY
    disconnect: float | None = None
    disconnect_outcome: Outcome | None = None
