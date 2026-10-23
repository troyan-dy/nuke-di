from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from nuke_di import Dependencies

from http_job.clients import PyPI, Settings
from http_job.versions import versions


@asynccontextmanager
async def connected(pypi: PyPI) -> AsyncIterator[PyPI]:
    """
    `pypi` connected for the block, as the container would connect it.
    """
    await pypi.connect()
    try:
        yield pypi
    finally:
        await pypi.disconnect()


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
    async with connected(PyPI(Settings(), transport=httpx.MockTransport(handler))) as pypi:
        assert await pypi.latest_version("fastapi") == "1.2.3"

    assert requested == ["https://pypi.test/pypi/fastapi/json"]


async def test_pypi_client_raises_on_not_found() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(404))
    async with connected(PyPI(Settings(), transport=transport)) as pypi:
        with pytest.raises(httpx.HTTPStatusError, match="404 Not Found"):
            await pypi.latest_version("no-such-package")
