from contextvars import ContextVar
from typing import Any, Final

from nuke_di.timings import ClientTiming

# The logger of the container's records, named after the module that held the whole container up to 1.19: the
# guide's log lines and the filters of applications show this name
CONTAINER_LOGGER: Final = "nuke_di.core"

# The name of the Run in progress; tasks started inside the Run inherit it
current_run: ContextVar[str | None] = ContextVar("nuke_di_run", default=None)


def fields(**given: Any) -> dict[str, Any]:
    """
    The structured fields of a log record, for `logging`'s `extra=`: the given ones that are set, and the Run.
    """
    extra = {key: field_value for key, field_value in given.items() if field_value is not None}
    run = current_run.get()
    if run is not None:
        extra["run"] = run
    return extra


def client_fields(timing: ClientTiming, duration: float | None = None) -> dict[str, Any]:
    """
    The structured fields of a log record about one client.
    """
    return fields(client=timing.name, duration=duration)
