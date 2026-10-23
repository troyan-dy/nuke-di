"""
The record of a run: what was measured where, and the figures as JSON.
"""

import json
import platform
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Final

from suite.measure import Measurement

ROOT: Final = Path(__file__).resolve().parent.parent.parent
_SUMMARY: Final = (
    "nuke-di {nuke_di} · {implementation} {python} · {platform} · commit {commit} · N = {sizes} · {repeat} repeats"
)


def environment(sizes: list[int], repeat: int) -> dict[str, Any]:
    try:
        nuke_di = version("nuke-di")
    except PackageNotFoundError:
        nuke_di = None
    # git from PATH with fixed arguments, for the record of what was measured
    git = subprocess.run(
        ["git", "rev-parse", "HEAD"],  # noqa: S607
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    return {
        "nuke_di": nuke_di,
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "commit": git.stdout.strip() or None,
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "sizes": sizes,
        "repeat": repeat,
    }


def summary(env: dict[str, Any]) -> str:
    commit = (env["commit"] or "unknown")[:7]
    sizes = ", ".join(map(str, env["sizes"]))
    return _SUMMARY.format_map({**env, "commit": commit, "sizes": sizes})


def results_json(env: dict[str, Any], measurements: Sequence[Measurement]) -> str:
    report = {**env, "results": [_json_row(measurement) for measurement in measurements]}
    text = json.dumps(report, indent=2)
    return f"{text}\n"


def _json_row(measurement: Measurement) -> dict[str, Any]:
    failed = measurement.error is not None
    row: dict[str, Any] = {} if measurement.library is None else {"library": measurement.library}
    row.update(
        scenario=measurement.scenario,
        shape=measurement.shape,
        n=measurement.size,
        unit=measurement.unit,
        median=None if failed else measurement.median,
        p95=None if failed else measurement.p95,
        min=None if failed else min(measurement.samples),
        per_client=measurement.per_client_value,
        samples=measurement.samples,
    )
    if failed:
        row["error"] = measurement.error
    return row


def write_json(
    path: Path | None,
    env: dict[str, Any],
    measurements: Sequence[Measurement],
) -> None:
    """
    The figures as JSON into `path`, the `--json` of a run, if one is given.
    """
    if path is not None:
        path.write_text(results_json(env, measurements))
