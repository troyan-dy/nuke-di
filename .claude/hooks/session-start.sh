#!/bin/bash
# Installs the dev environment of a Claude Code cloud session, so `make lint` and `make test` work from the start
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"

if ! command -v uv >/dev/null 2>&1; then
  python3 -m pip install --quiet --user uv
  export PATH="$HOME/.local/bin:$PATH"
  echo "export PATH=\"$HOME/.local/bin:\$PATH\"" >> "${CLAUDE_ENV_FILE:-/dev/null}"
fi

# The package, the dev and compare groups from uv.lock, as `make install`; idempotent
uv sync --locked

# pyright runs from uvx (Makefile); fetch the pinned one now rather than in the first `make lint`
uvx "$(sed -n 's/^PYRIGHT := //p' Makefile)" --version >/dev/null
