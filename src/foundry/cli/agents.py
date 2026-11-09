"""Sub-agents, as codex's (D-141): the runtime ``spawn_agent`` runs one in.

A sub-agent is the session's own model on a conversation of its own: the
system prompt, instructions and skills the session has, and nothing of the
session's conversation -- its task says what it needs. It answers to the
session's rules, approvals and hooks: the same policy engine, read-only for
it unless the call says otherwise; the same approval callback, its requests
named as its own; the same hooks. Its journal is the session's, under
``agents/``, so it is archived and deleted with the session and never listed
as one. What it does is shown as it happens, each line named as its own; only
its report reaches the session's conversation, and its tokens are counted in
the session's.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from foundry import __version__
from foundry.core.context import ContextManager
from foundry.core.events import (
    ErrorEvent, EventSink, Notice, TerminalStatus, ToolBegin, ToolEnd, ToolOutputDelta,
    ToolRejected,
)
from foundry.core.redaction import default_redactor
from foundry.core.runtime import AgentRuntime, Budget
from foundry.core.session import SessionStore
from foundry.core.tools.base import ReadTracker, ToolContext, ToolOutput
from foundry.core.tools.registry import default_registry

if TYPE_CHECKING:
    from foundry.cli.app import Wiring

SUBAGENT_READ_ONLY = ("a read-only sub-agent changes nothing: edits and commands are refused; "
                      "its report says what should change")
BRIEF = ("You are a sub-agent, started by another agent for one task, in a conversation of "
         "your own. When the task is done, call finish with your report: it is all the "
         "other agent sees of your work, so put in it everything the task asks for.")
READ_ONLY_BRIEF = (" You are read-only: read and search as you need, but change nothing and "
                   "run no command.")
# Not a sub-agent's: it starts none of its own, and the session's goal is the
# session's to pursue.
NOT_FOR_SUBAGENTS = frozenset({"spawn_agent", "get_goal", "update_goal"})
# What a sub-agent shows of its work: its calls and their ends, what went
# wrong -- not its text, which is its report, nor its turns and tokens.
_SHOWN = (ToolEnd, ToolOutputDelta, Notice, ErrorEvent)


@dataclasses.dataclass
class Spawner:
    """Starts the session's sub-agents; set to the session's wiring once
    there is one."""

    wiring: "Wiring | None" = None
    count: int = 0

    def spawn(self, task: str, *, read_only: bool, ctx: ToolContext) -> ToolOutput:
        wiring = self.wiring
        assert wiring is not None, "a spawner is given its session before a turn runs"
        parent = wiring.runtime
        self.count += 1
        number = self.count
        try:
            store = self._journal(wiring, number, task, read_only)
        except OSError as exc:
            return ToolOutput(content=f"could not start the sub-agent: {exc}", is_error=True)
        child = self._runtime(wiring, store, number)
        policy = parent.policy
        before = policy.read_only
        if read_only and not before:
            policy.read_only = SUBAGENT_READ_ONLY
        parent.child = child
        outcome = None
        try:
            outcome = child.run_turn(BRIEF + (READ_ONLY_BRIEF if read_only else "")
                                     + "\n\nThe task:\n" + task)
        finally:
            policy.read_only = before
            parent.child = None
            # The backend is the session's: its retries are told as the
            # session's again (D-085).
            if hasattr(parent.backend, "on_retry"):
                parent.backend.on_retry = parent._retrying
            parent.spent_by_agent(child.context.session_usage, number)
            if not store._terminated:
                store.record_termination(TerminalStatus.CANCELLED,
                                         "the session's task was stopped")
            store.close()
        if not read_only:
            # It may have changed any file, by a patch or a command: each is
            # read again before the session patches it.
            ctx.read_tracker.clear()
        status = outcome.status.value if outcome.status is not None else "answered"
        report = (outcome.summary or outcome.text or "").strip() or "(no report)"
        kind = "read-only" if read_only else "may change files"
        used = child.context.session_usage
        return ToolOutput(
            content=f"sub-agent {number} ({kind}) ended: {status}\n\n{report}",
            metadata={"agent": {"number": number, "session": store.session_id,
                                "status": status, "tokens": used.total}})

    def _journal(self, wiring: "Wiring", number: int, task: str,
                 read_only: bool) -> SessionStore:
        parent = wiring.runtime
        store = SessionStore(wiring.session.dir / "agents", redactor=default_redactor())
        store.write_header(workspace=str(wiring.workspace.root),
                           profile=wiring.config.backend.name, model=parent.model,
                           foundry_version=__version__)
        if parent.git_baseline is not None:
            store.append("git_baseline", parent.git_baseline.as_payload())
        store.append("subagent", {"parent": wiring.session.session_id, "number": number,
                                  "read_only": read_only, "task": task})
        return store

    def _runtime(self, wiring: "Wiring", store: SessionStore, number: int) -> AgentRuntime:
        parent = wiring.runtime
        # The parent's own built-in tools: view_image only where it has it
        # (D-045, D-144).
        registry = default_registry(recorder=store,
                                    images="view_image" in parent.registry.names())
        for name in parent.registry.names():
            if name not in NOT_FOR_SUBAGENTS and name not in registry.names():
                registry.register(parent.registry.get(name))   # skills', MCP servers'
        context = ContextManager(
            system_prompt=parent.context.system_prompt,
            project_doc=parent.context.project_doc,
            developer_instructions=parent.context.developer_instructions,
            environment=parent.context.environment,
            skills=parent.context.skills,
            max_context_tokens=parent.context.max_context_tokens,
            reserve_output_tokens=parent.context.reserve_output_tokens,
            compact_above_tokens=parent.context.compact_above_tokens,
        )
        pctx = parent.tool_ctx
        tool_ctx = ToolContext(workspace=pctx.workspace, artifacts=store.artifacts,
                               read_tracker=ReadTracker(),
                               max_output_bytes=pctx.max_output_bytes,
                               max_command_timeout_s=pctx.max_command_timeout_s,
                               images=pctx.images, env_policy=pctx.env_policy)
        sink = EventSink(redactor=default_redactor())
        sink.subscribe(_shown_as(wiring.renderer.handle, number))
        approval = parent.approval
        if approval is not None:
            ask = approval

            def approval(request):
                return ask(dataclasses.replace(request,
                                               display=f"agent {number}: {request.display}"))

        budget = parent.budget
        return AgentRuntime(
            backend=parent.backend, registry=registry, policy=parent.policy,
            context=context, tool_ctx=tool_ctx, session=store, events=sink,
            approval=approval,
            budget=Budget(max_tool_rounds=budget.max_tool_rounds,
                          max_tool_calls=budget.max_tool_calls, max_tokens=budget.max_tokens,
                          max_consecutive_failures=budget.max_consecutive_failures),
            model=parent.model, max_output_tokens=parent.max_output_tokens,
            stream_max_retries=parent.stream_max_retries, audit=parent.audit,
            git_baseline=parent.git_baseline, credentials=parent.credentials,
            persist_rule=parent.persist_rule, auto_compact=parent.auto_compact,
            compact_prompt=parent.compact_prompt,
            show_reasoning=False, scrub=parent.scrub, hooks=parent.hooks,
        )


def _shown_as(handle, number: int):
    """What the sub-agent does, shown as its own: its calls named ``agent N``."""
    def show(event) -> None:
        if isinstance(event, (ToolBegin, ToolRejected)):
            handle(dataclasses.replace(event, display=f"agent {number}: {event.display}"))
        elif isinstance(event, _SHOWN):
            handle(event)
    return show

