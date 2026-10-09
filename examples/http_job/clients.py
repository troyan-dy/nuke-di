import os

import httpx
from nuke_di import Client


class Settings(Client):
    """
    The environment, read once when the container builds the tree.
    """

    def __init__(self) -> None:
        self.pypi_url = os.environ.get("PYPI_URL", "https://pypi.org")
        self.timeout = float(os.environ.get("HTTP_TIMEOUT_SECONDS", "5"))


class PyPI(Client):
    """
    The PyPI JSON API over one httpx.AsyncClient: created in connect(), closed in disconnect().
    """

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        # `transport` is not a client and has a default, so the container leaves it alone;
        # a test passes httpx.MockTransport to stay offline
        self._settings = settings
        self._transport = transport
        self._http: httpx.AsyncClient | None = None

    async def connect(self) -> None:
        self._http = httpx.AsyncClient(
            base_url=self._settings.pypi_url,
            timeout=httpx.Timeout(self._settings.timeout),  # no request waits forever
            headers={"User-Agent": "nuke-di-examples"},
            transport=self._transport,
        )
        print(f"pypi: connected to {self._settings.pypi_url}")

    async def disconnect(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        print("pypi: disconnected")

    async def latest_version(self, package: str) -> str:
        if self._http is None:
            raise RuntimeError("PyPI is not connected")
        response = await self._http.get(f"/pypi/{package}/json")
        response.raise_for_status()
        version: str = response.json()["info"]["version"]
        return version
