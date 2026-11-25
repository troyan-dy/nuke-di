import os
from dataclasses import dataclass, field

CONNECT_TIMEOUT_ENV = "CONNECT_TIMEOUT_SECONDS"
DEFAULT_CONNECT_TIMEOUT = 30.0

DISCONNECT_TIMEOUT_ENV = "DISCONNECT_TIMEOUT_SECONDS"
DEFAULT_DISCONNECT_TIMEOUT = 10.0

DISCONNECT_DEADLINE_ENV = "DISCONNECT_DEADLINE_SECONDS"
DEFAULT_DISCONNECT_DEADLINE = 20.0


def _connect_timeout_from_env() -> float:
    return float(os.environ.get(CONNECT_TIMEOUT_ENV, DEFAULT_CONNECT_TIMEOUT))


def _disconnect_timeout_from_env() -> float:
    return float(os.environ.get(DISCONNECT_TIMEOUT_ENV, DEFAULT_DISCONNECT_TIMEOUT))


def _disconnect_deadline_from_env() -> float:
    return float(os.environ.get(DISCONNECT_DEADLINE_ENV, DEFAULT_DISCONNECT_DEADLINE))


@dataclass
class DependenciesSettings:
    connect_timeout: float = field(default_factory=_connect_timeout_from_env)
    disconnect_timeout: float = field(default_factory=_disconnect_timeout_from_env)
    # The budget of a whole disconnect(), seconds
    disconnect_deadline: float = field(default_factory=_disconnect_deadline_from_env)

    def __post_init__(self) -> None:
        # Written so that NaN fails too: every comparison with it is False
        if not self.connect_timeout >= 0:
            raise ValueError(f"connect_timeout must be >= 0, got {self.connect_timeout}")
        if not self.disconnect_timeout >= 0:
            raise ValueError(f"disconnect_timeout must be >= 0, got {self.disconnect_timeout}")
        if not self.disconnect_deadline >= 0:
            raise ValueError(f"disconnect_deadline must be >= 0, got {self.disconnect_deadline}")
