import pytest
from tools.stats import hedge_update

from researchos.perspectives import PERSPECTIVES, View
from researchos.playbook import (Lesson, Playbook, add_lessons, load_playbook, render_for_prompt,
                                 save_playbook, update_weights)


def test_initial():
    pb = Playbook.initial()
    assert list(pb.weights) == PERSPECTIVES
    assert all(w == pytest.approx(1 / 7) for w in pb.weights.values())
    assert pb.lessons == [] and pb.history == []


def test_round_trip(tmp_path):
    pb = Playbook.initial()
    pb.lessons.append(Lesson("t", "w", "growth", "c1"))
    pb.history.append({"case_id": "c1", "brier_judge": 0.1})
    path = tmp_path / "sub" / "pb.json"
    save_playbook(path, pb)
    assert load_playbook(path) == pb


def test_update_weights():
    pb = Playbook.initial()
    views = {p: View("neutral", 0.9 if p == "growth" else 0.2, "low", []) for p in PERSPECTIVES}
    expected = hedge_update(pb.weights, {p: (v.prob_up - 1.0) ** 2 for p, v in views.items()},
                            0.5)
    update_weights(pb, views, True)
    assert pb.weights == expected
    assert max(pb.weights, key=pb.weights.get) == "growth"
    assert sum(pb.weights.values()) == pytest.approx(1.0)
    update_weights(pb, views, False, eta=2.0)
    assert pb.weights["growth"] < expected["growth"]


def test_add_lessons_rejects_identity_whole_word_case_insensitive():
    pb = Playbook.initial()
    lessons = [
        Lesson("Margins matter.", "always", "profitability", "c"),
        Lesson("ACME beat on margins.", "always", "profitability", "c"),
        Lesson("Guidance matters.", "when acmw guides up", "guidance", "c"),
        Lesson("Acmeville-style towns are irrelevant.", "never", "general", "c"),
    ]
    assert add_lessons(pb, lessons, ["Acme", "ACMW", ""]) == 2
    assert [l.text for l in pb.lessons] == ["Margins matter.",
                                            "Acmeville-style towns are irrelevant."]


def test_render_for_prompt():
    pb = Playbook.initial()
    text = render_for_prompt(pb)
    assert "growth" in text and "0.143" in text
    assert "no lessons" in text.lower()
    for i in range(25):
        pb.lessons.append(Lesson(f"lesson-{i:02d}", f"when-{i}", "general", f"secret-case-{i}"))
    text = render_for_prompt(pb)
    assert "lesson-24" in text and "lesson-05" in text and "lesson-04" not in text
    assert "secret-case" not in text
    small = render_for_prompt(pb, max_lessons=2)
    assert "lesson-23" in small and "lesson-22" not in small
    assert "lesson-" not in render_for_prompt(pb, max_lessons=0)
