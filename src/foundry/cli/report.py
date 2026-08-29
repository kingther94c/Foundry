"""Offline analysis of recorded sessions.

Every project that got better at agent reliability did it by measuring: aider
re-ran a benchmark for each edit-format change, SWE-agent published per-feature
ablations. The journal already holds what is needed, so decisions like "add a
repo map", "enable the fuzzy match rung", or "drop this backend to whole-file
edits" can be argued from numbers rather than impressions.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from foundry.core.events import TerminalStatus
from foundry.core.session import EventType, SessionStore


@dataclass(slots=True)
class SessionReport:
    session_id: str
    status: TerminalStatus
    model: str = ""
    turns: int = 0
    tool_calls: int = 0
    patches_applied: int = 0
    patches_rejected: int = 0
    commands: int = 0
    commands_failed: int = 0
    denials: int = 0
    approvals: Counter = field(default_factory=Counter)
    input_tokens: int = 0
    output_tokens: int = 0
    rejected_claims: int = 0

    @property
    def patch_first_try_rate(self) -> float | None:
        total = self.patches_applied + self.patches_rejected
        return (self.patches_applied / total) if total else None


def analyse(journal: Path) -> SessionReport:
    report = SessionReport(session_id=journal.parent.name,
                           status=SessionStore.terminal_status(journal))
    for record in SessionStore.read_records(journal):
        payload = record.payload
        if record.type == EventType.SESSION_META:
            report.model = payload.get("model", "")
        elif record.type == EventType.MODEL_REQUEST:
            report.turns += 1
        elif record.type == EventType.TOOL_CALL:
            report.tool_calls += 1
        elif record.type == EventType.TOOL_RESULT:
            if payload.get("tool") == "apply_patch":
                if payload.get("is_error"):
                    report.patches_rejected += 1
                else:
                    report.patches_applied += 1
        elif record.type == EventType.COMMAND_EXEC:
            report.commands += 1
            if payload.get("exit_code") not in (0, None):
                report.commands_failed += 1
        elif record.type == EventType.POLICY_DECISION:
            if payload.get("verdict") == "deny":
                report.denials += 1
        elif record.type == EventType.APPROVAL:
            report.approvals[payload.get("choice", "?")] += 1
        elif record.type == EventType.TOKEN_USAGE:
            report.input_tokens += payload.get("input_tokens", 0)
            report.output_tokens += payload.get("output_tokens", 0)
    return report


def collect(root: Path) -> list[SessionReport]:
    reports = []
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        journal = directory / "events.jsonl"
        if journal.is_file():
            reports.append(analyse(journal))
    return reports


def summarize(reports: list[SessionReport]) -> dict:
    if not reports:
        return {"sessions": 0}

    statuses = Counter(r.status.value for r in reports)
    applied = sum(r.patches_applied for r in reports)
    rejected = sum(r.patches_rejected for r in reports)
    return {
        "sessions": len(reports),
        "statuses": dict(statuses),
        "patch_first_try_rate": round(applied / (applied + rejected), 3) if (applied + rejected) else None,
        "patches": {"applied": applied, "rejected": rejected},
        "commands": {
            "total": sum(r.commands for r in reports),
            "failed": sum(r.commands_failed for r in reports),
        },
        "policy_denials": sum(r.denials for r in reports),
        "tokens": {
            "input": sum(r.input_tokens for r in reports),
            "output": sum(r.output_tokens for r in reports),
        },
        "turns_per_session": round(sum(r.turns for r in reports) / len(reports), 1),
    }


def render(root: Path, *, as_json: bool = False) -> str:
    reports = collect(root)
    summary = summarize(reports)
    if as_json:
        return json.dumps(summary, indent=2)

    if not reports:
        return "no sessions recorded yet"

    lines = [f"{summary['sessions']} sessions in {root}", ""]
    for status, count in sorted(summary["statuses"].items()):
        lines.append(f"  {status:<12} {count}")
    lines.append("")
    rate = summary["patch_first_try_rate"]
    if rate is not None:
        lines.append(f"  patch first-try rate  {rate:.0%} "
                     f"({summary['patches']['applied']} applied, "
                     f"{summary['patches']['rejected']} rejected)")
    lines.append(f"  commands              {summary['commands']['total']} "
                 f"({summary['commands']['failed']} nonzero exit)")
    lines.append(f"  policy denials        {summary['policy_denials']}")
    lines.append(f"  turns per session     {summary['turns_per_session']}")
    lines.append(f"  tokens                {summary['tokens']['input']} in / "
                 f"{summary['tokens']['output']} out")
    return "\n".join(lines)
