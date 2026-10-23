"""
FastAPI: one handler per library, each taking one client through the library's integration.
"""

import sys
from typing import Final

import wireup
from dependency_injector import containers, providers
from dependency_injector.wiring import Provide, inject
from dishka import FromDishka, Provider, Scope, make_async_container
from dishka.integrations.fastapi import DishkaRoute, setup_dishka
from fastapi import Depends, FastAPI
from wireup.integration.fastapi import setup as wireup_setup

from nuke_di import Client
from nuke_di.fastapi import setup as nuke_di_setup

# What every handler returns
_OK_KEY: Final = "ok"


class Database(Client):
    """
    The no-op client every handler takes.
    """


nuke_di_app = FastAPI()
nuke_di_setup(nuke_di_app)


@nuke_di_app.get("/")
async def nuke_di_handler(db: Database) -> dict[str, bool]:
    return {_OK_KEY: True}


dishka_app = FastAPI()
dishka_app.router.route_class = DishkaRoute


@dishka_app.get("/")
async def dishka_handler(db: FromDishka[Database]) -> dict[str, bool]:
    return {_OK_KEY: True}


dishka_provider = Provider(scope=Scope.APP)
dishka_provider.provide(Database)
setup_dishka(make_async_container(dishka_provider), dishka_app)

wireup_app = FastAPI()


@wireup_app.get("/")
async def wireup_handler(db: wireup.Injected[Database]) -> dict[str, bool]:
    return {_OK_KEY: True}


wireup_setup(wireup.create_async_container(injectables=[wireup.injectable(Database)]), wireup_app)


class DependencyInjectorContainer(containers.DeclarativeContainer):
    db = providers.Singleton(Database)


dependency_injector_app = FastAPI()


@dependency_injector_app.get("/")
@inject
async def dependency_injector_handler(
    # dependency-injector's documented FastAPI wiring, compared as written
    db: Database = Depends(Provide[DependencyInjectorContainer.db]),  # noqa: B008, WPS404
) -> dict[str, bool]:
    return {_OK_KEY: True}


DependencyInjectorContainer().wire(modules=[sys.modules[__name__]])

APPS: Final = (
    ("nuke-di", nuke_di_app),
    ("dishka", dishka_app),
    ("wireup", wireup_app),
    ("dependency-injector", dependency_injector_app),
)
