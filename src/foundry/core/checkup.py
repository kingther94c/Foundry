"""What ``foundry checkup`` found, said three ways (D-204).

The checkup is for the first run on a machine whose results leave it only by
word of mouth. So each case gives its verdict in one line on the screen, with
a short code naming what went wrong; the feedback code puts every level's
verdict and those codes on one line, which holds no host name and nothing
secret, for the user to read out; and the report, kept on the machine, has
the detail and what to do about each case.
"""

from __future__ import annotations

from dataclasses import dataclass

OK, WARN, FAIL, SKIP = "OK", "WARN", "FAIL", "SKIP"
MARKS = {OK: "+", WARN: "~", FAIL: "!", SKIP: "-"}
FORMAT = "FB1"
# A level names at most this many codes in the feedback code, and how many
# more it had.
MAX_CODES = 3

LEVELS = {
    "E": "this machine",
    "C": "configuration",
    "N": "network",
    "P": "protocol",
    "T": "a task, end to end",
}

TELL_IN_WORDS = (
    "How the gateway takes a credential: a key that does not change, a token "
    "fetched from somewhere (a command, a web page), or a sign-in in a browser.",
    "How long such a token lasts before it has to be fetched again.",
    "Which protocol the gateway's own documentation names -- Chat Completions, "
    "Responses or Anthropic Messages -- and which models it offers.",
    "Who puts policy.toml on these machines, and whether one is meant to be "
    "there (M: in the code says whether there is one, and how many rules).",
)


@dataclass(frozen=True, slots=True)
class Case:
    """One thing checked: its level's letter, a name, the verdict, one line
    for the screen, a short code when it is not OK, and what to do."""

    level: str
    name: str
    status: str
    said: str
    code: str = ""
    fix: str = ""

    def __post_init__(self) -> None:
        if self.level not in LEVELS or self.status not in MARKS:
            raise ValueError(f"not a case: {self.level} {self.status}")
        if self.status in (WARN, FAIL) and not self.code.isalnum():
            raise ValueError(f"a {self.status} needs a short code: {self.name}")


class Found(list):
    """The cases found so far, each told to ``said`` as it is found: a step
    can wait seconds, and the screen should show the ones before it."""

    def __init__(self, said) -> None:
        super().__init__()
        self._said = said

    def append(self, case: Case) -> None:
        super().append(case)
        self._said(case)

    def extend(self, cases) -> None:
        for case in cases:
            self.append(case)


def level_status(cases: list[Case]) -> str:
    """A level fails with any case that fails; it warns with any that warns;
    with none checked, it was skipped."""
    statuses = {case.status for case in cases}
    if not statuses or statuses == {SKIP}:
        return SKIP
    for status in (FAIL, WARN):
        if status in statuses:
            return status
    return OK


def feedback_code(source: str, cases: list[Case], facts: dict[str, str]) -> str:
    """``FB1 f4c38e1 E+ C+ K:key-env M:- R:chat N!  N!=TLS1``: the format,
    the Foundry that ran, each level's mark (+ OK, ~ warned, ! failed,
    - skipped) with the configuration's facts after C, and then, for each
    level that was not OK, the codes of its cases that were not."""
    parts = [FORMAT, source]
    notes = []
    for level in LEVELS:
        mine = [case for case in cases if case.level == level]
        if not mine:
            continue
        status = level_status(mine)
        parts.append(level + MARKS[status])
        if level == "C":
            parts.extend(f"{name}:{value}" for name, value in facts.items())
        codes = ([case.code for case in mine if case.status == FAIL]
                 + [case.code for case in mine if case.status == WARN])
        if codes:
            shown = codes[:MAX_CODES]
            if len(codes) > MAX_CODES:
                shown.append(f"+{len(codes) - MAX_CODES}")
            notes.append(f"{level}{MARKS[status]}={','.join(shown)}")
    return " ".join(parts) + ("  " + " ".join(notes) if notes else "")


def report(*, when: str, about: list[str], cases: list[Case], code: str) -> str:
    """The report kept on the machine, as Markdown: the code, the summary,
    each level's cases with what to do, and what only the user can say."""
    lines = [f"# Foundry checkup, {when}", "",
             f"Feedback code, to read out: `{code}`", ""]
    lines += [f"- {fact}" for fact in about]
    lines += ["", "## Summary", ""]
    for level, title in LEVELS.items():
        mine = [case for case in cases if case.level == level]
        if not mine:
            continue
        status = level_status(mine)
        codes = [case.code for case in mine if case.status in (FAIL, WARN)]
        lines.append(f"- {level} {title}: {status}" + (f" ({', '.join(codes)})" if codes else ""))
    for level, title in LEVELS.items():
        mine = [case for case in cases if case.level == level]
        if not mine:
            continue
        lines += ["", f"## {level} {title}", ""]
        for case in mine:
            code_part = f" {case.code}" if case.code else ""
            lines.append(f"- **{case.status}{code_part}** {case.name}: {case.said}")
            if case.fix:
                lines.append(f"  - What to do: {case.fix}")
    lines += ["", "## To tell me in words", "",
              "A check cannot see these; with the feedback code, they are what is needed:", ""]
    lines += [f"{number}. {text}" for number, text in enumerate(TELL_IN_WORDS, 1)]
    return "\n".join(lines) + "\n"
