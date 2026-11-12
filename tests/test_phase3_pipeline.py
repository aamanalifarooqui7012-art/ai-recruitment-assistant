from interview.fakes import OfflineDemoLLM
from interview.pipeline import run_plain
from interview.schemas import GeneratedQuestionSet, QuestionType, ScaffoldStatus, Section, SkillBucket

from phase3_helpers import demo_llm, make_svc, run, sample_request


def _by_skill(scaffold):
    return {p.skill_id: p for p in scaffold.skill_profiles}


def test_end_to_end_with_offline_llm(tmp_path):
    svc = make_svc(tmp_path)
    sc = run(run_plain(sample_request(), svc))

    assert sc.status == ScaffoldStatus.complete
    assert sc.disclaimer == "AI-generated, needs human review"
    assert sc.review_status == "pending_human_review"
    assert len(sc.questions) == svc.cfg.questions.grand_total == 20   # 4 sections x 5

    prof = _by_skill(sc)
    assert prof["python"].bucket == SkillBucket.project_hands_on
    assert prof["redis"].bucket == SkillBucket.project_hands_on
    assert prof["docker"].bucket == SkillBucket.listed_only
    assert prof["python"].difficulty.value == "hard" and prof["python"].level_basis.value == "skill_years"
    assert prof["redis"].level_basis.value == "overall_years"     # no years for Redis -> overall experience
    assert sc.candidate_overall_years >= 5                        # "5 years" in the summary, or more from the job dates
    assert 5 <= prof["python"].years_on_skill <= sc.candidate_overall_years   # from the Phase 2 dates of the Acme job
    assert prof["kubernetes"].bucket == SkillBucket.gap           # Phase 2 found no Kubernetes in the resume
    assert prof["kafka"].bucket == SkillBucket.gap

    covered = {q.skill_id for q in sc.questions}
    assert {"kubernetes", "kafka"} <= covered

    assert svc.store.get(sc.scaffold_id) is not None          # saved


class _FabricatesFirstRound(OfflineDemoLLM):
    """Returns an invalid question (one keyword only) for slot 1 in round 1, then behaves. (One call per section: 4 in round 1.)"""
    def __init__(self, base, always=False):
        super().__init__()
        self.calls, self.always, self.fabricated = 0, always, False

    async def generate(self, schema, *, role, instructions, prompt):
        result = await super().generate(schema, role=role, instructions=instructions, prompt=prompt)
        if schema is GeneratedQuestionSet:
            self.calls += 1
            first = next((q for q in result.questions if q.slot_id == 1), None)   # slot 1 = first "basic" slot
            if first is not None and (self.always or not self.fabricated):
                self.fabricated = True
                first.answer_keywords = ["only-one"]
        return result


def test_failed_slot_is_regenerated(tmp_path):
    llm = _FabricatesFirstRound(demo_llm())
    sc = run(run_plain(sample_request(), make_svc(tmp_path, llm=llm)))
    assert llm.calls == 4 + 1                       # round 1: one call per section; round 2: only slot 1's section
    assert sc.status == ScaffoldStatus.complete and not sc.unfilled_slots


def test_never_fixed_slot_ends_partial_not_crash(tmp_path):
    llm = _FabricatesFirstRound(demo_llm(), always=True)
    svc = make_svc(tmp_path, llm=llm)
    sc = run(run_plain(sample_request(), svc))
    assert llm.calls == 4 + svc.cfg.workflow.max_regeneration_rounds
    assert sc.status == ScaffoldStatus.partial and sc.unfilled_slots == [1]
    assert sc.validation_issues and "answer_keywords" in sc.validation_issues[0]
    assert len(sc.questions) == svc.cfg.questions.grand_total - 1   # valid ones are kept, human sees the gap
