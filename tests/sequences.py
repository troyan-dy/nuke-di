"""
Helpers for the sequences tests check: the one element of a sequence, and a sequence repeated.
"""

from collections.abc import Iterable, Sequence
from typing import TypeVar

ElementT = TypeVar("ElementT")


def single(candidates: Iterable[ElementT]) -> ElementT:
    """
    The one element of `candidates`; fails the test when there is not exactly one.
    """
    found = list(candidates)
    assert len(found) == 1, found
    return found[0]


def repeated(elements: Sequence[ElementT], times: int) -> list[ElementT]:
    """
    `elements` `times` times over, in order.
    """
    return [element for _ in range(times) for element in elements]
