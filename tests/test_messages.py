"""
Every container error names the call, the state and the fix; a client is named unambiguously.
"""

import logging
import re
from typing import Any

import pytest

from nuke_di import (
    Client,
    ConnectError,
    ConnectTimeoutError,
    Dependencies,
    DependenciesSettings,
    InitializeDependencyError,
    InvalidSignatureError,
    NotSingletonClient,
)


class Database(Client):
    pass


class Users(Client):
    def __init__(self, db: Database) -> None:
        self.db = db


class Settings:
    """
    Not a client.
    """


class Reports(Client):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings


class Strict(Client):
    def __init__(self, db: Database) -> None:
        raise ValueError("pool: field required")


class Outer:
    class Inner(Client):
        pass


def other_module_client(name: str, module: str = "app.billing") -> type[Client]:
    """
    A client class with the name of another one, as a wrapper per package would be.
    """
    cls = type(name, (Client,), {})
    cls.__module__ = module
    cls.__qualname__ = name
    return cls


# #54: the state in the message


async def test_resolve_inject_mock_override_name_the_call_and_the_state() -> None:
    deps = Dependencies()
    deps.resolve(Users)
    await deps.connect()

    def handler(users: Users) -> None: ...

    expected = re.escape(
        "the container is already connected; resolve, inject, mock and override only work before connect(), "
        "flush() after disconnect()"
    )
    with pytest.raises(ConnectError, match=rf"^resolve\(Database\): {expected}$"):
        deps.resolve(Database)
    with pytest.raises(ConnectError, match=rf"^inject\(handler\): {expected}$"):
        deps.inject(handler)
    with pytest.raises(ConnectError, match=rf"^mock\(Database\): {expected}$"):
        deps.mock(Database)
    with pytest.raises(ConnectError, match=rf"^override\(Database\): {expected}$"), deps.override(Database):
        pass
    with pytest.raises(ConnectError, match=r"^flush\(\): already connected, call disconnect\(\) first$"):
        deps.flush()
    with pytest.raises(ConnectError, match=r"^connect\(\): already connected, call disconnect\(\) first$"):
        await deps.connect()
    await deps.disconnect()


async def test_disconnect_names_the_state() -> None:
    with pytest.raises(ConnectError, match=r"^disconnect\(\): already disconnected$"):
        await Dependencies().disconnect()


async def test_timeout_names_the_limit(caplog: pytest.LogCaptureFixture) -> None:
    class Hanging(Client):
        async def connect(self) -> None:
            raise TimeoutError

    deps = Dependencies(settings=DependenciesSettings(connect_timeout=2.5))
    deps.resolve(Hanging)

    with caplog.at_level(logging.ERROR, logger="nuke_di.core"), pytest.raises(ConnectTimeoutError) as info:
        await deps.connect()

    assert str(info.value) == "Hanging did not connect within 2.5s (CONNECT_TIMEOUT_SECONDS)"
    assert "Hanging did not connect within 2.5s (CONNECT_TIMEOUT_SECONDS)" in caplog.text


async def test_failed_connect_names_the_exception(caplog: pytest.LogCaptureFixture) -> None:
    class Broken(Client):
        async def connect(self) -> None:
            raise OSError("broker unreachable")

    deps = Dependencies()
    deps.resolve(Broken)

    with caplog.at_level(logging.ERROR, logger="nuke_di.core"), pytest.raises(ConnectError) as info:
        await deps.connect()

    assert str(info.value) == "Broken.connect() raised OSError: broker unreachable"
    assert isinstance(info.value.__cause__, OSError)
    assert "Broken.connect() raised OSError: broker unreachable" in caplog.text


async def test_failed_disconnect_is_logged_with_the_exception(caplog: pytest.LogCaptureFixture) -> None:
    class Broken(Client):
        async def disconnect(self) -> None:
            raise OSError("socket closed")

    deps = Dependencies()
    deps.resolve(Broken)
    await deps.connect()
    with caplog.at_level(logging.ERROR, logger="nuke_di.core"):
        await deps.disconnect()

    assert "Broken.disconnect() raised OSError: socket closed" in caplog.text


