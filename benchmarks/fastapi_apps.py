"""
The applications of the `fastapi` scenario of benchmarks/run.py: a handler that takes a client through nuke-di,
the same through a plain FastAPI `Depends()`, and one with no dependencies. Importing it needs fastapi.
"""

from typing import Annotated, Final

from fastapi import Depends, FastAPI

from nuke_di import Client
from nuke_di.fastapi import setup


class Database(Client):
    """
    The no-op client of the handler of nuke-di.
    """


class PlainDatabase:
    """
    The same dependency without nuke-di, a plain object.
    """


# async, as the binding of nuke-di: a sync dependency would run in a threadpool
async def get_db() -> PlainDatabase:
    return PlainDatabase()


nuke_app = FastAPI()
setup(nuke_app)
plain_app = FastAPI()


@nuke_app.get("/nuke")
async def with_client(db: Database) -> dict[str, bool]:
    return {"ok": True}


@plain_app.get("/depends")
async def with_depends(db: Annotated[PlainDatabase, Depends(get_db)]) -> dict[str, bool]:
    return {"ok": True}


@plain_app.get("/plain")
async def without_dependencies() -> dict[str, bool]:
    return {"ok": True}


# The Shape column, the application and the path of every row
ROWS: Final = (
    ("a client through nuke-di", nuke_app, "/nuke"),
    ("a plain FastAPI Depends()", plain_app, "/depends"),
    ("no dependencies", plain_app, "/plain"),
)
