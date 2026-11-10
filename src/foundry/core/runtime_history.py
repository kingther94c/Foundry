"""What the runtime does to the conversation outside a turn: compaction
(D-040), a rewind to an earlier point (D-056), and a side question asked
and put away (D-129).
"""

from __future__ import annotations

from typing import Any

from foundry.core.commands import builtin_prompt
from foundry.core.conversation import Message, Role
from foundry.core.errors import Cancelled, FoundryError, explain
from foundry.core.events import Compacted, Notice, TokenCount
from foundry.core.hooks import POST_COMPACT, PRE_COMPACT
from foundry.core.session import EventType


class HistoryEditing:
    """AgentRuntime's history edits: a mixin of methods, no state of its own."""

    __slots__ = ()

    def compact(self, *, auto: bool, focus: str = "") -> str:
        """Replace the history with the user's messages and the model's summary
        of the work (D-040). Returns why it did not, or "" when it did.

        The summary request carries the same tools as any other, since a
        history holding tool calls is refused without them, and says not to
        use them; a call it makes anyway is ignored. Files read before are
        read again before a patch: their contents are no longer in context.
        """
        self._compact_stop = ""
        if not self.context.compactable():
            return "nothing to compact yet"
        trigger = "auto" if auto else "manual"
        verdict = self._compact_hooks(PRE_COMPACT, trigger)
        if verdict is not None and verdict.stops:
            self._compact_stop = self._said("a PreCompact hook stopped compaction",
                                            verdict.stops)
            return self._compact_stop
        before = self.context.estimated_context_tokens()
        self._emit(Notice("compacting the conversation to free context", level="info"))
        prompt = (self.compact_prompt or builtin_prompt("compact")).strip()
        if focus:
            prompt += f"\n\nThe user asks the summary to give particular attention to: {focus}"
        messages = (*self.context.project(), Message.text(Role.USER, prompt))
        try:
            turn = self._sample(messages, purpose="compact")
        except Cancelled:
            raise
        except FoundryError as exc:
            return f"compaction failed: {explain(exc)}"
        self.context.record_usage(turn.usage)
        self._emit(TokenCount(turn.usage, self.context.session_usage))
        self._journal(EventType.TOKEN_USAGE, {
            "input_tokens": turn.usage.input_tokens,
            "output_tokens": turn.usage.output_tokens,
            "cached_input_tokens": turn.usage.cached_input_tokens,
            "purpose": "compact",
        })
        self._goal_spent(turn.usage)
        summary = turn.text.strip()
        if not summary:
            return "compaction failed: the model wrote no summary"
        if self.plan:
            summary += "\n\nYour plan, as update_plan last set it:\n" + "\n".join(
                f"- [{step.status.value}] {step.step}" for step in self.plan)
        replaced = len(self.context.history)
        self.context.replace_history(self.context.compacted_history(summary))
        self.tool_ctx.read_tracker.clear()
        # Positions in the old history mean nothing in the new one.
        self._patched_at = []
        after = self.context.estimated_context_tokens()
        self._journal(EventType.COMPACTED, {
            "reason": "auto" if auto else "user",
            "turn": self.context.turn,
            "replaced": replaced,
            "tokens_before": before,
            "tokens_after": after,
            "messages": self._history_payload(),
        })
        self._emit(Compacted(tokens_before=before, tokens_after=after, auto=auto))
        if self.hooks is not None:
            # Owed before the next request, whatever PostCompact says.
            self.start_sources.append("compact")
        verdict = self._compact_hooks(POST_COMPACT, trigger)
        if verdict is not None and verdict.stops:
            self._compact_stop = self._said(
                "a PostCompact hook stopped the task after compaction", verdict.stops)
            if not auto:
                self._emit(Notice(self._compact_stop, level="warning"))
        return ""

    def rewind(self, length: int) -> list[str]:
        """Go back to the conversation's first ``length`` messages (/rewind,
        D-056) and return the files changed after that point.

        Nothing in the workspace is reverted. The model no longer sees what
        it read after the point, so every file is read again before it is
        patched, as after a compaction. The history kept is journaled whole,
        as a compaction's is, so a resume rebuilds it exactly.
        """
        removed = len(self.context.history) - length
        self.context.truncate(length)
        self.tool_ctx.read_tracker.clear()
        changed = list(dict.fromkeys(path for at, path in self._patched_at if at > length))
        # Those changes now come before the next message, which tells of them:
        # going back past that message again forgets it too.
        self._patched_at = [(min(at, length + 1), path) for at, path in self._patched_at]
        self._journal(EventType.REWOUND, {
            "keep": length,
            "removed": removed,
            "turn": self.context.turn,
            "turns": list(self.context._turn_of_message),
            "messages": self._history_payload(),
        })
        return changed

    def aside(self) -> tuple:
        """Where the conversation is before a side question (/side, D-129):
        what going back to it restores."""
        return (len(self.context.history), self.tool_ctx.read_tracker.snapshot(),
                self.plan, self.plan_explanation)

    def back_from_aside(self, point: tuple) -> None:
        """Back where the conversation was before the side question: the
        question and its answer dropped, journaled as a rewind is, and what
        the model had read, and its plan, as they were."""
        length, read, plan, explanation = point
        self.rewind(length)
        self.tool_ctx.read_tracker.restore(read)
        self.plan, self.plan_explanation = plan, explanation

    def _history_payload(self) -> list[dict[str, Any]]:
        return [
            {"role": m.role.value,
             "blocks": [self._block_payload(b, self._store_image) for b in m.blocks]}
            for m in self.context.history
        ]
