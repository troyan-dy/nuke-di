"""
What nuke-di knows of aiogram: the messages of a missing client, the names aiogram passes itself, and how it
reads a signature.
"""

from importlib import import_module
from importlib.util import find_spec
from types import MappingProxyType
from typing import Final, TypeAlias

from nuke_di.integration import Binding, Framework

# aiogram has no `Depends`: it passes a handler the items of its middleware data by name, see `_Clients`
_AIOGRAM = Framework(
    name="aiogram",
    not_started="{client} was not started with the dispatcher: register its handler before the dispatcher starts",
    not_connected=(
        "{client} is not connected: start the dispatcher with `start_polling()`, or `await dp.emit_startup()` in a test"
    ),
)

# What aiogram itself puts into the data of an update or the arguments of a startup handler: a client under
# one of these names would hide it from the handler and from the middlewares after nuke-di's
_RESERVED = frozenset(
    (
        "bot",
        "bots",
        "dispatcher",
        "router",
        "event_router",
        "event_update",
        "handler",
        "event_context",
        "event_from_user",
        "event_chat",
        "event_thread_id",
        "event_business_connection_id",
        "fsm_storage",
        "state",
        "raw_state",
        "scenes",
        "callback_answer",
    )
)

# The client arguments of one function, with the binding that fills each
Arguments: TypeAlias = tuple[tuple[str, Binding], ...]

# How aiogram reads a signature: on Python 3.14 an annotation that does not evaluate is left as it is
_SIGNATURE: Final = MappingProxyType(
    {"annotation_format": import_module("annotationlib").Format.FORWARDREF} if find_spec("annotationlib") else {},
)
