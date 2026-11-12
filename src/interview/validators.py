"""Code step: every generated question must pass these checks or it is rejected
and only that slot is regenerated."""
import re

from .schemas import GeneratedQuestion, QuestionSlot, Section, SkillBucket
from .textutil import jaccard

_PRESUMES_EXPERIENCE = re.compile(
    r"\b(in your (project|work|role|experience)|you (built|implemented|used|worked|developed|designed)"
    r"|your (project|experience|work) (with|on|in))\b",
    re.IGNORECASE,
)
DUPLICATE_THRESHOLD = 0.8
MIN_QUESTION_CHARS = 15


def check_question(
    q: GeneratedQuestion,
    slot: QuestionSlot,
    jd_text: str,
    resume_text: str,
    accepted: dict[int, GeneratedQuestion],
) -> list[str]:
    problems: list[str] = []

    if len(q.question.strip()) < MIN_QUESTION_CHARS:
        problems.append("question is empty or too short")
    if slot.section == Section.coding and "\n" not in q.question.strip():
        problems.append("coding question must include a short code snippet (on separate lines) in the question text")

    kws = [k for k in q.answer_keywords if k and k.strip()]
    if not 3 <= len(kws) <= 6:
        problems.append(f"answer_keywords must have 3-6 items (got {len(kws)})")
    no_proof = slot.section != Section.resume_based or slot.bucket in (SkillBucket.gap, SkillBucket.listed_only)
    if no_proof and _PRESUMES_EXPERIENCE.search(q.question):
        problems.append("question assumes hands-on experience that the resume does not show")

    for other_id, other in accepted.items():
        if jaccard(q.question, other.question) >= DUPLICATE_THRESHOLD:
            problems.append(f"question is almost identical to the question for slot {other_id}")
            break
    return problems


def validate_batch(
    generated: list[GeneratedQuestion],
    slots_by_id: dict[int, QuestionSlot],
    pending: list[int],
    accepted: dict[int, GeneratedQuestion],
    jd_text: str,
    resume_text: str,
) -> tuple[dict[int, GeneratedQuestion], dict[int, list[str]], list[str]]:
    """Returns (newly_accepted, issues_by_slot for slots still failing, warnings)."""
    pending_set = set(pending)
    newly: dict[int, GeneratedQuestion] = {}
    issues: dict[int, list[str]] = {}
    warnings: list[str] = []
    seen: set[int] = set()

    for q in generated:
        if q.slot_id not in slots_by_id or q.slot_id not in pending_set:
            warnings.append(f"Ignored a question for unknown or already-filled slot {q.slot_id}.")
            continue
        if q.slot_id in seen:
            warnings.append(f"Ignored a second question returned for slot {q.slot_id}.")
            continue
        seen.add(q.slot_id)
        problems = check_question(q, slots_by_id[q.slot_id], jd_text, resume_text, {**accepted, **newly})
        if problems:
            issues[q.slot_id] = problems
        else:
            newly[q.slot_id] = q

    for sid in pending_set - seen:
        issues[sid] = ["no question was returned for this slot"]
    return newly, issues, warnings
