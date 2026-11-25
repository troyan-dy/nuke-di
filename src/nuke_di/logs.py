from typing import Any, Final

CONTAINER_LOGGER: Final = "nuke_di"


def fields(**given: Any) -> dict[str, Any]:
    return {key: field_value for key, field_value in given.items() if field_value is not None}


def client_fields(name: str, duration: float | None = None) -> dict[str, Any]:
    return fields(client=name, duration=duration)
