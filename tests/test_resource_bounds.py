"""Memory bounds for output and file reads.

Both limits used to apply after the fact: the cap said what would be journaled,
not what would be allocated. A command printing in a loop, or a dataset sitting
in the repository, could exhaust memory before any timeout fired.
"""

from __future__ import annotations

import sys

import pytest

from foundry.core.errors import ToolError
from foundry.core.session import ArtifactStore
from foundry.core.tools.base import ReadTracker, ToolContext
from foundry.core.tools.command import MAX_CAPTURE_BYTES, RunCommand, run_process
from foundry.core.tools.files import MAX_READ_BYTES, ReadFile, SearchText
from foundry.core.workspace import Workspace


@pytest.fixture()
def ctx(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "small.py").write_text("x = 1\n", encoding="utf-8")
    return ToolContext(workspace=Workspace(repo),
                       artifacts=ArtifactStore(tmp_path / "artifacts"),
                       read_tracker=ReadTracker())


def test_output_capture_is_bounded_while_the_child_writes(tmp_path):
    """The child writes far more than the cap; only the cap is retained, and
    the discarded amount is reported rather than silently lost."""
    producer = tmp_path / "flood.py"
    producer.write_text(
        "import sys\nblock = 'x' * 1_000_000\nfor _ in range(40):\n"
        "    sys.stdout.write(block)\n",
        encoding="utf-8",
    )
    result = run_process(f"python {producer.name}", cwd=str(tmp_path), timeout_s=60)

    assert result.exit_code == 0
    assert len(result.stdout) <= MAX_CAPTURE_BYTES
    assert result.dropped_bytes > 30_000_000
    assert len(result.stdout) + result.dropped_bytes == 40_000_000


def test_the_model_is_told_output_was_discarded(ctx, tmp_path):
    producer = ctx.workspace.root / "flood.py"
    producer.write_text(
        "import sys\nblock = 'y' * 1_000_000\nfor _ in range(10):\n"
        "    sys.stdout.write(block)\n",
        encoding="utf-8",
    )
    tool = RunCommand()
    out = tool.execute(tool.validate({"command": "python flood.py"}), ctx)
    assert "discarded" in out.content


def test_read_file_refuses_an_oversized_file(ctx):
    big = ctx.workspace.root / "data.csv"
    with big.open("wb") as fh:
        fh.truncate(MAX_READ_BYTES + 1_000)

    tool = ReadFile()
    with pytest.raises(ToolError, match="too large"):
        tool.execute(tool.validate({"path": "data.csv", "line_count": 5}), ctx)


def test_read_file_error_names_an_alternative(ctx):
    big = ctx.workspace.root / "data.csv"
    with big.open("wb") as fh:
        fh.truncate(MAX_READ_BYTES + 1)
    tool = ReadFile()
    with pytest.raises(ToolError, match="search_text"):
        tool.execute(tool.validate({"path": "data.csv"}), ctx)


def test_read_file_still_reads_ordinary_files(ctx):
    tool = ReadFile()
    assert "x = 1" in tool.execute(tool.validate({"path": "small.py"}), ctx).content


def test_search_skips_oversized_files(ctx):
    """A packed log should not be decoded in full just to grep the repo."""
    big = ctx.workspace.root / "huge.txt"
    with big.open("w", encoding="utf-8") as fh:
        fh.write("needle\n")
        fh.write("filler\n" * 900_000)

    tool = SearchText()
    out = tool.execute(tool.validate({"query": "needle"}), ctx)
    assert "huge.txt" not in out.content
