"""How a task ends: the finish gate's evidence and checks, the outcome,
and the termination journaled and announced.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from foundry.core.errors import FoundryError
from foundry.core.events import Notice, StepStatus, TerminalStatus, Termination
from foundry.core.session import EventType
from foundry.core.tools.finish import ValidationClaim, verify_claims


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
