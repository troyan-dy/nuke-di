"""
The scenarios of benchmarks/run.py by name.
"""

from collections.abc import Mapping
from functools import partial
from types import MappingProxyType

from suite.cli import Scenario
from suite.connect import Reference, connect_scenario
from suite.footprint import import_scenario, memory_scenario
from suite.replacements import inject_scenario, overrides_scenario
from suite.resolve import not_singleton_scenario, resolve_scenario
from suite.web import RequestSamples, fastapi_scenario


def scenarios(ideal: Reference, critical_path: Reference, request_samples: RequestSamples) -> Mapping[str, Scenario]:
    """
    Every scenario by its name, in the order of a full run. What awaits one coroutine after another, which is what
    it times, is benchmarks/run.py's own: the references of `connect` and the requests of `fastapi`.
    """
    return MappingProxyType(
        {
            "resolve": resolve_scenario,
            "connect": partial(connect_scenario, ideal=ideal, critical_path=critical_path),
            "inject": inject_scenario,
            "not_singleton": not_singleton_scenario,
            "overrides": overrides_scenario,
            "fastapi": partial(fastapi_scenario, request_samples=request_samples),
            "import": import_scenario,
            "memory": memory_scenario,
        }
    )
