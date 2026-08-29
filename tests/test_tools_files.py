from __future__ import annotations

import pytest

from foundry.core.errors import InvalidToolCall, ToolError
from foundry.core.session import ArtifactStore
from foundry.core.tools.base import ReadTracker, ToolContext
from foundry.core.tools.files import ListFiles, ReadArtifact, ReadFile, SearchText
from foundry.core.workspace import Workspace


@pytest.fixture()
def ctx(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (repo / "src" / "util.py").write_text("VALUE = 2\n", encoding="utf-8")
    (repo / "README.md").write_text("# demo\n", encoding="utf-8")
    return ToolContext(
        workspace=Workspace(repo),
        artifacts=ArtifactStore(tmp_path / "artifacts"),
        read_tracker=ReadTracker(),
    )


def test_list_files_filters_and_sorts(ctx):
    tool = ListFiles()
    out = tool.execute(tool.validate({"pattern": "*.py"}), ctx)
    assert "src/app.py" in out.content
    assert "README.md" not in out.content


def test_list_files_rejects_unknown_argument(ctx):
    with pytest.raises(InvalidToolCall, match="unknown argument"):
        ListFiles().validate({"pattern": "*.py", "recursive": True})


def test_search_text_reports_matches(ctx):
    tool = SearchText()
    out = tool.execute(tool.validate({"query": r"def \w+"}), ctx)
    assert "src/app.py:1" in out.content


def test_search_text_rejects_bad_regex(ctx):
    with pytest.raises(InvalidToolCall, match="invalid regular expression"):
        SearchText().validate({"query": "([unclosed"})


def test_read_file_numbers_lines_and_records_digest(ctx):
    tool = ReadFile()
    out = tool.execute(tool.validate({"path": "src/app.py"}), ctx)
    assert "def run():" in out.content
    assert "     1\t" in out.content
    assert ctx.read_tracker.has_read("src/app.py")


def test_read_file_rejects_escape(ctx):
    tool = ReadFile()
    with pytest.raises(ToolError):
        tool.execute(tool.validate({"path": "../outside.txt"}), ctx)


def test_read_file_rejects_binary(ctx):
    (ctx.workspace.root / "blob.bin").write_bytes(b"\x00\x01\x02binary")
    tool = ReadFile()
    with pytest.raises(ToolError, match="binary"):
        tool.execute(tool.validate({"path": "blob.bin"}), ctx)


def test_read_file_windows_long_files(ctx):
    big = "\n".join(f"line {i}" for i in range(1, 600))
    (ctx.workspace.root / "big.txt").write_text(big, encoding="utf-8")
    tool = ReadFile()
    out = tool.execute(tool.validate({"path": "big.txt", "line_count": 10}), ctx)
    assert "more below" in out.content


def test_read_artifact_requires_opaque_token(ctx):
    tool = ReadArtifact()
    for bad in ["../../auth.json", "not-hex", "sessions/x.jsonl"]:
        with pytest.raises(InvalidToolCall):
            tool.validate({"artifact_id": bad})


def test_read_artifact_returns_stored_text(ctx):
    ref = ctx.artifacts.put_text("full output here")
    tool = ReadArtifact()
    out = tool.execute(tool.validate({"artifact_id": ref.artifact_id}), ctx)
    assert out.content == "full output here"


def test_read_artifact_unknown_id_is_tool_error(ctx):
    tool = ReadArtifact()
    with pytest.raises(ToolError, match="unknown artifact"):
        tool.execute(tool.validate({"artifact_id": "0" * 64}), ctx)
