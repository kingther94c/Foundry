#!/bin/bash
# SessionStart hook for Claude Code cloud sessions.
#
# Foundry targets Python 3.12 on Windows. The cloud image is Linux, defaults to
# Python 3.11 and has no pytest, so this builds .venv on 3.12, installs the
# package with its dev extras, and puts the venv first on PATH. That last step
# matters: the demo and golden-task tests spawn a bare `python`, which must be
# the 3.12 interpreter that has pytest.
#
# Local sessions are left alone; set up your own environment as AGENT.md says.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"
venv="$CLAUDE_PROJECT_DIR/.venv"

is_py312() {
  "$venv/bin/python" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12))' 2>/dev/null
}

if ! is_py312; then
  rm -rf "$venv"
  if command -v uv >/dev/null 2>&1; then
    # --seed keeps `python -m pip` working, which AGENT.md tells people to use.
    uv venv --quiet --seed --python 3.12 "$venv"
  else
    python3.12 -m venv "$venv"
  fi
fi

if command -v uv >/dev/null 2>&1; then
  uv pip install --quiet --python "$venv/bin/python" -e ".[dev]"
else
  "$venv/bin/python" -m pip install --quiet -e ".[dev]"
fi

if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo "export VIRTUAL_ENV=\"$venv\""
    echo "export PATH=\"$venv/bin:\$PATH\""
  } >> "$CLAUDE_ENV_FILE"
fi
