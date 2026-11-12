from interview.classify import classify_skills, level_from_years, resolve_level
from interview.config import load_settings
from interview.evidence import VerifiedExtraction
from interview.planner import apportion, build_plan
from interview.schemas import (
    Difficulty, ExperienceLevel, Importance, JDSkill, LevelBasis, ProjectUse, QuestionType,
    SkillBucket, SkillEvidence, SkillProfile,
)


def _skill(sid, imp=Importance.must_have):
    return JDSkill(skill_id=sid, name=sid, importance=imp, required_experience="Not specified", jd_quote=f"needs {sid} skill")


def _proj(sid):
    return ProjectUse(project_name=f"{sid}-proj", what_candidate_did="x", resume_quote=f"used {sid} in prod")


def _profile(sid, bucket, imp=Importance.must_have, order=0):
    return SkillProfile(
        skill_id=sid, name=sid, importance=imp, jd_order=order, bucket=bucket,
        experience_level=ExperienceLevel.mid, difficulty=Difficulty.medium, jd_quote=f"needs {sid} skill",
        projects=[_proj(sid)] if bucket == SkillBucket.project_hands_on else [],
        listed_quote=f"skills: {sid}" if bucket == SkillBucket.listed_only else None,
    )


def test_levels_use_skill_years_not_career():
    cfg = load_settings()
    assert level_from_years(None, cfg) == ExperienceLevel.unknown
    assert level_from_years(0.5, cfg) == ExperienceLevel.fresher
    assert level_from_years(2, cfg) == ExperienceLevel.mid
    assert level_from_years(6, cfg) == ExperienceLevel.senior


def test_bucket_and_difficulty_rules():
    cfg = load_settings()
    skills = [_skill("a"), _skill("b"), _skill("c")]
    ev = {
        "a": SkillEvidence(skill_id="a", listed_quote=None, years_on_skill=6, years_basis="explicit", project_uses=[_proj("a")]),
        "b": SkillEvidence(skill_id="b", listed_quote="skills: b", years_on_skill=6, years_basis="explicit", project_uses=[]),
    }
    got = {p.skill_id: p for p in classify_skills(VerifiedExtraction(skills=skills, evidence=ev), cfg)}
    assert got["a"].bucket == SkillBucket.project_hands_on and got["a"].difficulty == Difficulty.hard
    assert got["b"].bucket == SkillBucket.listed_only and got["b"].difficulty == Difficulty.medium
    assert got["c"].bucket == SkillBucket.gap and got["c"].difficulty == Difficulty.easy
    assert got["c"].years_on_skill is None


def test_apportion_always_sums_to_n():
    mix = {QuestionType.technical: 0.5, QuestionType.scenario: 0.3, QuestionType.behavioural: 0.2}
    for n in range(1, 25):
        assert sum(apportion(mix, n).values()) == n
    assert apportion(mix, 8) == {QuestionType.technical: 4, QuestionType.scenario: 2, QuestionType.behavioural: 2}


def test_plan_honours_count_mix_and_type_rules():
    cfg = load_settings()
    cfg.questions.total = 8                  # the mix assertions below are written for 8 resume-based questions
    profiles = [
        _profile("py", SkillBucket.project_hands_on, order=0),
        _profile("api", SkillBucket.project_hands_on, order=1),
        _profile("db", SkillBucket.project_hands_on, order=2),
        _profile("dock", SkillBucket.listed_only, order=3),
        _profile("k8s", SkillBucket.gap, order=4),
        _profile("kafka", SkillBucket.gap, order=5),
        _profile("redis", SkillBucket.project_hands_on, Importance.nice_to_have, order=6),
        _profile("aws", SkillBucket.gap, Importance.nice_to_have, order=7),
    ]
    slots, warnings = build_plan(profiles, cfg)
    assert len(slots) == cfg.questions.total
    assert [s.slot_id for s in slots] == list(range(1, len(slots) + 1))
    for s in slots:
        if s.bucket == SkillBucket.gap:
            assert s.question_type == QuestionType.technical
        if s.question_type == QuestionType.behavioural:
            assert s.bucket == SkillBucket.project_hands_on
    ids = {s.skill_id for s in slots}
    assert {"k8s", "kafka"} <= ids          # must-have gaps always covered
    assert "aws" not in ids                  # nice-to-have gap: policy must_have_only
    assert max(sum(1 for s in slots if s.skill_id == i) for i in ids) <= cfg.questions.max_per_skill
    got = {t: sum(1 for s in slots if s.question_type == t) for t in QuestionType}
    assert got == {QuestionType.technical: 4, QuestionType.scenario: 2, QuestionType.behavioural: 2}


def test_must_have_gaps_raise_the_total():
    cfg = load_settings()
    cfg.questions.total = 2
    profiles = [_profile(f"g{i}", SkillBucket.gap, order=i) for i in range(4)]
    slots, warnings = build_plan(profiles, cfg)
    assert len(slots) == 4 and warnings


def test_resolve_level_order_skill_then_overall_then_unknown():
    cfg = load_settings()
    assert resolve_level(5, 0.5, cfg) == (ExperienceLevel.senior, LevelBasis.skill_years)      # skill years win
    assert resolve_level(None, 8, cfg) == (ExperienceLevel.senior, LevelBasis.overall_years)   # fallback to overall
    assert resolve_level(None, 0.4, cfg) == (ExperienceLevel.fresher, LevelBasis.overall_years)
    assert resolve_level(None, None, cfg) == (ExperienceLevel.unknown, LevelBasis.unknown)


def test_fresher_gets_medium_experienced_gets_hard_and_unknown_gets_medium():
    cfg = load_settings()
    skills = [_skill("a")]
    ev = {"a": SkillEvidence(skill_id="a", listed_quote=None, years_on_skill=None, years_basis="unknown",
                             project_uses=[_proj("a")])}
    cases = [(0.4, Difficulty.medium, LevelBasis.overall_years),
             (8, Difficulty.hard, LevelBasis.overall_years),
             (None, Difficulty.medium, LevelBasis.unknown)]
    for overall, difficulty, basis in cases:
        p = classify_skills(VerifiedExtraction(skills=skills, evidence=ev, overall_years=overall), cfg)[0]
        assert p.difficulty == difficulty and p.level_basis == basis


def test_skill_years_beat_overall_years():
    cfg = load_settings()
    ev = {"a": SkillEvidence(skill_id="a", listed_quote=None, years_on_skill=0.5, years_basis="explicit",
                             project_uses=[_proj("a")])}
    p = classify_skills(VerifiedExtraction(skills=[_skill("a")], evidence=ev, overall_years=9), cfg)[0]
    assert p.experience_level == ExperienceLevel.fresher and p.difficulty == Difficulty.medium
    assert p.level_basis == LevelBasis.skill_years
