"""How a task ends: the finish gate's evidence and checks, the outcome,
and the termination journaled and announced.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from foundry.core.errors import FoundryError
from foundry.core.events import Notice, StepStatus, TerminalStatus, Termination
from foundry.core.policy.engine import Mode
from foundry.core.policy.segmenter import segment_command
from foundry.core.session import EventType
from foundry.core.tools.command import MAX_TIMEOUT_S
from foundry.core.tools.finish import ValidationClaim, verify_claims

# The user's check (D-213): exec --verify, or runtime.verify_command. Foundry
# runs it itself when the model finishes as completed; a failure refuses the
# finish and the model goes on, told what it printed -- aider's auto-test and
# a Stop hook that blocks, as Claude Code has it. At the last check, or with
# no request left to fix anything in, the task ends partial instead.
VERIFY_ATTEMPTS = 3
VERIFY_REFUSED = (
    "finish was not accepted: the user's check, `{command}`, ran as you finished and "
    "failed ({how}) -- check {attempt} of {attempts}. Find the cause in what it printed, "
    "fix it, and call finish again; if it cannot pass, finish with status partial and "
    "say why.\n\n{output}")
VERIFY_FAILED = "the user's check failed"


def verify_problem(command: str) -> str:
    """Why ``command`` cannot be the user's check, or "": a list of commands
    is not one, for only the last one's exit code survives (D-213)."""
    if len(segment_command(command).segments) > 1:
        return ("it runs several commands, and only the last one's exit code survives "
                "in PowerShell 5.1; name one command, or a script that runs them all")
    return ""


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    status: TerminalStatus | None
    text: str
    summary: str = ""
    claims: tuple[ValidationClaim, ...] = ()
    # finish's `result` as JSON text under exec --output-schema (D-051);
    # None when there is none.
    result_json: str | None = None


