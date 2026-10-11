"""Projecting the transcript into what the model actually sees.

The journal and the model's context are different things, and keeping them
separate is what makes replay deterministic and audit meaningful.

V1's context management is observation masking, not summarization: old tool
outputs collapse to a one-line stub. The Complexity Trap paper (arXiv
2508.21433) measured masking as matching LLM summarization on both solve rate
and cost, and masking needs no extra model call -- which through a corporate
gateway is a dependency worth not having.

Masking is compaction, so it starts only under pressure (D-031). Masking every
output older than a few rounds, whatever the context held, made Foundry forget
files it had just read while a sixth of the window was in use: working on its
own repository it re-read the same handful of files round after round and ran
out of tool rounds before writing a test.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from foundry.core.conversation import (
    Message,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)
from foundry.core.redaction import Redactor, default_redactor

MASK_AFTER_TURNS = 5
# The share of the context window the projection may fill before old outputs
# are masked. Well short of the ceiling, so masking -- not termination -- is
# what a long session meets first.
MASK_ABOVE_FRACTION = 0.5
MASK_NOTICE = "[output elided to save context; re-run the tool if you need it again]"


def _estimate_tokens(messages) -> int:
    """Rough sizing: four characters to the token."""
    chars = 0
    for message in messages:
        for block in message.blocks:
            if isinstance(block, TextBlock):
                chars += len(block.text)
            elif isinstance(block, ToolResultBlock):
                chars += len(block.content)
            elif isinstance(block, ToolUseBlock):
                chars += len(block.arguments) + len(block.name)
    return chars // 4


def _masked(message: Message) -> Message:
    # A result already shorter than the notice is left alone: replacing it
    # would cost context, not save it.
    return Message(role=message.role, blocks=tuple(
        ToolResultBlock(call_id=b.call_id, content=MASK_NOTICE, is_error=b.is_error)
        if isinstance(b, ToolResultBlock) and len(b.content) > len(MASK_NOTICE)
        else b
        for b in message.blocks
    ))


@dataclass(slots=True)
class ContextManager:
    """Owns history, the token budget, and the projection into model context."""

    system_prompt: str = ""
    project_doc: str = ""
    environment: str = ""
    max_context_tokens: int = 128_000
    # The most recent rounds whose output is never masked, whatever the pressure.
    mask_after_turns: int = MASK_AFTER_TURNS
    mask_above_fraction: float = MASK_ABOVE_FRACTION
    reserve_output_tokens: int = 8_000

    redactor: Redactor = field(default_factory=default_redactor)
    history: list[Message] = field(default_factory=list)
    session_usage: Usage = field(default_factory=Usage)
    _turn_of_message: list[int] = field(default_factory=list)
    _turn: int = 0

    # -- history ----------------------------------------------------------

    def start_turn(self) -> int:
        self._turn += 1
        return self._turn

    def append(self, message: Message) -> None:
        self.history.append(message)
        self._turn_of_message.append(self._turn)

    def append_user(self, text: str) -> None:
        self.append(Message.text(Role.USER, text))

    def append_tool_result(self, call_id: str, content: str, *, is_error: bool = False) -> None:
        # The third redaction sink. A command that printed a credential must not
        # carry it into the next request just because the journal copy was
        # scrubbed -- the model's context is the one that leaves the machine.
        self.append(Message(role=Role.TOOL,
                            blocks=(ToolResultBlock(call_id=call_id,
                                                    content=self.redactor.scrub(content),
                                                    is_error=is_error),)))

    def record_usage(self, usage: Usage) -> None:
        self.session_usage = self.session_usage + usage

    # -- projection -------------------------------------------------------

    def system_message(self) -> Message:
        """Assembled in a fixed order: base prompt, then the policy-derived
        permissions paragraph (so the model is told the truth about what will be
        auto-allowed), then the project doc, then environment facts."""
        parts = [p for p in (self.system_prompt, self.project_doc, self.environment) if p]
        return Message.text(Role.SYSTEM, "\n\n".join(parts))

    def project(self) -> tuple[Message, ...]:
        """The full transcript while it fits under the masking threshold.

        Past it, tool outputs are masked oldest first, and only until the
        projection fits again; the last ``mask_after_turns`` rounds are never
        masked. Below the threshold masking saves nothing worth having and
        costs a round per re-read -- and it rewrote the prefix every round,
        which defeats a provider's prompt cache.
        """
        system = self.system_message()
        history = list(self.history)
        budget = int(self.max_context_tokens * self.mask_above_fraction)
        total = _estimate_tokens([system, *history])
        cutoff = self._turn - self.mask_after_turns

        for index, turn in enumerate(self._turn_of_message):
            if total <= budget or turn > cutoff:
                break
            message = history[index]
            if message.role is not Role.TOOL:
                continue
            masked = _masked(message)
            total -= _estimate_tokens([message]) - _estimate_tokens([masked])
            history[index] = masked

        return (system, *history)

    # -- budget -----------------------------------------------------------

    def estimated_context_tokens(self) -> int:
        """Rough sizing for the overflow check only.

        Never used for reporting: recorded usage always comes from the provider,
        because a wrong number enforced as a budget is worse than no budget.
        """
        return _estimate_tokens(self.project())

    def context_exhausted(self) -> bool:
        return self.estimated_context_tokens() > (self.max_context_tokens - self.reserve_output_tokens)
