"""Run ResearchOS straight from this clone, with no install.

    python run-researchos.py evaluate --root cases --playbook playbook.json --ids a,b

Only the third-party dependency needs to be installed (anthropic). This folder
and the sibling tools package go first on sys.path, ahead of any installed copy,
so what runs is always this checkout's code.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

if __name__ == "__main__":
    sys.path[:0] = [str(HERE), str(HERE.parent / "tools")]
    from researchos.__main__ import main

    raise SystemExit(main())
