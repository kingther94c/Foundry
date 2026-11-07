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

Masking cannot touch the last few rounds, so a session whose recent rounds
alone fill the window still ran out. Summary compaction (D-040) is the step
after it: the runtime asks the model for a handoff summary, and the history
becomes the user's own messages plus that summary. ``compacted_history``
builds the replacement; the model call is the runtime's.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace

from foundry.core.conversation import (
    ImageBlock,
    Message,
    OpaqueBlock,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)
from foundry.core.images import IMAGE_TOKEN_ESTIMATE
from foundry.core.redaction import Redactor, default_redactor
from foundry.core.tools.base import truncate_middle

MASK_AFTER_TURNS = 5
# The share of the context window the projection may fill before old outputs
# are masked. Well short of the ceiling, so masking -- not termination -- is
# what a long session meets first.
MASK_ABOVE_FRACTION = 0.5
MASK_NOTICE = "[output elided to save context; re-run the tool if you need it again]"
# Past this share of the window, after masking, the runtime compacts (D-040):
# masking has done what it can, and termination is still a round or two away.
COMPACT_ABOVE_FRACTION = 0.8
# What a compacted history may hold, as shares of the window: the user's own
# messages, newest first, and the model's summary. Together well under the
# compaction threshold, so one compaction cannot set off the next.
COMPACT_USER_FRACTION = 0.1
COMPACT_SUMMARY_FRACTION = 0.15
# Less than this left of the user's share is not worth a cut-down message.
_MIN_KEPT_CHARS = 200
SUMMARY_PREFIX = (
    "This conversation was compacted to free context. Earlier replies and tool "
    "output are gone; the user's messages from before it are kept above, and "
    "below is the summary you wrote of the work so far. Re-read a file before "
    "patching it, and run a command again rather than trusting its remembered "
    "output.\n\n"
)


def _estimate_tokens(messages) -> int:
    """Rough sizing: four characters to the token, and an image as what a
    provider charges for one -- not its base64, which would count a screenshot
    as a quarter of a million tokens."""
    chars = 0
    for message in messages:
        for block in message.blocks:
            if isinstance(block, TextBlock):
                chars += len(block.text)
            elif isinstance(block, ToolResultBlock):
                chars += len(block.content) + 4 * IMAGE_TOKEN_ESTIMATE * len(block.images)
            elif isinstance(block, ToolUseBlock):
                chars += len(block.arguments) + len(block.name)
            elif isinstance(block, ImageBlock):
                chars += 4 * IMAGE_TOKEN_ESTIMATE
    return chars // 4


def _masked(message: Message) -> Message:
    # A result already shorter than the notice is left alone: replacing it
    # would cost context, not save it. Its images go either way.
    return Message(role=message.role, blocks=tuple(
        ToolResultBlock(call_id=b.call_id, content=MASK_NOTICE, is_error=b.is_error)
        if isinstance(b, ToolResultBlock) and (len(b.content) > len(MASK_NOTICE) or b.images)
        else b
        for b in message.blocks
    ))


# Reasoning a provider binds to the conversation before it (D-048): Anthropic's
# thinking blocks, which ride verbatim in the assistant's opaque block. Once
# anything before one changes, replaying it is wasted -- the API drops it under
# drop_block (D-032), and reports the drop again on every later request.
BOUND_PROVIDER = "anthropic"
BOUND_TYPES = frozenset({"thinking", "redacted_thinking"})