def test_init_error_names_the_exception_and_the_path() -> None:
    class Root(Client):
        def __init__(self, strict: Strict) -> None:
            self.strict = strict

    with pytest.raises(InitializeDependencyError) as info:
        Dependencies().resolve(Root)

    assert str(info.value) == "Strict.__init__ raised ValueError: pool: field required (resolving Root -> Strict)"
    assert isinstance(info.value.__cause__, ValueError)


def test_init_error_of_a_root_has_no_path() -> None:
    with pytest.raises(InitializeDependencyError, match=r"^Strict.__init__ raised ValueError: pool: field required$"):
        Dependencies().resolve(Strict)


# #55: a class that is not a client


@pytest.mark.parametrize(
    ("cls", "name"), [(Settings, "Settings"), (int, "int"), (type(None), "None"), ("Database", "'Database'")]
)
def test_resolve_refuses_what_is_not_a_client(cls: Any, name: str) -> None:
    with pytest.raises(
        InvalidSignatureError, match=rf"^{name} is not a client: subclass Client or NotSingletonClient$"
    ):
        Dependencies().resolve(cls)


def test_inject_refuses_a_function_that_takes_a_non_client() -> None:
    # `Settings` is looked up by the signature as a plain argument, so it is not resolved at all
    def handler(settings: Settings) -> None: ...

    injected = Dependencies().inject(handler)

    with pytest.raises(TypeError):
        injected()


def test_a_non_client_argument_is_named_with_the_path() -> None:
    with pytest.raises(
        InvalidSignatureError,
        match=r'^Argument "settings" of "Reports.__init__" is Settings, which is not a client \(resolving Reports\)$',
    ):
        Dependencies().resolve(Reports)


def test_a_non_client_is_refused_before_connect() -> None:
    deps = Dependencies()
    with pytest.raises(InvalidSignatureError):
        deps.resolve(Settings)  # type: ignore[type-var]
    assert not deps.connect_clients


# #64: unambiguous names


async def test_two_clients_with_one_name_are_told_apart_by_module(caplog: pytest.LogCaptureFixture) -> None:
    billing = other_module_client("Database")

    class Root(Client):
        def __init__(self, orders: Database, billing: billing) -> None:  # type: ignore[valid-type]
            self.orders, self.billing = orders, billing

    deps = Dependencies()
    deps.resolve(Root)
    with caplog.at_level(logging.INFO, logger="nuke_di.core"):
        await deps.connect()
    await deps.disconnect()

    names = [timing.name for timing in deps.timings]
    assert names == [f"{__name__}.Database", "app.billing.Database", "Root"]
    assert f"{__name__}.Database 0.00s" in caplog.text and "app.billing.Database 0.00s" in caplog.text


def test_two_clients_with_one_name_are_told_apart_in_the_path() -> None:
    billing = other_module_client("Database")

    class Broken(Client):
        def __init__(self, db: billing, pool: int) -> None: ...  # type: ignore[valid-type]

    class Root(Client):
        def __init__(self, orders: Database, broken: Broken) -> None: ...

    with pytest.raises(InvalidSignatureError) as info:
        Dependencies().resolve(Root)

    assert str(info.value) == (
        'Argument "pool" of "Broken.__init__" is int, which is not a client (resolving Root -> Broken)'
    )
    # The container holds the Replacement of the other `Database`, so the resolved one is named in full
    deps = Dependencies()
    deps.resolve(Database)
    deps.mock(billing)
    with pytest.raises(ConnectError) as error, deps.override(billing):
        pass
    assert str(error.value) == (
        f"override(Database) needs a container without resolved clients, found: {__name__}.Database"
    )


async def test_a_nested_class_keeps_its_outer_class() -> None:
    deps = Dependencies()
    deps.resolve(Outer.Inner)
    await deps.connect()
    await deps.disconnect()

    assert deps.timings[0].name == "Outer.Inner"


async def test_a_class_local_to_a_function_drops_the_function() -> None:
    class Local(NotSingletonClient):
        pass

    deps = Dependencies()
    deps.resolve(Local)
    await deps.connect()
    await deps.disconnect()

    assert deps.timings[0].name == "Local"


async def test_a_unique_name_stays_short() -> None:
    deps = Dependencies()
    deps.resolve(Users)
    await deps.connect()
    await deps.disconnect()

    assert [timing.name for timing in deps.timings] == ["Database", "Users"]
