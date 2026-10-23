"""
The module Python runs as the program: the name an entrypoint run in it goes by, and the command its `--help`
shows. Imported only by nuke_di.entrypoint.
"""

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

# The name of the module Python runs as the program
MAIN = "__main__"


def entrypoint_name(func: Callable[..., Any]) -> str:
    module = func.__module__
    if module == MAIN:
        main = sys.modules[MAIN]
        # `python -m app.jobs.sync` keeps the importable name in the spec, `python app/jobs/sync.py` has none
        spec = main.__spec__
        if spec is None:
            module = Path(main.__file__ or "").stem
        else:
            module = spec.name
    return f"{module}.{func.__qualname__}"


def entrypoint_prog() -> str | None:
    """
    The command shown in `--help`: `python -m <module>`, or the argparse default for a file run by path.
    """
    spec = sys.modules[MAIN].__spec__
    if spec is None:
        return None
    module = spec.name.removesuffix(".__main__")
    return f"python -m {module}"
