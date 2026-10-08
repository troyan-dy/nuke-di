from contextvars import ContextVar
from typing import Any

# The name of the Run in progress; tasks started inside the Run inherit it
current_run: ContextVar[str | None] = ContextVar("nuke_di_run", default=None)


def fields(**values: Any) -> dict[str, Any]:
    """
    The structured fields of a log record, for `logging`'s `extra=`: the given ones that are set, and the Run.
    """
    extra = {key: value for key, value in values.items() if value is not None}
    run = current_run.get()
    if run is not None:
        extra["run"] = run
    return extra
