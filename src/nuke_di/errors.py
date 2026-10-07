class InvalidSignatureError(TypeError):
    """
    A signature contains an argument without a type hint.
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
