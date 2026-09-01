"""The notebook has to actually run.

There is no jupyter in this environment, so this executes the code cells in
order in one namespace -- which is exactly what "Run All" does. A teaching
notebook that raises on cell 7 teaches the wrong thing twice.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

NOTEBOOK = Path(__file__).parent.parent / "demo" / "mini_foundry.ipynb"


def _cells() -> list[dict]:
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))["cells"]


def _code_cells() -> list[str]:
    return ["".join(c["source"]) for c in _cells() if c["cell_type"] == "code"]


def test_the_notebook_is_valid_and_has_both_kinds_of_cell():
    payload = json.loads(NOTEBOOK.read_text(encoding="utf-8"))

    assert payload["nbformat"] == 4
    assert payload["metadata"]["kernelspec"]["name"] == "python3"
    kinds = {c["cell_type"] for c in payload["cells"]}
    assert kinds == {"code", "markdown"}
    for cell in payload["cells"]:
        assert isinstance(cell["source"], list), "source must be a line list"
        if cell["cell_type"] == "code":
            assert cell["outputs"] == [], "committed outputs make the diff noise"
            assert cell["execution_count"] is None


def test_every_code_cell_compiles():
    for index, source in enumerate(_code_cells()):
        compile(source, f"<cell {index}>", "exec")


def test_run_all_succeeds(tmp_path, monkeypatch, capsys):
    """Executes every code cell in order in one namespace, the way Run All does.

    Runs from the demo directory so the notebook's own path discovery is the
    thing under test.
    """
    monkeypatch.chdir(NOTEBOOK.parent)
    namespace: dict = {"__name__": "__notebook__"}

    for index, source in enumerate(_code_cells()):
        try:
            exec(compile(source, f"<cell {index}>", "exec"), namespace)
        except Exception as exc:                              # noqa: BLE001
            captured = capsys.readouterr()
            pytest.fail(f"cell {index} raised {type(exc).__name__}: {exc}\n"
                        f"--- source ---\n{source}\n--- stdout ---\n{captured.out[-2000:]}")

    # It has to demonstrate what it claims, not print a plausible transcript.
    out = capsys.readouterr().out
    for expected, why in [
        ("证据核对通过", "the loop never reached a verified completion"),
        ("拒绝（第 0 步）", "the breaker section showed no step-0 denial"),
        ("实际 exit code 是 1", "the liar section did not catch the false claim"),
        ("plan 模式下不做任何改动", "plan mode allowed a mutation"),
        ("溜过去了", "the evasion cell no longer shows the gap it teaches"),
    ]:
        assert expected in out, why


def test_the_api_toggle_defaults_to_offline():
    """Cloning the repo and hitting Run All must not need a network or a key."""
    toggle = [s for s in _code_cells() if "USE_API" in s][0]
    assert "USE_API  = False" in toggle


def test_the_notebook_imports_rather_than_copies_the_implementation():
    """One source of truth: if the notebook pasted the classes, the two would
    drift and the notebook would start teaching a version that does not exist."""
    joined = "\n".join(_code_cells())

    assert "import mini_foundry as mf" in joined
    for defined_in_the_module in ("class Policy", "class Tools", "def run("):
        assert defined_in_the_module not in joined, \
            f"{defined_in_the_module!r} is copied into the notebook"


def test_the_evasion_cell_still_demonstrates_the_gap():
    """The point of that cell is that substring matching loses. If the demo's
    breaker ever became clever enough to catch them all, the lesson would be
    silently false."""
    import sys

    sys.path.insert(0, str(NOTEBOOK.parent))
    import mini_foundry as mf

    policy = mf.Policy()
    tools = mf.Tools(NOTEBOOK.parent)
    slipped = []
    for command in ["git reset ,--hard HEAD", "git    reset   --hard",
                    "git.exe reset --hard", "&'git' reset --hard"]:
        op = tools.validate(mf.ToolCall("c", "run_command",
                                        json.dumps({"command": command})))
        if policy.evaluate(op).verdict != mf.DENY:
            slipped.append(command)

    assert slipped, "the cell claims some forms slip past; none did"
