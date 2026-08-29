"""Entry point.

UTF-8 mode is set on the process rather than asked of the user: telling people to
set PYTHONUTF8 globally changes behaviour for every other Python program on the
machine.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure:
                reconfigure(encoding="utf-8", errors="replace")

    from foundry.cli.app import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
