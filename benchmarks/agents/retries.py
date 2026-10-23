"""
The retries shapes() of benchmarks/agents/evaluate.py finds: connect() is fail-fast (ADR-0009), so a loop in it that
retries, a loop anywhere that catches and sleeps, and a retry library are rejected designs.
"""

import ast
import re
from typing import Final

_LOOPS: Final = (ast.For, ast.AsyncFor, ast.While)


def retry_shapes(node: ast.AST, test: bool) -> list[tuple[int, str]]:
    """
    The retries of `node`, as (line, what): in a connect(), and, outside a test, in a loop that catches and sleeps.
    """
    found: list[tuple[int, str]] = []
    if isinstance(node, ast.AsyncFunctionDef) and node.name == "connect":
        found.extend(retries(node))
    if not test and isinstance(node, _LOOPS):
        # A helper of connect(), a lifespan or a startup hook that retries: catches and sleeps in a loop
        loop = list(ast.walk(node))
        if _catches(loop) and _sleeps(loop):
            found.append((node.lineno, "a loop that catches and sleeps, a retry (ADR-0009)"))
    return found


def retries(connect: ast.AsyncFunctionDef) -> list[tuple[int, str]]:
    """
    A retry in a connect(), as (line, what): a `while` loop, a loop that sleeps or catches an exception, a counter
    of attempts. A plain `for` over data, e.g. to warm a cache, is not one.
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(connect):
        if isinstance(node, ast.While):
            found.append((node.lineno, "a while loop in connect() (ADR-0009)"))
        elif isinstance(node, ast.For | ast.AsyncFor):
            what = _loop_retry(node)
            if what is not None:
                found.append((node.lineno, what))
    return found


def _loop_retry(loop: ast.For | ast.AsyncFor) -> str | None:
    inner = list(ast.walk(loop))
    if _catches(inner):
        return "a loop that catches exceptions in connect() (ADR-0009)"
    if _sleeps(inner):
        return "a loop that sleeps in connect() (ADR-0009)"
    if _counts_attempts(loop):
        return "a loop over attempts in connect() (ADR-0009)"
    return None


def _catches(nodes: list[ast.AST]) -> bool:
    return any(isinstance(each, ast.Try) for each in nodes)


def _sleeps(nodes: list[ast.AST]) -> bool:
    calls = [each for each in nodes if isinstance(each, ast.Call)]
    return any("sleep" in ast.unparse(call.func) for call in calls)


def _counts_attempts(loop: ast.For | ast.AsyncFor) -> bool:
    target = ast.unparse(loop.target)
    return re.search(r"attempt|retr|tries|backoff", target, flags=re.IGNORECASE) is not None


def uses_retry_library(source: str) -> bool:
    return re.search(r"^\s*(import|from) (tenacity|backoff)\b", source, flags=re.MULTILINE) is not None
