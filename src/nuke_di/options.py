import os
from dataclasses import dataclass, field

CONNECT_TIMEOUT_ENV = "CONNECT_TIMEOUT_SECONDS"
DEFAULT_CONNECT_TIMEOUT = 30.0


def _connect_timeout_from_env() -> float:
    return float(os.environ.get(CONNECT_TIMEOUT_ENV, DEFAULT_CONNECT_TIMEOUT))


@dataclass
class DependenciesSettings:
    connect_timeout: float = field(default_factory=_connect_timeout_from_env)
