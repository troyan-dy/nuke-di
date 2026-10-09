import httpx
import pytest
from nuke_di import Dependencies

from http_job.clients import PyPI, Settings
from http_job.versions import versions


async def test_versions_with_a_mocked_api(di: Dependencies, capsys: pytest.CaptureFixture[str]) -> None:
    latest = {"fastapi": "0.1.0", "litestar": "2.0.0"}
    di.mock(PyPI).latest_version.side_effect = latest.__getitem__  # an autospec mock: never connected, no network
    injected = di.inject(versions)
    async with di:
        await injected(package=["fastapi", "litestar"])

    assert capsys.readouterr().out == "fastapi: 0.1.0\nlitestar: 2.0.0\n"


async def test_pypi_client_over_a_mock_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYPI_URL", "https://pypi.test")
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, json={"info": {"version": "1.2.3"}})

    # A client is a class: build it by hand and drive its lifecycle, with the real httpx code in between
    pypi = PyPI(Settings(), transport=httpx.MockTransport(handler))
    await pypi.connect()
    try:
        assert await pypi.latest_version("fastapi") == "1.2.3"
    finally:
        await pypi.disconnect()

    assert requested == ["https://pypi.test/pypi/fastapi/json"]


async def test_pypi_client_raises_on_404() -> None:
    pypi = PyPI(Settings(), transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    await pypi.connect()
    try:
        with pytest.raises(httpx.HTTPStatusError, match="404 Not Found"):
            await pypi.latest_version("no-such-package")
    finally:
        await pypi.disconnect()