class TaskEnding:
    """AgentRuntime's ending of a task: a mixin of methods, no state of its
    own."""

    __slots__ = ()

    def _finalize(self, outcome: TurnOutcome) -> TurnOutcome:
        """The gate: a claimed ``completed`` must survive the journal and Git."""
        run_command = self.registry.tools.get("run_command")
        history = getattr(run_command, "history", [])
        result = verify_claims(list(outcome.claims), history, outcome.status.value,
                               last_mutation_ordinal=self._last_mutation)

        for claim in outcome.claims:
            self._journal(EventType.VALIDATION_CLAIM, claim.as_payload())

        rejected = list(result.rejected)
        status, reason = result.status, result.reason
        summary = outcome.summary
        if self._unverified:
            # Its last check failed, or it had no request left to fix anything
            # in: the finish ends the task, but not as completed (D-213).
            rejected.append(self._unverified)
            if status is TerminalStatus.COMPLETED:
                status, reason = TerminalStatus.PARTIAL, VERIFY_FAILED
        elif self._verified:
            summary += f"\n\nChecked by Foundry as the task ended: {self._verified}."

        # Fresh Git evidence at finish. A model that moved HEAD -- committing
        # through a wrapper the breaker's argv scan cannot see -- would leave a
        # clean-looking diff, so the baseline comparison is what catches it.
        evidence = self._collect_git_evidence()
        if evidence is not None:
            self._journal(EventType.GIT_BASELINE, {
                "phase": "final",
                "head_now": evidence.head_now,
                "head_moved": evidence.head_moved,
                "session_changed": sorted(evidence.session_changed),
                "preexisting_changed": sorted(evidence.preexisting_changed),
                "outside_changed": sorted(evidence.outside_changed),
            })
            if evidence.head_moved:
                rejected.append(
                    f"HEAD moved during the session ({self.git_baseline.head[:12]} -> "
                    f"{evidence.head_now[:12]}); the diff no longer reflects what changed"
                )
                status = TerminalStatus.PARTIAL
                reason = "HEAD moved during the session"
            outside = evidence.outside_changed
            if evidence.session_changed or evidence.preexisting_changed or outside:
                summary += "\n\nFiles changed by this session:\n" + (
                    "\n".join(f"  - {p}" for p in sorted(evidence.session_changed))
                    or "  (none)")
                if evidence.preexisting_changed:
                    summary += ("\nAlready modified before this session (not claimed):\n"
                                + "\n".join(f"  - {p}" for p in sorted(evidence.preexisting_changed)))
                if outside:
                    # D-177: the user's, written while the session was stopped.
                    summary += ("\nChanged outside this session since it started (not "
                                "claimed):\n" + "\n".join(f"  - {p}" for p in sorted(outside)))

        for problem in rejected:
            self._emit(Notice(f"claim rejected: {problem}", level="warning"))
        if rejected:
            summary += "\n\nRejected claims:\n" + "\n".join(f"  - {r}" for r in rejected)

        # The plan is the model's own account, so it decides nothing: a step it
        # forgot to tick is not a failed task, and ticking every step proves
        # nothing was done. What it can do is show where the account and the
        # outcome disagree.
        unfinished = [s for s in self.plan if s.status is not StepStatus.COMPLETED]
        if unfinished:
            summary += "\n\nPlan steps not marked completed:\n" + "\n".join(
                f"  - [{s.status.value.replace('_', ' ')}] {s.step}" for s in unfinished)
            if status is TerminalStatus.COMPLETED:
                self._emit(Notice(f"finish reported completed with {len(unfinished)} plan "
                                  "step(s) not marked completed", level="warning"))

        return self._terminate(status, reason, summary, result_json=outcome.result_json)

    def _verify_finish(self) -> str:
        """The user's check, run as the model finishes as completed (D-213):
        what the model is told when it failed and the task goes on, or "" when
        the finish stands -- passed, not checked, or failed for the last time,
        which ``_finalize`` then reports."""
        command = self.verify_command
        if (not command or self._finish.status is not TerminalStatus.COMPLETED
                # A run that may change nothing has nothing to check, and a
                # check may write (a test cache, a build).
                or self.policy.read_only or self.policy.mode is Mode.PLAN):
            return ""
        self._verify_runs += 1
        attempt = self._verify_runs
        self._emit(Notice(f"the model finished: running your check, {command} "
                          f"(check {attempt} of {VERIFY_ATTEMPTS})"))
        tool = self.registry.get("run_command")
        timeout = min(MAX_TIMEOUT_S, getattr(self.tool_ctx, "max_command_timeout_s",
                                             MAX_TIMEOUT_S))
        try:
            op = tool.validate({"command": command, "timeout_s": timeout})
            output = tool.execute(op, self.tool_ctx)
        except FoundryError as exc:
            meta, content = {}, f"$ {command}\nthe check could not be run: {exc}"
        else:
            meta, content = output.metadata, output.content
        ordinal = meta.get("event_ordinal") or 0
        if ordinal:
            content += f"\n[event_id={ordinal}]"
        passed = (meta.get("exit_code") == 0 and not meta.get("timed_out")
                  and not meta.get("output_incomplete"))
        how = ("passed" if passed
               else f"timed out after {meta['timeout_s']}s" if meta.get("timed_out")
               else "its output could not all be read" if meta.get("output_incomplete")
               else f"exit code {meta['exit_code']}" if meta.get("exit_code") is not None
               else "it could not be run")
        last = attempt >= VERIFY_ATTEMPTS or self._last_turn is not None
        verdict = "accepted" if passed else "partial" if last else "refused"
        self._journal(EventType.VERIFY, {"command": command, "attempt": attempt,
                                         "event_ordinal": ordinal, "outcome": how,
                                         "finish": verdict})
        if passed:
            self._verified, self._unverified = f"{command} exited 0", ""
            self._emit(Notice(f"your check passed: {command}"))
            return ""
        if last:
            self._unverified = (f"the user's check, {command}, failed as the model finished "
                                f"({how}; check {attempt} of {VERIFY_ATTEMPTS})")
            self._emit(Notice(f"your check failed ({how}); the task ends partial",
                              level="warning"))
            return ""
        self._emit(Notice(f"your check failed ({how}); the model is told and goes on",
                          level="warning"))
        return VERIFY_REFUSED.format(command=command, how=how, attempt=attempt,
                                     attempts=VERIFY_ATTEMPTS, output=content)

    def _refresh_credentials(self) -> bool:
        """Re-acquire once per session, and only retry if the value changed.

        Never invalidates first: for the gateway source that clears the stored
        credential, and nothing can re-acquire it yet -- one expired token would
        log the user out permanently. Re-acquiring is harmless for both sources
        and picks up a token refreshed out of band.
        """
        if self.credentials is None or self._refreshed:
            return False
        self._refreshed = True
        if not hasattr(self.backend, "api_key"):
            return False
        try:
            handle = self.credentials.acquire()
        except FoundryError:
            return False
        fresh = handle.reveal()
        if fresh == self.backend.api_key:
            return False  # same credential: a second request would fail identically
        self.backend.api_key = fresh
        return True

    def _collect_git_evidence(self):
        if self.git_baseline is None:
            return None
        from foundry.core.tools.git import collect_evidence

        try:
            return collect_evidence(self.tool_ctx.workspace.root, self.git_baseline)
        except FoundryError as exc:
            self._emit(Notice(f"could not collect final Git evidence: {exc}", level="warning"))
            return None

    def _terminate(self, status: TerminalStatus, reason: str, summary: str = "",
                   result_json: str | None = None) -> TurnOutcome:
        if status is TerminalStatus.CANCELLED:
            self._answer_left()
            # Honoured, so spent (D-062): a Ctrl+C or an abort stops the task
            # it came in, and the next one starts afresh.
            self._cancelled = False
            # A second Ctrl+C while they run still ends the session (D-062).
            self.interrupt_hooks()
        # The sink holds a tail of streamed text so a credential cannot straddle
        # two deltas unseen. Every path here is followed by an event that would
        # release it anyway; flushing first makes that independent of ordering.
        flush = getattr(self.events, "flush", None)
        if callable(flush):
            flush()

        # A journal that could not be written means the evidence for this run is
        # incomplete; the report must say so rather than reading as a clean
        # record of what happened.
        degraded = getattr(self.session, "degraded", "") if self.session else ""
        if degraded:
            summary += (f"\n\nThe session journal could not be written ({degraded}); "
                        "this run's recorded evidence is incomplete.")
            self._emit(Notice(f"session journal degraded: {degraded}", level="warning"))

        audit_degraded = getattr(self.audit, "degraded", "") if self.audit else ""
        if audit_degraded:
            summary += (f"\n\nThe audit log could not be written ({audit_degraded}); "
                        "the record of what was allowed and denied is incomplete.")
            self._emit(Notice(f"audit log degraded: {audit_degraded}", level="warning"))

        record: dict[str, Any] = {"status": status.value, "reason": reason, "summary": summary}
        result = json.loads(result_json) if result_json is not None else None
        if result_json is not None:
            record["result"] = result
        self._journal(EventType.TERMINATION, record)
        self._ended = (status, reason)
        if self.session is not None:
            self.session._terminated = True
        self._emit(Termination(status, reason, summary, result=result))
        # A budget or context termination has no model summary; the reason is
        # the only explanation the caller gets, so it must survive.
        text = summary or reason
        return TurnOutcome(status=status, text=text, summary=text, result_json=result_json)
