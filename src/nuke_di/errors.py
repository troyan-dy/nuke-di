class InvalidSignatureError(TypeError):
    """
    A client or function signature cannot be injected.
    """


class CircularDependencyError(InvalidSignatureError):
    """
    Clients depend on each other in a cycle, so none of them can be built.

    An `InvalidSignatureError` because the cycle is a mistake in the `__init__` signatures, not in the environment.
    """


class ConnectError(Exception):
    """
    An error occurred while connecting clients.
    """


class ConnectTimeoutError(ConnectError):
    """
    A client did not finish connecting in time.

    The timeout is set by the `CONNECT_TIMEOUT_SECONDS` environment variable.
    """


class InitializeDependencyError(Exception):
    """
    A dependency raised an exception during initialization.
    """
