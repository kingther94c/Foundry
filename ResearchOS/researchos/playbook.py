"""The playbook: perspective weights, general lessons and training history."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from tools.stats import hedge_update
from tools.textmask import find_terms

from researchos.perspectives import PERSPECTIVES


@dataclass
class Lesson:
    text: str
    applies_when: str
    perspective: str
    case_id: str


@dataclass
class Playbook:
    weights: dict[str, float]
    lessons: list[Lesson] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)

    @classmethod
    def initial(cls) -> "Playbook":
        return cls(weights={p: 1.0 / len(PERSPECTIVES) for p in PERSPECTIVES},
                   lessons=[], history=[])


def save_playbook(path, playbook: Playbook) -> None:
    path = Path(path)
    if path.parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "weights": playbook.weights,
        "lessons": [asdict(lesson) for lesson in playbook.lessons],
        "history": playbook.history,
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_playbook(path) -> Playbook:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Playbook(
        weights={k: float(v) for k, v in data.get("weights", {}).items()},
        lessons=[
            Lesson(text=d.get("text", ""), applies_when=d.get("applies_when", ""),
                   perspective=d.get("perspective", "general"), case_id=d.get("case_id", ""))
            for d in data.get("lessons", [])
        ],
        history=list(data.get("history", [])),
    )


def update_weights(playbook: Playbook, views: dict, actual_up: bool, eta: float = 0.5) -> None:
    """Hedge update of the weights with each perspective's squared error as its loss."""
    y = 1.0 if actual_up else 0.0
    losses = {name: (view.prob_up - y) ** 2 for name, view in views.items()}
    playbook.weights = hedge_update(playbook.weights, losses, eta)


def lesson_is_clean(lesson: Lesson, forbidden_terms: list[str]) -> bool:
    terms = [t for t in forbidden_terms if isinstance(t, str) and t]
    return not find_terms(lesson.text, terms) and not find_terms(lesson.applies_when, terms)


def add_lessons(playbook: Playbook, lessons: list[Lesson], forbidden_terms: list[str]) -> int:
    """Append the lessons that name none of forbidden_terms; return how many were rejected."""
    rejected = 0
    for lesson in lessons:
        if lesson_is_clean(lesson, forbidden_terms):
            playbook.lessons.append(lesson)
        else:
            rejected += 1
    return rejected


def render_for_prompt(playbook: Playbook, max_lessons: int = 20) -> str:
    """Weights and the most recent lessons as plain text (never the lessons' case ids)."""
    lines = ["Playbook", "", "Perspective weights (learned from past cases):"]
    if playbook.weights:
        for name, weight in playbook.weights.items():
            lines.append(f"- {name}: {weight:.3f}")
    else:
        lines.append("- (none)")
    lines.append("")
    total = len(playbook.lessons)
    if total == 0:
        lines.append("Lessons: none yet (no lessons learned so far).")
        return "\n".join(lines)
    shown = playbook.lessons[max(total - max(max_lessons, 0), 0):] if max_lessons > 0 else []
    lines.append(f"Lessons from past cases ({len(shown)} most recent of {total}):")
    for lesson in shown:
        lines.append(f"- [{lesson.perspective}] {lesson.text} (applies when: {lesson.applies_when})")
    return "\n".join(lines)
