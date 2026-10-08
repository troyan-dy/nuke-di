class InvalidSignatureError(TypeError):
    """
    A signature cannot be injected: an argument without a type hint, a required `__init__` argument that is not
    a client, or an entrypoint Parameter that cannot be parsed.
    """


class CircularDependencyError(InvalidSignatureError):
    """
    Clients depend on each other in a cycle, so none of them can be built.
    """


class ConnectError(SystemExit):
    """
    An error occurred while connecting clients.

    It is a `SystemExit` on purpose: a client that failed to start should stop the application.
    """


class ConnectTimeoutError(ConnectError):
    """
    A client did not finish connecting in time.

    The timeout is set by the `CONNECT_TIMEOUT_SECONDS` environment variable.
    """


class InitializeDependencyError(SystemExit):
    """
    A dependency raised an exception during initialization.
    """


class UsageError(Exception):
    """
    The command line does not match the Parameters of an entrypoint.
    """

    def __init__(self, message: str, usage: str) -> None:
        super().__init__(message)
        self.usage = usage
