"""Resolved clients and their declared dependencies."""

from typing import NamedTuple, TypeAlias

from nuke_di.types import Client

ClientClass: TypeAlias = type[Client]


class Registration(NamedTuple):
    client: Client
    # An empty tuple is a real client with no dependencies. None denotes a replacement.
    dependencies: tuple[ClientClass, ...] | None


class BindingLifetime:
    """Invalidated when the container forgets the registrations a function captured."""

    def __init__(self) -> None:
        self.valid = True
