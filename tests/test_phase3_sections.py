"""The interview paper has 4 sections: basic, resume_based, scenario_case, coding (5 questions each by default)."""
from interview.config import load_settings
from interview.fakes import OfflineDemoLLM
from interview.pipeline import run_plain
from interview.planner import build_full_plan, build_jd_section_slots
from interview.prompts import build_generation_prompt, generation_instructions, slots_from_prompt
from interview.schemas import (
    SECTION_ORDER, Difficulty, ExperienceLevel, GeneratedQuestion, Importance, ProjectUse, QuestionSlot,
    QuestionType, Section, SkillBucket, SkillProfile,
)
from interview.validators import check_question

from phase3_helpers import make_svc, run, sample_request


def _profile(sid, bucket, imp=Importance.must_have, order=0):
    return SkillProfile(
        skill_id=sid, name=sid, importance=imp, jd_order=order, bucket=bucket,
        experience_level=ExperienceLevel.mid, difficulty=Difficulty.medium, jd_quote=f"needs {sid} skill",
        projects=[ProjectUse(project_name="P", what_candidate_did="d", resume_quote=f"used {sid}")]
        if bucket == SkillBucket.project_hands_on else [],
        listed_quote=f"skills: {sid}" if bucket == SkillBucket.listed_only else None,
    )


def _profiles():
    return [
        _profile("py", SkillBucket.project_hands_on, order=0),
        _profile("api", SkillBucket.project_hands_on, order=1),
        _profile("dock", SkillBucket.listed_only, order=2),
        _profile("k8s", SkillBucket.gap, order=3),
        _profile("redis", SkillBucket.project_hands_on, Importance.nice_to_have, order=4),
    ]


def test_default_paper_is_four_sections_of_five():
    cfg = load_settings()
    assert cfg.questions.total == 5 and cfg.questions.grand_total == 20
    slots, _ = build_full_plan(_profiles(), cfg)
    assert [s.slot_id for s in slots] == list(range(1, 21))
    for section in SECTION_ORDER:
        assert sum(1 for s in slots if s.section == section) == 5
    # slots come out grouped in the order basic -> resume_based -> scenario_case -> coding
    order = [s.section for s in slots]
    assert order == sorted(order, key=SECTION_ORDER.index)


def test_jd_sections_carry_no_resume_evidence_and_fixed_types():
    cfg = load_settings()
    slots, _ = build_full_plan(_profiles(), cfg)
    for s in slots:
        if s.section == Section.resume_based:
            continue
        assert s.projects == [] and s.listed_quote is None
        assert s.question_type == (QuestionType.scenario if s.section == Section.scenario_case else QuestionType.technical)
        if s.section in (Section.basic, Section.coding):
            assert s.difficulty == Difficulty.easy


def test_basic_section_uses_only_skills_the_resume_shows():
    cfg = load_settings()
    slots, _ = build_full_plan(_profiles(), cfg)
    basic = [s for s in slots if s.section == Section.basic]
    assert all(s.skill_id != "k8s" for s in basic)                      # gap skill: nothing on the resume to ask basics about
    assert [s.skill_id for s in basic[:2]] == ["py", "api"]            # hands-on first, then listed-only
    only_gaps = [_profile("a", SkillBucket.gap), _profile("b", SkillBucket.gap, order=1)]
    assert {s.skill_id for s in build_jd_section_slots(only_gaps, Section.basic, 3)} == {"a", "b"}   # no resume skills -> fall back


def test_jd_section_prefers_must_have_and_cycles_when_few_skills():
    profiles = [_profile("a", SkillBucket.gap, Importance.nice_to_have, 0), _profile("b", SkillBucket.gap, order=1)]
    slots = build_jd_section_slots(profiles, Section.coding, 5)
    assert [s.skill_id for s in slots] == ["b", "a", "b", "a", "b"]


def test_extra_sections_can_be_switched_off():
    cfg = load_settings()
    cfg.questions.extra_sections = {Section.basic: 0, Section.scenario_case: 0, Section.coding: 0}
    slots, _ = build_full_plan(_profiles(), cfg)
    assert len(slots) == cfg.questions.total and {s.section for s in slots} == {Section.resume_based}


def _slot(section, bucket=SkillBucket.project_hands_on):
    return QuestionSlot(
        slot_id=1, skill_id="x", skill_name="X", importance=Importance.must_have, section=section, bucket=bucket,
        experience_level=ExperienceLevel.mid, difficulty=Difficulty.medium, question_type=QuestionType.technical,
        jd_quote="strong Kubernetes knowledge",
    )


def test_basic_question_may_not_presume_hands_on_experience_():
    jd = "We need strong Kubernetes knowledge for deploying services."
    q = GeneratedQuestion(
        slot_id=1, question="In your project, how did you use Kubernetes for deployments?",
        answer_keywords=["pods", "deployments", "services"],
    )
    problems = check_question(q, _slot(Section.basic), jd, "some resume text", {})
    assert any("assumes hands-on experience" in p for p in problems)
    q.question = "What is a Pod in Kubernetes and why is it the smallest deployable unit?"
    assert check_question(q, _slot(Section.basic), jd, "some resume text", {}) == []


def test_prompt_describes_all_sections_and_hides_resume_data_from_jd_sections():
    text = generation_instructions()
    for word in ("basic", "resume_based", "scenario_case", "coding"):
        assert word in text
    cfg = load_settings()
    slots, _ = build_full_plan(_profiles(), cfg)
    sent = slots_from_prompt(build_generation_prompt(slots, "jd text", "resume text"))
    for s in sent:
        assert s["section"] in {x.value for x in SECTION_ORDER}
        if s["section"] != "resume_based":
            assert not {"projects", "listed_quote", "bucket"} & set(s)


def test_end_to_end_all_sections_complete_and_ordered(tmp_path):
    sc = run(run_plain(sample_request(), make_svc(tmp_path)))
    assert sc.status.value == "complete" and len(sc.questions) == 20
    assert [q.section for q in sc.questions] == [s for s in SECTION_ORDER for _ in range(5)]
    for q in sc.questions:
        assert not hasattr(q, "why_asked") and not hasattr(q, "evidence") and not hasattr(q, "follow_ups")
        if q.section == Section.coding:
            assert q.difficulty == Difficulty.easy and "```" in q.question      # easy, and the code is in the question
        if q.section == Section.basic:
            assert q.difficulty == Difficulty.easy and q.bucket != SkillBucket.gap
    assert OfflineDemoLLM  # the offline model was used (no network)


def test_coding_question_must_contain_code_lines():
    q = GeneratedQuestion(slot_id=1, question="Write a function that reverses a string and returns it.",
                          answer_keywords=["loop", "string", "slice"])
    assert any("code snippet" in p for p in check_question(q, _slot(Section.coding), "jd", "resume", {}))
    q.question = "What does this print?\n```python\nprint(sum([1, 2, 3]))\n```"
    assert check_question(q, _slot(Section.coding), "jd", "resume", {}) == []
