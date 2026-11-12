"""Phase 3A must reuse what Phase 1 and Phase 2 already extracted. It must not extract anything again."""
import copy
from datetime import date

import pytest

from interview.errors import InputError
from interview.from_phases import build_extraction, overall_experience
from interview.llm import MafLLMClient
from interview.pipeline import run_plain
from interview.schemas import GeneratedQuestionSet, SkillBucket
from interview.taxonomy import SkillTaxonomy

from phase3_helpers import DATA, demo_llm, load, make_svc, run, sample_request

TODAY = date(2026, 1, 15)
TAX = SkillTaxonomy.from_yaml()
P1, KW, LAYOUT = load("sample_phase1.json"), load("sample_phase2_keywords.json"), load("sample_phase2_layout.json")


class SpyLLM:
    """Records every model call. Only question wording may be asked of the model."""
    def __init__(self):
        self.schemas, self.prompts = [], []
        self._inner = demo_llm()

    async def generate(self, schema, *, role, instructions, prompt):
        self.schemas.append(schema)
        self.prompts.append((instructions, prompt))
        return await self._inner.generate(schema, role=role, instructions=instructions, prompt=prompt)


def test_the_model_is_only_asked_to_write_questions(tmp_path):
    spy = SpyLLM()
    sc = run(run_plain(sample_request(), make_svc(tmp_path, llm=spy)))
    assert sc.questions
    assert spy.schemas and set(spy.schemas) == {GeneratedQuestionSet}      # no extraction call, ever
    assert not any("extract skill evidence" in ins for ins, _ in spy.prompts)


def test_the_extraction_prompts_are_gone():
    import interview.prompts as prompts
    assert not hasattr(prompts, "extraction_instructions") and not hasattr(prompts, "build_extraction_prompt")
    from interview.llm import AGENT_NAMES
    assert set(AGENT_NAMES) == {"generate"}


def test_the_client_has_only_the_question_writer_role():
    import typing
    from interview.llm import Role
    assert typing.get_args(Role) == ("generate",)
    assert MafLLMClient is not None


def test_jd_skills_come_from_phase1_and_evidence_from_phase2():
    e = build_extraction(P1["skills"], KW, LAYOUT, TAX, TODAY)
    assert [s.skill_id for s in e.jd_skills] == ["python", "fastapi", "postgresql", "docker", "kubernetes", "kafka", "redis", "aws"]
    by = {x.skill_id: x for x in e.evidence}
    assert set(by) == {"python", "fastapi", "postgresql", "docker", "redis"}       # what Phase 2 found, nothing more
    assert by["docker"].listed_quote == "Tools: Docker, Git, Redis" and not by["docker"].project_uses
    assert by["python"].project_uses[0].resume_quote.endswith("serving 200 requests per second")
    assert "kubernetes" not in by and "kafka" not in by                              # not in Phase 2 -> gap


def test_project_names_come_from_the_resume_layout():
    by = {x.skill_id: x for x in build_extraction(P1["skills"], KW, LAYOUT, TAX, TODAY).evidence}
    assert [p.project_name for p in by["fastapi"].project_uses] == ["Backend Developer, Acme Fintech", "Invoice Matching Service"]
    assert by["redis"].project_uses[0].project_name == "Personal Blog Platform"     # 2 projects share one Phase 2 entry
    names = [p.project_name for p in build_extraction(P1["skills"], KW, None, TAX, TODAY).evidence[0].project_uses]
    assert names == ["Experience entry 2"]                                           # no layout -> neutral label, nothing invented


def test_skill_years_are_date_arithmetic_on_phase2_dates():
    by = {x.skill_id: x for x in build_extraction(P1["skills"], KW, LAYOUT, TAX, TODAY).evidence}
    assert by["python"].years_on_skill == 5.1 and by["python"].years_basis == "inferred_from_dates"   # Jan 2021 -> Jan 2026
    assert by["redis"].years_on_skill is None and by["redis"].years_basis == "unknown"                # undated project: not guessed
    assert by["docker"].years_on_skill is None


