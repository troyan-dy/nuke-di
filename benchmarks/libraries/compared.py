"""
The libraries of a comparison, in the order of its columns.
"""

from collections.abc import Mapping
from importlib.metadata import version
from typing import Final

from libraries.dependency_injector import DEPENDENCY_INJECTOR
from libraries.dishka import DISHKA
from libraries.injector import INJECTOR
from libraries.nuke_di import NUKE_DI
from libraries.wireup import WIREUP

LIBRARIES: Final = (NUKE_DI, DISHKA, WIREUP, DEPENDENCY_INJECTOR, INJECTOR)


def versions() -> dict[str, str]:
    """
    The installed version of every library, by its name.
    """
    return {library.name: version(library.package) for library in LIBRARIES}


def listed(found: Mapping[str, str]) -> str:
    """
    The libraries and their versions on one line.
    """
    return " · ".join(f"{name} {number}" for name, number in found.items())
