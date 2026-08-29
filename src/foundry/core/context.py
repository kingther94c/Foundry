"""Projecting the transcript into what the model actually sees.

The journal and the model's context are different things, and keeping them
separate is what makes replay deterministic and audit meaningful.

V1's context management is observation masking, not summarization: tool outputs
older than the last few turns collapse to a one-line stub. The Complexity Trap
paper (arXiv 2508.21433) measured masking as matching LLM summarization on both
solve rate and cost, and masking needs no extra model call -- which through a
corporate gateway is a dependency worth not having.
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
MASK_NOTICE = "[output elided to save context; re-run the tool if you need it again]"


@dataclass(slots=True)
class ContextManager:
    """Owns history, the token budget, and the projection into model context."""

    system_prompt: str = ""
    project_doc: str = ""
    environment: str = ""
    max_context_tokens: int = 128_000
    mask_after_turns: int = MASK_AFTER_TURNS
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
        messages: list[Message] = [self.system_message()]
        cutoff = self._turn - self.mask_after_turns

        for message, turn in zip(self.history, self._turn_of_message):
            if turn > cutoff or message.role is not Role.TOOL:
                messages.append(message)
                continue
            masked = tuple(
                ToolResultBlock(call_id=b.call_id, content=MASK_NOTICE, is_error=b.is_error)
                if isinstance(b, ToolResultBlock) and len(b.content) > len(MASK_NOTICE)
                else b
                for b in message.blocks
            )
            messages.append(Message(role=message.role, blocks=masked))

        return tuple(messages)

    # -- budget -----------------------------------------------------------

    def estimated_context_tokens(self) -> int:
        """Rough sizing for the overflow check only.

        Never used for reporting: recorded usage always comes from the provider,
        because a wrong number enforced as a budget is worse than no budget.
        """
        chars = 0
        for message in self.project():
            for block in message.blocks:
                if isinstance(block, TextBlock):
                    chars += len(block.text)
                elif isinstance(block, ToolResultBlock):
                    chars += len(block.content)
                elif isinstance(block, ToolUseBlock):
                    chars += len(block.arguments) + len(block.name)
        return chars // 4

    def context_exhausted(self) -> bool:
        return self.estimated_context_tokens() > (self.max_context_tokens - self.reserve_output_tokens)
