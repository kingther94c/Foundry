"""Run Foundry straight from this clone, with no install.

    python run-foundry.py doctor
    foundry.cmd doctor          (the same, from any directory; see foundry.cmd)

Only the dependencies need to be installed (requirements.in: rich). The source
tree goes first on sys.path, ahead of any installed or editable copy, so what runs
is always this checkout's code; `foundry doctor` prints where it came from.

Deliberately not named foundry.py: a module of that name at the repository root
would shadow the installed wheel whenever `python -m foundry` runs from here, and
the offline-install check in CI would then test the source tree, not the wheel.
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"

if __name__ == "__main__":
    if sys.version_info[:2] != (3, 12):
        print(f"warning: Foundry targets Python 3.12; this is "
              f"{sys.version_info[0]}.{sys.version_info[1]}", file=sys.stderr)
    sys.path.insert(0, str(SRC))
    # foundry.__main__.main, not foundry.cli.app.main: it switches the console
    # streams to UTF-8 on Windows first.
    from foundry.__main__ import main

    raise SystemExit(main())