def test_overlapping_periods_are_counted_once():
    kw = {"hands_on": [{"skill_id": "python", "name": "Python", "evidence": [
        {"quote": "a", "section": "experience", "entry": 0, "dates": {"start": "2020-01", "end": "2022-01"}},
        {"quote": "b", "section": "experience", "entry": 1, "dates": {"start": "2021-01", "end": "2023-01"}}]}], "theoretical": []}
    e = build_extraction(P1["skills"], kw, None, TAX, TODAY)
    assert e.evidence[0].years_on_skill == 3.1                                      # 2020-01..2023-01 once, not 4 years


def test_overall_experience_is_the_larger_of_the_summary_and_the_job_dates():
    oe = overall_experience(LAYOUT, TODAY)
    assert oe.years == 5.1 and oe.basis == "inferred_from_dates" and "Jan 2021" in oe.quote   # "5 years" < 5.1 from dates
    assert overall_experience(LAYOUT, date(2025, 6, 1)).basis == "explicit"                   # 4.4 from dates < 5 stated
    assert overall_experience(None, TODAY).years is None


def test_soft_skills_and_unmapped_skills_are_handled():
    skills = copy.deepcopy(P1["skills"][:1]) + [
        {"raw_text": "Communication", "standard_id": "Unmapped", "category": "Soft skill", "importance": "must_have"},
        {"raw_text": "Zorbex Engine", "standard_id": "Unmapped", "category": "Tool", "importance": "nice_to_have",
         "evidence_quote": "Zorbex Engine is a plus"}]
    kw = {"hands_on": [], "theoretical": [{"skill_id": "Unmapped", "name": "Zorbex Engine", "category": "Unmapped",
                                           "evidence": [{"quote": "Used Zorbex Engine", "section": "skills", "entry": 0, "dates": None}]}]}
    e = build_extraction(skills, kw, None, TAX, TODAY)
    assert [s.name for s in e.jd_skills] == ["Python", "Zorbex Engine"]                # soft skill skipped
    assert e.jd_skills[1].skill_id == "zorbex-engine"
    assert [x.listed_quote for x in e.evidence] == ["Used Zorbex Engine"]               # matched to the Phase 2 unmapped item


def test_a_skill_phase2_did_not_find_stays_a_gap_even_if_the_text_mentions_it(tmp_path):
    """No second look at the resume text: the Phase 2 result (and the Phase 2.5 score) is the single truth."""
    req = sample_request()
    req.resume_text += "\nAlso familiar with Kubernetes and Apache Kafka."
    sc = run(run_plain(req, make_svc(tmp_path)))
    prof = {p.skill_id: p for p in sc.skill_profiles}
    assert prof["kubernetes"].bucket == SkillBucket.gap and prof["kafka"].bucket == SkillBucket.gap


def test_a_phase2_quote_that_is_not_in_the_resume_is_still_dropped(tmp_path):
    req = sample_request()
    kw = copy.deepcopy(req.resume_keywords)
    kw["hands_on"].append({"skill_id": "kafka", "name": "Apache Kafka", "category": "Tool", "sources": ["llm"], "also_in_theory": False,
                           "evidence": [{"quote": "Ran a 50-node Kafka cluster at Google", "section": "experience", "entry": 1, "dates": None}]})
    req.resume_keywords = kw
    sc = run(run_plain(req, make_svc(tmp_path)))
    assert {p.skill_id: p for p in sc.skill_profiles}["kafka"].bucket == SkillBucket.gap
    assert any("kafka" in w.lower() and "not found in resume" in w for w in sc.warnings)


def test_missing_phase_inputs_are_a_clear_error_not_a_hidden_extraction(tmp_path):
    from pydantic import ValidationError
    from interview.schemas import ScaffoldRequest
    with pytest.raises(ValidationError):                                             # skills and keywords are required
        ScaffoldRequest(jd_text="x" * 60, resume_text="y" * 90)
    req = sample_request()
    req.resume_keywords = {"unrelated": 1}
    with pytest.raises(InputError, match="Phase 2 keywords"):
        run(run_plain(req, make_svc(tmp_path)))


def test_a_jd_without_technical_skills_is_reported(tmp_path):
    req = sample_request()
    req.jd_skills = [{"raw_text": "Teamwork", "standard_id": "Unmapped", "category": "Soft skill", "importance": "must_have"}]
    from interview.errors import PipelineError
    with pytest.raises(PipelineError, match="no technical JD skills"):
        run(run_plain(req, make_svc(tmp_path)))
