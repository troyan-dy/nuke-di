import os
from dataclasses import dataclass, field

CONNECT_TIMEOUT_ENV = "CONNECT_TIMEOUT_SECONDS"
DEFAULT_CONNECT_TIMEOUT = 30.0

DISCONNECT_TIMEOUT_ENV = "DISCONNECT_TIMEOUT_SECONDS"
DEFAULT_DISCONNECT_TIMEOUT = 10.0

CONNECT_CONCURRENCY_ENV = "CONNECT_CONCURRENCY"
DEFAULT_CONNECT_CONCURRENCY = 0


def _connect_timeout_from_env() -> float:
    return float(os.environ.get(CONNECT_TIMEOUT_ENV, DEFAULT_CONNECT_TIMEOUT))


def _disconnect_timeout_from_env() -> float:
    return float(os.environ.get(DISCONNECT_TIMEOUT_ENV, DEFAULT_DISCONNECT_TIMEOUT))


def _connect_concurrency_from_env() -> int:
    return int(os.environ.get(CONNECT_CONCURRENCY_ENV, DEFAULT_CONNECT_CONCURRENCY))


@dataclass(frozen=True)
class Option:
    """
    Command-line metadata of an entrypoint Parameter, given through `Annotated[T, Option(...)]`.
    """

    help: str | None = None
    # One letter or digit, e.g. "d" for "-d"
    short: str | None = None


@dataclass
class DependenciesSettings:
    connect_timeout: float = field(default_factory=_connect_timeout_from_env)
    disconnect_timeout: float = field(default_factory=_disconnect_timeout_from_env)
    # How many clients may connect or disconnect at once; 0 means no limit
    connect_concurrency: int = field(default_factory=_connect_concurrency_from_env)

    def __post_init__(self) -> None:
        if self.disconnect_timeout < 0:
            raise ValueError(f"disconnect_timeout must be >= 0, got {self.disconnect_timeout}")
        if self.connect_concurrency < 0:
            raise ValueError(f"connect_concurrency must be >= 0, got {self.connect_concurrency}")
