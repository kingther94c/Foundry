"""What a compaction says of its size (D-184). With a real model, `/compact`
of a short conversation printed "about 1,767 tokens down to 1,782": a summary
can be as long as what it replaces, and "down to" a larger number misreads."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from foundry.cli.render import Renderer
from foundry.core.events import Compacted


def _said(before, after, auto=False):
    out = io.StringIO()
    Renderer(console=Console(file=out, width=200)).handle(
        Compacted(tokens_before=before, tokens_after=after, auto=auto))
    return out.getvalue().strip()


def test_smaller_is_down_to():
    assert _said(90_000, 12_345, auto=True) == (
        "context compacted automatically: about 90,000 tokens down to 12,345")


@pytest.mark.parametrize("after", [1_782, 1_767])
def test_no_smaller_is_not_down_to(after):
    assert _said(1_767, after) == (
        f"context compacted on request: about 1,767 tokens, {after:,} after: no smaller, "
        "as there was little to compact")
