"""
The records of a connect() that succeeded.
"""

import logging

from nuke_di.logs import CONTAINER_LOGGER, client_fields, fields
from nuke_di.naming import count
from nuke_di.timings import ClientTiming

logger = logging.getLogger(CONTAINER_LOGGER)

# The connect() timings a startup record names
_SLOWEST = 3


def log_startup(timings: list[ClientTiming], timeout: float, duration: float) -> None:
    """
    Log how a connect() of `timings` went, and the clients that took more than half of `timeout` to connect.

    The records are the caller's, `stacklevel=2`: they keep the module of `Dependencies.connect()`, `core`, which the
    log lines of the guide show.
    """
    if not timings:
        return

    slowest = sorted(timings, key=_connect_seconds, reverse=True)[:_SLOWEST]
    logger.info(
        "Connected %s in %.2fs (slowest: %s)",
        count(len(timings), "client"),
        duration,
        ", ".join(f"{timing.name} {_connect_seconds(timing):.2f}s" for timing in slowest),
        extra=fields(duration=duration),
        stacklevel=2,
    )

    for timing in timings:
        if timing.connect is not None and timing.connect > timeout / 2:
            logger.warning(
                "Client %s took %.2fs to connect, more than half of CONNECT_TIMEOUT_SECONDS (%gs)",
                timing.name,
                timing.connect,
                timeout,
                extra=client_fields(timing, timing.connect),
                stacklevel=2,
            )


def _connect_seconds(timing: ClientTiming) -> float:
    return timing.connect or 0
