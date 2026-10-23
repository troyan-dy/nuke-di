"""
Helpers of the tests over sequences: the one element a test expects, and a sequence repeated.
"""

from collections.abc import Iterable, Sequence
from typing import TypeVar

ElementT = TypeVar("ElementT")


def single(candidates: Iterable[ElementT]) -> ElementT:
    """
    The only element of `candidates`; the assertion shows them all when there is not exactly one.
    """
    found = list(candidates)
    assert len(found) == 1, found
    return found[0]


def repeated(elements: Sequence[ElementT], times: int) -> list[ElementT]:
    """
    `elements` one after another `times` times, as `elements * times` makes of a list.
    """
    return [element for _ in range(times) for element in elements]
