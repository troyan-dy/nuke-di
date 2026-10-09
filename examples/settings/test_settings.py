import pytest
from nuke_di import Dependencies

from settings.clients import Cache, Database, Settings
from settings.show import show


async def test_every_client_gets_the_test_settings(di: Dependencies) -> None:
    # Fields left out still come from the environment or the defaults
    test_settings = di.mock(Settings, Settings(database_url="sqlite://", pool_size=1))
    db, cache = di.resolve(Database), di.resolve(Cache)
    async with di:
        assert db.settings is cache.settings is test_settings


async def test_the_job_with_override(di: Dependencies, capsys: pytest.CaptureFixture[str]) -> None:
    with di.override(Settings, Settings(database_url="sqlite://", pool_size=2)):
        injected = di.inject(show)
        async with di:
            await injected()

    assert "database: connected to sqlite://, pool of 2" in capsys.readouterr().out


def test_settings_read_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POOL_SIZE", "20")
    monkeypatch.setenv("DEBUG", "yes")
    settings = Settings()
    assert (settings.pool_size, settings.debug) == (20, True)


def test_a_bad_value_fails_early() -> None:
    with pytest.raises(ValueError, match="POOL_SIZE must be at least 1"):
        Settings(pool_size=0)
