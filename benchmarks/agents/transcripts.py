"""
What benchmarks/agents/evaluate.py reads in the stream-json transcript of a Claude Code session: its final event,
whether the agent loaded the nuke-di skill, and what the session took.
"""

import contextlib
import json
from collections.abc import Mapping
from typing import Any, Final

from tasks import Attempt

_NUMBER: Final = (int, float)


def summary(transcript: str) -> tuple[dict[str, Any], bool]:
    """
    The final event of a stream-json transcript, and whether the agent loaded the nuke-di skill.
    """
    events = _events(transcript)
    endings = [event for event in events if event.get("type") == "result"]
    final = endings[-1] if endings else {}
    return final, _loaded_skill(events)


def _events(transcript: str) -> list[dict[str, Any]]:
    events = []
    for line in transcript.splitlines():
        if line.startswith("{"):
            with contextlib.suppress(json.JSONDecodeError):
                events.append(json.loads(line))
    return events


def _loaded_skill(events: list[dict[str, Any]]) -> bool:
    for event in events:
        if event.get("type") == "assistant":
            message = event.get("message", {})
            if any(_loads_the_skill(block) for block in message.get("content", [])):
                return True
    return False


def _loads_the_skill(block: object) -> bool:
    """
    Whether a block of an answer of the agent is a call of its Skill tool for the nuke-di skill.
    """
    if not isinstance(block, dict) or block.get("type") != "tool_use":
        return False
    if block.get("name") != "Skill":
        return False
    return "nuke-di" in str(block.get("input"))


def record(attempt: Attempt, final: Mapping[str, object]) -> None:
    """
    The duration, the cost and the turns of the session into a new `attempt`, from the final event of its
    transcript; what the event lacks keeps the default of Attempt.
    """
    duration = final.get("duration_ms")
    cost = final.get("total_cost_usd")
    turns = final.get("num_turns")
    if isinstance(duration, _NUMBER):
        attempt.seconds = round(duration / 1000, 1)
    if isinstance(cost, _NUMBER):
        attempt.cost_usd = round(float(cost), 4)
    if isinstance(turns, int):
        attempt.turns = turns
