"""The runtime's side of the user's lifecycle hooks (D-115): what each hook
is told, when it runs, and what the loop does with what it says.

The hooks themselves -- matching, running, reading their output -- are in
hooks.py; this is AgentRuntime's part, kept apart from the loop.
"""

from __future__ import annotations

from typing import Any

from foundry.core.conversation import ToolUseBlock, Usage
from foundry.core.errors import InvalidToolCall
from foundry.core.events import Notice
from foundry.core.hooks import (
    INTERRUPT,
    PERMISSION_MODES,
    PERMISSION_REQUEST,
    POST_MODEL_REQUEST,
    POST_TOOL_USE,
    PRE_MODEL_REQUEST,
    PRE_TOOL_USE,
    READERS,
    SESSION_END,
    SESSION_END_REASON,
    SESSION_START,
    STOP,
    USER_PROMPT_SUBMIT,
    HookVerdict,
    hook_tool_input,
    hook_tool_name,
    matcher_names,
    rewritten_args,
    scrubbed,
)
from foundry.core.policy.engine import Decision
from foundry.core.session import EventType
from foundry.core.tools.base import Operation


class HookCalls:
    """AgentRuntime's hook calls: a mixin of methods, no state of its own."""

    __slots__ = ()

    def _compact_hooks(self, event: str, trigger: str) -> HookVerdict | None:
        """PreCompact or PostCompact hooks (D-118), told what started the
        compaction and not the permission mode, as codex's."""
        if self.hooks is None:
            return None
        return self._run_hooks(event, (trigger,), omit=("permission_mode",), trigger=trigger)

    def _model_hooks(self, event: str, *, status: str = "", error: str | None = None,
                     usage: Usage | None = None) -> None:
        """The PreModelRequest or PostModelRequest hooks (D-122): told, never
        asked, with codex's fields. After a try, they run to their end even
        when a cancel ended it."""
        if self.hooks is None:
            return
        fields: dict[str, Any] = {}
        if event == POST_MODEL_REQUEST:
            fields = {"status": status, "error": error, "token_usage": None if usage is None else {
                "input_tokens": usage.input_tokens,
                "cached_input_tokens": usage.cached_input_tokens,
                "output_tokens": usage.output_tokens,
                # Not counted apart from the output here.
                "reasoning_output_tokens": 0,
                "total_tokens": usage.total}}
        self._run_hooks(event, omit=("permission_mode",), stoppable=event == PRE_MODEL_REQUEST,
                        **fields)

    def _hook_payload(self, event: str, omit: tuple[str, ...] = (),
                      **fields: Any) -> dict[str, Any]:
        """What every hook is told, codex's names, then the event's own;
        less ``omit``, the common fields codex does not tell this event."""
        payload = {
            "session_id": self.session.session_id if self.session is not None else "",
            "transcript_path": str(self.session.path) if self.session is not None else None,
            "cwd": str(self.tool_ctx.workspace.root),
            "hook_event_name": event,
            "model": self.model,
            "turn_id": self._turn_id or str(self.context.turn),
            "permission_mode": PERMISSION_MODES.get(self.policy.mode.value,
                                                    self.policy.mode.value),
            **fields,
        }
        for name in omit:
            payload.pop(name)
        return payload

    def _run_hooks(self, event: str, names: tuple[str, ...] = (), call_id: str | None = None,
                   *, omit: tuple[str, ...] = (), stoppable: bool = True,
                   **fields: Any) -> HookVerdict | None:
        """Run the hooks of ``event`` whose matcher takes one of ``names``,
        and say what they said: to the user, the warnings and messages; to
        the journal, each run. What a hook said for the model comes back
        scrubbed, in the verdict's notes; at a call, ``call_id``, they are
        kept for its reply too. None when no hook matches. ``stoppable``
        False: a cancel already honoured does not end them as they start."""
        hooks = self.hooks.matching(event, names)
        if not hooks:
            return None
        for hook in hooks:
            if hook.status_message:
                self._emit(Notice(f"{event} hook: {hook.status_message}"))
        payload = self._hook_payload(event, omit, **fields)
        runs = self.hooks.run(hooks, scrubbed(payload, self.scrub),
                              cancelled=(lambda: self._cancelled) if stoppable else None)
        if not runs:
            # Each runs in the background: heard from later (D-120).
            return None
        verdict = READERS[event](runs)
        self._heard(event, verdict, call_id)
        return verdict

    def _heard(self, event: str, verdict: HookVerdict, call_id: str | None = None, *,
               background: bool = False) -> None:
        """Say what ``event``'s hooks said: to the user, the warnings and
        messages; to the journal, each run. Their notes are scrubbed in
        place and, at a call, kept for its reply."""
        clean = self.scrub or (lambda text: text)
        for message in verdict.messages:
            self._emit(Notice(f"{event} hook: {clean(message)}", level="warning"))
        for failure in verdict.failures:
            self._emit(Notice(f"{event} hook {clean(failure)}", level="warning"))
        verdict.context = [clean(note) for note in verdict.context]
        record: dict[str, Any] = {"event": event}
        if call_id is not None:
            self._hook_notes.setdefault(call_id, []).extend(
                (event, note) for note in verdict.context)
            record["call_id"] = call_id
        if background:
            record["async"] = True
        record["runs"] = scrubbed(verdict.records, self.scrub)
        self._journal(EventType.HOOK, record)

    def _background_notes(self) -> list[tuple[str, str]]:
        """What the background hooks that ended since said (D-120), heard
        now, at a safe point, as codex delivers it: their warnings to the
        user and their runs to the journal; their notes returned, by event."""
        if self.hooks is None:
            return []
        runs = self.hooks.finished()
        notes: list[tuple[str, str]] = []
        for event in dict.fromkeys(run.hook.event for run in runs):
            verdict = READERS[event]([run for run in runs if run.hook.event == event])
            self._heard(event, verdict, background=True)
            notes += [(event, note) for note in verdict.context]
        return notes

    def _hook_message(self, event: str, note: str) -> None:
        """A hook's note for the work in hand, as a message of its own."""
        message = f"From a {event} hook: {note}"
        self._journal(EventType.USER_INPUT, {"text": message, "hook": event})
        self.context.append_user(message, ())

    def _tool_hooks(self, event: str, call: ToolUseBlock, op: Operation,
                    **fields: Any) -> HookVerdict | None:
        """The hooks of a tool call's event, told of the call as codex
        tells them."""
        return self._run_hooks(event, matcher_names(op.tool), call.call_id,
                               tool_name=hook_tool_name(op.tool), tool_use_id=call.call_id,
                               tool_input=hook_tool_input(op.tool, op.args), **fields)

    def _said(self, what: str, reasons: list[str]) -> str:
        """``what`` a hook did, with the reasons it gave, scrubbed."""
        clean = self.scrub or (lambda text: text)
        given = [clean(reason) for reason in reasons if reason]
        return what + (": " + "; ".join(given) if given else "")

    def _start_hooks(self) -> tuple[list[str], list[str] | None]:
        """The SessionStart hooks owed (D-117, D-118), each source once, as
        codex's pending starts: their notes, and the reasons one stopped
        with, or None. A source is spent whatever its hooks said."""
        notes: list[str] = []
        while self.start_sources:
            source = self.start_sources.pop(0)
            verdict = self._run_hooks(SESSION_START, (source,), omit=("turn_id",),
                                      source=source)
            if verdict is None:
                continue
            if verdict.stops:
                return notes, verdict.stops
            notes += verdict.context
        return notes, None

    def _prompt_hooks(self, prompt: str) -> tuple[list[tuple[str, str]], str]:
        """The hooks a prompt meets before it is sent: the SessionStart
        hooks owed (D-117), then UserPromptSubmit's (D-116). Their notes, by
        event, and why one stopped it, or ""."""
        started, stops = self._start_hooks()
        if stops is not None:
            return [], self._said("a SessionStart hook stopped this prompt, so it was not sent",
                                  stops)
        notes = [(SESSION_START, note) for note in started]
        verdict = self._run_hooks(USER_PROMPT_SUBMIT, prompt=prompt)
        if verdict is None:
            return notes, ""
        if verdict.block or verdict.stops:
            return [], self._said("a UserPromptSubmit hook stopped this prompt, so it was not "
                                  "sent", verdict.block + verdict.stops)
        return notes + [(USER_PROMPT_SUBMIT, note) for note in verdict.context], ""

    def interrupt_hooks(self) -> None:
        """The Interrupt hooks (D-117): the task was stopped. Told, never
        asked; a cancel already honoured does not cut them short."""
        if self.hooks is not None:
            self._run_hooks(INTERRUPT, stoppable=False)

    def end_hooks(self) -> None:
        """The SessionEnd hooks (D-117), as the session closes, before its
        journal does. Told, never asked; a Ctrl+C meanwhile ends them, not
        the close."""
        if self.hooks is None:
            return
        try:
            self._run_hooks(SESSION_END, (SESSION_END_REASON,), stoppable=False,
                            omit=("model", "turn_id", "permission_mode"),
                            reason=SESSION_END_REASON)
        except KeyboardInterrupt:
            self._emit(Notice(f"{SESSION_END} hooks stopped", level="warning"))
        finally:
            # Background hooks still running have no one left to tell (D-120).
            self.hooks.close()

    def _stop_hooks(self, last_message: str, active: bool) -> str:
        """The Stop hooks (D-116), as the model is done: what the task goes
        on with, when one sends it on, or "". A ``continue: false`` ends it
        whatever another asked, as codex has it."""
        verdict = self._run_hooks(STOP, stop_hook_active=active,
                                  last_assistant_message=last_message or None)
        if verdict is None:
            return ""
        clean = self.scrub or (lambda text: text)
        if verdict.stops:
            for reason in verdict.stops:
                if reason:
                    self._emit(Notice(f"{STOP} hook: {clean(reason)}"))
            return ""
        if not verdict.block:
            return ""
        again = clean("\n\n".join(verdict.block))
        self._emit(Notice(f"a {STOP} hook sent the task on: {again}"))
        return again

    def _pre_tool_use(self, call: ToolUseBlock, op: Operation) -> tuple[Operation, str]:
        """The call the PreToolUse hooks leave, and why they refuse it, or
        "". A rewrite is validated as the model's call was and then goes
        through policy like any call: a hook can refuse or change a call,
        never let one skip a rule or a question."""
        verdict = self._tool_hooks(PRE_TOOL_USE, call, op)
        if verdict is None:
            return op, ""
        clean = self.scrub or (lambda text: text)
        if verdict.block:
            return op, clean("; ".join(verdict.block))
        rewrites = []
        for rewrite in verdict.rewrites:
            if rewrite not in rewrites:
                rewrites.append(rewrite)
        if len(rewrites) > 1:
            return op, "two PreToolUse hooks rewrote this call differently, so it was not run"
        if not rewrites:
            return op, ""
        args = rewritten_args(op.tool, op.args, rewrites[0])
        if args is None:
            self._emit(Notice(f"{PRE_TOOL_USE} hook returned an updatedInput that is not "
                              "this tool's input (an object; a string command for a command "
                              "or a patch); the call runs as the model made it",
                              level="warning"))
            return op, ""
        try:
            changed = self._canonicalize_paths(self.registry.get(op.tool).validate(args))
        except (InvalidToolCall, ValueError, TypeError) as exc:
            self._emit(Notice(f"{PRE_TOOL_USE} hook's rewrite is not a valid call "
                              f"({clean(str(exc))}); the call runs as the model made it",
                              level="warning"))
            return op, ""
        if changed == op:
            return op, ""
        self._hook_notes.setdefault(call.call_id, []).append(
            (PRE_TOOL_USE, f"this call was changed before it ran; it ran as: {changed.display}"))
        return changed, ""

    def _permission_request(self, call: ToolUseBlock, op: Operation,
                            decision: Decision) -> tuple[bool, str]:
        """What the PermissionRequest hooks (D-119) say of a call policy
        would ask the user about: whether one allowed it, and why one denied
        it, which wins, or "". Neither: the user is asked. They run only
        where someone would be asked; an unattended run asks no one."""
        if self.hooks is None or self.approval is None:
            return False, ""
        tool_input = hook_tool_input(op.tool, op.args)
        if not op.tool.startswith("mcp__"):
            # codex's place for policy's reason: none of Foundry's own tools
            # takes a description, so none can be spoofed through it.
            tool_input["description"] = decision.reason
        # And where no call can reach (D-152): an MCP tool takes any argument,
        # so `description` was the model's to write, read by a hook as policy's.
        verdict = self._run_hooks(PERMISSION_REQUEST, matcher_names(op.tool), call.call_id,
                                  tool_name=hook_tool_name(op.tool), tool_input=tool_input,
                                  permission_reason=decision.reason)
        if verdict is None:
            return False, ""
        if verdict.block:
            # Scrubbed where it goes: the reply, the journal, the event.
            return False, "; ".join(verdict.block)
        return verdict.allow, ""

    def _post_tool_use(self, call: ToolUseBlock, op: Operation, content: str,
                       images: tuple) -> tuple[str, tuple]:
        """The result the model is given once the PostToolUse hooks have
        seen it: their feedback in its place when one blocks it."""
        verdict = self._tool_hooks(POST_TOOL_USE, call, op, tool_response=content)
        if verdict is None or not verdict.block:
            return content, images
        clean = self.scrub or (lambda text: text)
        return (f"The call ran; a {POST_TOOL_USE} hook gave this in place of its result:\n"
                + clean("\n\n".join(verdict.block))), ()