def unbind(message: Message) -> Message:
    """``message`` without its bound reasoning; the same object when it has
    none. Text and tool calls stay verbatim; an opaque block left empty goes,
    and the adapter rebuilds the turn from its text and calls."""
    if message.role is not Role.ASSISTANT:
        return message
    blocks: list = []
    changed = False
    for block in message.blocks:
        if isinstance(block, OpaqueBlock) and block.provider == BOUND_PROVIDER:
            try:
                content = json.loads(block.data)
            except ValueError:
                content = None
            if isinstance(content, list):
                kept = [c for c in content
                        if not (isinstance(c, dict) and c.get("type") in BOUND_TYPES)]
                if len(kept) != len(content):
                    changed = True
                    if kept:
                        blocks.append(OpaqueBlock(provider=block.provider,
                                                  data=json.dumps(kept)))
                    continue
        blocks.append(block)
    return Message(role=message.role, blocks=tuple(blocks)) if changed else message


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
    compact_above_fraction: float = COMPACT_ABOVE_FRACTION
    # runtime.auto_compact_token_limit (D-094), codex's
    # model_auto_compact_token_limit: compact past this many tokens when that
    # comes before the share above. None: the share alone.
    compact_above_tokens: int | None = None

    redactor: Redactor = field(default_factory=default_redactor)
    history: list[Message] = field(default_factory=list)
    session_usage: Usage = field(default_factory=Usage)
    _turn_of_message: list[int] = field(default_factory=list)
    _turn: int = 0
    # The conversation's last request as sent, and the positions whose bound
    # reasoning is no longer sent because something before them changed (D-048).
    _sent: tuple[Message, ...] = ()
    _unbound: set[int] = field(default_factory=set)
    # unbind() parses the reply's JSON; a projection runs several times a
    # round, so each reply is stripped once, keyed by the message itself.
    _stripped: dict[int, tuple[Message, Message]] = field(default_factory=dict)

    # -- history ----------------------------------------------------------

    @property
    def turn(self) -> int:
        """The current round; every message appended now is stamped with it."""
        return self._turn

    def start_turn(self) -> int:
        self._turn += 1
        return self._turn

    def restore(self, history: list[Message], turns: list[int], turn: int,
                usage: Usage | None = None, sent: tuple[Message, ...] = ()) -> None:
        """Take over a resumed session's history, as its journal recorded it.

        Tool results go through the redactor again, as on the way in; the rest
        is the journal's own copy, which was scrubbed when it was written.
        ``sent`` is the last request as the journal recorded it: what the next
        one is compared with, and which reasoning had stopped being sent.
        """
        if len(history) != len(turns):
            raise ValueError("every restored message needs the round it belongs to")
        if any(a > b for a, b in zip(turns, turns[1:])) or (turns and turns[-1] > turn):
            raise ValueError("restored rounds must not go backwards")
        self.history = [
            Message(role=m.role, blocks=tuple(
                replace(b, content=self.redactor.scrub(b.content))
                if isinstance(b, ToolResultBlock) else b
                for b in m.blocks))
            for m in history
        ]
        self._turn_of_message = list(turns)
        self._turn = turn
        if usage is not None:
            self.session_usage = usage
        self._sent = tuple(sent)
        self._unbound = {
            index for index in range(1, min(len(self._sent), len(self.history) + 1))
            if unbind(self.history[index - 1]) is not self.history[index - 1]
            and unbind(self._sent[index]) is self._sent[index]
        }

    def truncate(self, length: int) -> None:
        """Forget every message from position ``length`` on (/rewind, D-056).
        What is kept is unchanged, so the reasoning in it stays bound to it."""
        if not 0 <= length <= len(self.history):
            raise ValueError("a rewind keeps part of the history, not more than it")
        del self.history[length:]
        del self._turn_of_message[length:]
        # A request's first message is the system message.
        self._sent = self._sent[:length + 1]
        self._unbound = {index for index in self._unbound if index <= length}

    def unanswered_calls(self) -> list[ToolUseBlock]:
        """The calls in the model's newest message that have no result yet:
        what a stopped task leaves (D-062). A request carrying one is refused
        by every provider."""
        for index in range(len(self.history) - 1, -1, -1):
            if self.history[index].role is Role.ASSISTANT:
                break
        else:
            return []
        answered = {block.call_id for message in self.history[index + 1:]
                    for block in message.blocks if isinstance(block, ToolResultBlock)}
        return [block for block in self.history[index].blocks
                if isinstance(block, ToolUseBlock) and block.call_id not in answered]

    def append(self, message: Message) -> None:
        self.history.append(message)
        self._turn_of_message.append(self._turn)

    def append_user(self, text: str, images: tuple[ImageBlock, ...] = ()) -> None:
        # Images first: a model reads the question after what it is about.
        self.append(Message(role=Role.USER, blocks=(*images, TextBlock(text))))

    def append_tool_result(self, call_id: str, content: str, *, is_error: bool = False,
                           images: tuple[ImageBlock, ...] = ()) -> None:
        # The third redaction sink. A command that printed a credential must not
        # carry it into the next request just because the journal copy was
        # scrubbed -- the model's context is the one that leaves the machine.
        # (An image cannot be scrubbed; what it shows is untrusted content.)
        self.append(Message(role=Role.TOOL,
                            blocks=(ToolResultBlock(call_id=call_id,
                                                    content=self.redactor.scrub(content),
                                                    is_error=is_error, images=images),)))

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
        """What the next request sends: the masked transcript, without the
        reasoning a change before it has invalidated (D-048)."""
        return self._bind(self._masked_projection())[0]

    def mark_sent(self) -> None:
        """The projection was sent as the conversation's next request: later
        requests are compared with it."""
        self._sent, self._unbound = self._bind(self._masked_projection())

    def _bind(self, projected: tuple[Message, ...]) -> tuple[tuple[Message, ...], set[int]]:
        """Strip the bound reasoning that can no longer be valid.

        Each thinking block is bound to everything sent before it. Where this
        projection first differs from the last request sent -- a rebuilt
        permissions paragraph, a masked result, a resume's new system prompt --
        every block from there on was made against the old prefix, so it is
        left out, now and on every later request. Blocks before the change
        keep their prefix and stay; blocks made after it are bound to the new
        one. Appending, which is all a turn normally does, changes nothing.
        """
        unbound = set(self._unbound)
        out = [self._unbind(m) if index in unbound else m for index, m in enumerate(projected)]
        for index in range(min(len(out), len(self._sent))):
            if out[index] != self._sent[index]:
                for later in range(index, len(out)):
                    stripped = self._unbind(out[later])
                    if stripped is not out[later]:
                        unbound.add(later)
                        out[later] = stripped
                break
        return tuple(out), unbound

    def _unbind(self, message: Message) -> Message:
        cached = self._stripped.get(id(message))
        if cached is not None and cached[0] is message:
            return cached[1]
        stripped = unbind(message)
        if stripped is not message:
            self._stripped[id(message)] = (message, stripped)
        return stripped

    def _masked_projection(self) -> tuple[Message, ...]:
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

    # -- compaction (D-040) -------------------------------------------------

    def needs_compaction(self) -> bool:
        """Whether masking has left too much: the projection is past the
        compaction threshold and there is model output to summarise."""
        return (self.compactable()
                and self.estimated_context_tokens() > self.compaction_threshold())

    def compaction_threshold(self) -> int:
        """Where automatic compaction starts: a share of the window (D-040),
        or the user's limit when that comes first (D-094) -- never later, so
        a limit past the window cannot leave a session to run out of it.

        Never past the point the window counts as full either (D-095): in a
        small window the room kept for the answer is more than the share
        leaves, and a 32,000-token model ran out at 24,000 tokens before it
        would have compacted at 25,600."""
        threshold = min(int(self.max_context_tokens * self.compact_above_fraction),
                        self.max_context_tokens - self.reserve_output_tokens)
        if self.compact_above_tokens is not None:
            threshold = min(threshold, self.compact_above_tokens)
        return threshold

    def compactable(self) -> bool:
        """Something besides the user's messages to compact. Right after a
        compaction there is not, so a summary is never summarised again
        before the model has done anything since."""
        return any(m.role is not Role.USER for m in self.history)

    def compacted_history(self, summary: str) -> list[Message]:
        """The history a compaction leaves: the user's messages, the newest
        that fit in their share of the window, oldest first; then the summary.

        The first message that does not fit is cut to what is left and is the
        oldest kept: the newest are the ones the work in hand answers. A
        summary from an earlier compaction is not kept as a user message: the
        new summary was written from it.
        """
        budget = int(self.max_context_tokens * COMPACT_USER_FRACTION) * 4
        kept: list[Message] = []
        for message in reversed(self.history):
            if message.role is not Role.USER:
                continue
            text = message.text_content
            if not text.strip() or text.startswith(SUMMARY_PREFIX):
                continue
            if len(text) > budget:
                if budget >= _MIN_KEPT_CHARS or not kept:
                    kept.append(Message.text(Role.USER, truncate_middle(text, budget)[0]))
                break
            budget -= len(text)
            kept.append(Message.text(Role.USER, text))
        kept.reverse()
        summary, _ = truncate_middle(
            self.redactor.scrub(summary.strip()),
            int(self.max_context_tokens * COMPACT_SUMMARY_FRACTION) * 4)
        return [*kept, Message.text(Role.USER, SUMMARY_PREFIX + summary)]

    def replace_history(self, messages: list[Message]) -> None:
        """Make ``messages`` the whole history, all of them from this round,
        so nothing in it is masked."""
        self.history = list(messages)
        self._turn_of_message = [self._turn] * len(self.history)
        # A new prefix: nothing from before it is sent again.
        self._sent, self._unbound = (), set()
        self._stripped.clear()
