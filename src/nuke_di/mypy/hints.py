"""
What a type hint is to the container: a client it fills, an optional client it refuses, or surely no client.
"""

from mypy.nodes import TypeInfo
from mypy.types import AnyType, Instance, NoneType, Type, TypeOfAny, UnboundType, UnionType, get_proper_type

from nuke_di.mypy.names import CLIENT, METADATA, type_statement


def client(hint: Type) -> TypeInfo | None:
    """
    The client class of a type hint that the container fills: a class, not an alias of one.
    """
    if type_statement(hint) is not None:
        return None
    proper = get_proper_type(hint)
    # A NewType of a client is a function at runtime, not a class
    if not isinstance(proper, Instance) or proper.type.is_newtype:
        return None
    if not is_client(proper.type):
        return None
    # `Repository[int]` is a generic alias at runtime; a bare `Repository` is the class, with `Any` parameters to mypy
    if any(not any_of(arg, TypeOfAny.from_omitted_generics) for arg in proper.args):
        return None
    return proper.type


def is_client(type_info: TypeInfo) -> bool:
    if type_info.has_base(CLIENT):
        return True
    marks = (base.metadata.get(METADATA, {}) for base in type_info.mro)
    return any(mark.get("client") for mark in marks)


def optional_client(hint: Type) -> TypeInfo | None:
    """
    The first client of a union with None, as the container names it.
    """
    hint = get_proper_type(hint)
    if not isinstance(hint, UnionType):
        return None
    members = [get_proper_type(member) for member in hint.items]
    if not any(isinstance(member, NoneType) for member in members):
        return None
    for member in members:
        found = client(member)
        if found is not None:
            return found
    return None


def any_of(hint: Type, kind: int) -> bool:
    """
    Whether the hint is an `Any` of the given kind: `TypeOfAny.unannotated` for an argument without a type hint,
    `from_omitted_generics` for the parameters of a bare generic class.
    """
    hint = get_proper_type(hint)
    return isinstance(hint, AnyType) and hint.type_of_any == kind


def surely_no_client(hint: Type) -> bool:
    """
    Whether mypy knows that the hint is no client: an `Any` of an error or of a module it does not follow, or a
    variable used as a type, may be one at runtime, alone or in a union.
    """
    hint = get_proper_type(hint)
    if isinstance(hint, UnionType):
        return all(surely_no_client(member) for member in hint.items)
    if isinstance(hint, AnyType):
        return hint.type_of_any == TypeOfAny.explicit
    return not isinstance(hint, UnboundType)
