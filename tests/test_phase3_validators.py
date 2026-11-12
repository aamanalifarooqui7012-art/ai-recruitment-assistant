from interview.schemas import (
    Difficulty, ExperienceLevel, GeneratedQuestion, Importance, ProjectUse,
    QuestionSlot, QuestionType, SkillBucket,
)
from interview.validators import check_question, validate_batch

JD = "We need strong Kubernetes knowledge for deploying services."
RESUME = "Built payment reconciliation APIs in Python and FastAPI serving 200 requests per second"


def _slot(bucket, sid=1):
    return QuestionSlot(
        slot_id=sid, skill_id="x", skill_name="X", importance=Importance.must_have, bucket=bucket,
        experience_level=ExperienceLevel.mid, difficulty=Difficulty.medium, question_type=QuestionType.technical,
        jd_quote="strong Kubernetes knowledge for deploying services",
        projects=[ProjectUse(project_name="P", what_candidate_did="d", resume_quote=RESUME)]
        if bucket == SkillBucket.project_hands_on else [],
    )


def _q(**over):
    base = dict(
        slot_id=1, question="How did you structure the reconciliation API and why?",
        answer_keywords=["api", "idempotency", "retries"],
    )
    base.update(over)
    return GeneratedQuestion(**base)


def test_valid_question_passes():
    assert check_question(_q(), _slot(SkillBucket.project_hands_on), JD, RESUME, {}) == []


def test_keyword_count():
    probs = check_question(_q(answer_keywords=["a"]), _slot(SkillBucket.project_hands_on), JD, RESUME, {})
    assert any("answer_keywords" in p for p in probs)


def test_gap_question_must_not_assume_experience():
    q = _q(question="In your project, how did you run Kubernetes clusters?")
    assert any("assumes" in p for p in check_question(q, _slot(SkillBucket.gap), JD, RESUME, {}))


def test_duplicate_questions_rejected():
    first = _q()
    probs = check_question(_q(slot_id=2), _slot(SkillBucket.project_hands_on, 2), JD, RESUME, {1: first})
    assert any("identical" in p for p in probs)


def test_batch_reports_missing_and_unknown_slots():
    slots = {1: _slot(SkillBucket.project_hands_on, 1), 2: _slot(SkillBucket.project_hands_on, 2)}
    newly, issues, warns = validate_batch([_q(), _q(slot_id=99)], slots, [1, 2], {}, JD, RESUME)
    assert list(newly) == [1] and 2 in issues and warns
