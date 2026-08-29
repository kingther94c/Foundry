"""A backend wrapper that captures a session as a replay fixture.

Golden fixtures have to come from somewhere, and re-recording has to be cheap or
the suite ossifies around whatever the first capture happened to produce. This
wraps any real backend, passes every turn through, and writes the pairs out.

Recording from a *local* endpoint is the default path: it costs nothing, and a
fixture captured from a production session would carry the user's own code into
the test suite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from foundry.core.backends.base import ModelBackend, StreamEvent, TurnFinished
from foundry.core.backends.replay import write_fixture
from foundry.core.conversation import Capabilities, ModelTurn, TurnRequest


@dataclass(slots=True)
class RecordingBackend:
    inner: ModelBackend
    fixture_path: Path
    name: str = "recording"
    captured: list[tuple[TurnRequest, ModelTurn]] = field(default_factory=list)

    def capabilities(self) -> Capabilities:
        return self.inner.capabilities()

    def stream_turn(self, request: TurnRequest) -> Iterator[StreamEvent]:
        for event in self.inner.stream_turn(request):
            if isinstance(event, TurnFinished):
                self.captured.append((request, event.turn))
            yield event

    def save(self) -> Path:
        write_fixture(self.fixture_path, self.captured, self.capabilities())
        return self.fixture_path
