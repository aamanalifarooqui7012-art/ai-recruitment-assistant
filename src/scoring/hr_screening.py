"""HR screening marks (Phase 1 questions, marked per candidate). Pure code, no LLM.

HR reads a candidate's answer against the question's keywords / model answer and marks it
Met, Partly met or Not met. The HR score is kept SEPARATE from the 100-point resume score.
"""
from pathlib import Path

NOT_MARKED = "Not marked"
_FALLBACK = {"Met": 1.0, "Partly met": 0.5, "Not met": 0.0}
from project_config import CONFIG_PATH


def _load_points():
    try:
        import yaml
        raw = ((yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}).get("scoring") or {}).get("hr_screening") or {}
        pts = {str(k): float(v) for k, v in raw.items()}
        return pts or dict(_FALLBACK)
    except Exception:
        return dict(_FALLBACK)


HR_POINTS = _load_points()
MARK_CHOICES = [NOT_MARKED, *HR_POINTS]


def hr_score(marks):
    """marks: iterable of 'Met' / 'Partly met' / 'Not met' / None / 'Not marked'.
    Returns (points, number_of_marked_questions). Unmarked questions add nothing."""
    done = [HR_POINTS[m] for m in marks if m in HR_POINTS]
    return sum(done), len(done)


def blank_packet(questions, resume_names):
    """The HR step's output: the questions plus an empty mark sheet per candidate."""
    return {
        "questions": list(questions),
        "points": dict(HR_POINTS),
        "candidates": {name: {"marks": [None] * len(questions)} for name in resume_names},
    }
