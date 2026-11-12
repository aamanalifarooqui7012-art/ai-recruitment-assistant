"""The 3A steps as plain functions. Both engines (plain loop and MAF workflow)
call exactly these, so behaviour is identical.

    prepare -> load_evidence -> verify_and_classify -> plan -> generate[LLM] -> validate
                                                               ^______retry failed slots___|
                                                                         -> finalize -> save
"""
import uuid
from dataclasses import dataclass, field

from .classify import classify_skills
from .errors import InputError, PipelineError
from .evidence import VerifiedExtraction, verify_extraction
from .planner import build_full_plan
from .from_phases import build_extraction
from .prompts import build_generation_prompt, generation_instructions
from .schemas import (
    ExtractionResult,
    GeneratedQuestion,
    GeneratedQuestionSet,
    InterviewQuestion,
    InterviewScaffold,
    QuestionSlot,
    ScaffoldRequest,
    ScaffoldStatus,
    SECTION_ORDER,
    Section,
    SkillBucket,
    SkillProfile,
    Importance,
)
from .services import Services
from .textutil import clean_text, mask_pii
from .validators import validate_batch


@dataclass
class PipelineState:
    scaffold_id: str
    jd_text: str
    resume_text: str
    candidate_label: str | None
    jd_id: str | None
    max_rounds: int
    jd_skills: list[dict] = field(default_factory=list)   # Phase 1 skills
    resume_keywords: dict = field(default_factory=dict)   # Phase 2 keywords
    resume_layout: dict | None = None                      # Phase 2 layout
    extraction: ExtractionResult | None = None
    verified: VerifiedExtraction | None = None
    profiles: list[SkillProfile] = field(default_factory=list)
    slots: list[QuestionSlot] = field(default_factory=list)
    accepted: dict[int, GeneratedQuestion] = field(default_factory=dict)
    pending: list[int] = field(default_factory=list)
    issues_by_slot: dict[int, list[str]] = field(default_factory=dict)
    last_generated: list[GeneratedQuestion] = field(default_factory=list)
    rounds_done: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def needs_retry(self) -> bool:
        return bool(self.pending) and self.rounds_done < 1 + self.max_rounds


# ------------------------------------------------------------------ steps
def prepare(req: ScaffoldRequest, svc: Services) -> PipelineState:
    c = svc.cfg.input
    jd, resume = clean_text(req.jd_text), clean_text(req.resume_text)
    if len(jd) < c.min_chars_jd:
        raise InputError(f"JD text is too short (minimum {c.min_chars_jd} characters).")
    if len(resume) < c.min_chars_resume:
        raise InputError(f"Resume text is too short (minimum {c.min_chars_resume} characters).")
    if len(jd) > c.max_chars_jd:
        raise InputError(f"JD text is too long (maximum {c.max_chars_jd} characters).")
    if len(resume) > c.max_chars_resume:
        raise InputError(f"Resume text is too long (maximum {c.max_chars_resume} characters).")
    if not any(k in (req.resume_keywords or {}) for k in ("hands_on", "theoretical")):
        raise InputError("Phase 3A needs the Phase 2 keywords of this resume (hands_on / theoretical); it does not extract them again.")
    if c.mask_pii:  # all later quote checks run against the masked text the model actually saw
        jd, resume = mask_pii(jd), mask_pii(resume)
    return PipelineState(
        scaffold_id=uuid.uuid4().hex, jd_text=jd, resume_text=resume,
        candidate_label=req.candidate_label, jd_id=req.jd_id, max_rounds=svc.cfg.workflow.max_regeneration_rounds,
        warnings=list(req.notes), jd_skills=req.jd_skills, resume_keywords=req.resume_keywords,
        resume_layout=req.resume_layout,
    )


def step_load_evidence(state: PipelineState, svc: Services) -> PipelineState:
    """No LLM: the JD skills come from Phase 1 and the resume evidence from Phase 2 (already extracted)."""
    state.extraction = build_extraction(state.jd_skills, state.resume_keywords, state.resume_layout, svc.taxonomy)
    if not state.extraction.jd_skills:
        raise PipelineError("Phase 1 found no technical JD skills to build interview questions from.")
    return state


def step_verify_and_classify(state: PipelineState, svc: Services) -> PipelineState:
    assert state.extraction is not None
    state.verified = verify_extraction(
        state.extraction, state.jd_text, state.resume_text, svc.taxonomy, svc.cfg.input.max_skills,
        text_search=False,
    )
    state.warnings.extend(state.verified.warnings)
    if not state.verified.skills:
        raise PipelineError("No JD skills could be verified against the JD text.")
    state.profiles = classify_skills(state.verified, svc.cfg)
    return state


def step_plan(state: PipelineState, svc: Services) -> PipelineState:
    state.slots, plan_warnings = build_full_plan(state.profiles, svc.cfg)
    state.warnings.extend(plan_warnings)
    if not state.slots:
        raise PipelineError("No questions could be planned from the verified skills.")
    state.pending = [s.slot_id for s in state.slots]
    return state


async def step_generate(state: PipelineState, svc: Services) -> PipelineState:
    """One LLM call per section (basic / resume_based / scenario_case / coding): keeps each answer short enough
    for the output limit and lets the model focus on one kind of question at a time."""
    pending = set(state.pending)
    generated: list[GeneratedQuestion] = []
    for section in SECTION_ORDER:
        todo = [s for s in state.slots if s.slot_id in pending and s.section == section]
        if not todo:
            continue
        issues = {sid: p for sid, p in state.issues_by_slot.items() if sid in {s.slot_id for s in todo}}
        result = await svc.llm.generate(
            GeneratedQuestionSet, role="generate", instructions=generation_instructions(),
            prompt=build_generation_prompt(todo, state.jd_text, state.resume_text, issues or None),
        )
        generated.extend(result.questions)
    state.last_generated = generated
    state.rounds_done += 1
    return state


def step_validate(state: PipelineState, svc: Services) -> PipelineState:
    slots_by_id = {s.slot_id: s for s in state.slots}
    newly, issues, warns = validate_batch(
        state.last_generated, slots_by_id, state.pending, state.accepted, state.jd_text, state.resume_text
    )
    state.accepted.update(newly)
    state.pending = sorted(set(state.pending) - set(newly))
    state.issues_by_slot = {sid: issues.get(sid, ["not generated"]) for sid in state.pending}
    state.warnings.extend(warns)
    return state


def step_finalize(state: PipelineState, svc: Services) -> InterviewScaffold:
    slots_by_id = {s.slot_id: s for s in state.slots}
    questions: list[InterviewQuestion] = []
    for sid in sorted(state.accepted):
        slot, g = slots_by_id[sid], state.accepted[sid]
        questions.append(
            InterviewQuestion(
                question_id=sid, skill_id=slot.skill_id, skill_name=slot.skill_name,
                importance=slot.importance, bucket=slot.bucket, experience_level=slot.experience_level,
                level_basis=slot.level_basis, years_on_skill=slot.years_on_skill, section=slot.section,
                question_type=slot.question_type, difficulty=slot.difficulty, question=g.question.strip(),
                answer_keywords=[k.strip() for k in g.answer_keywords if k.strip()],
            )
        )

    # every must-have gap needs at least one accepted question
    covered = {slots_by_id[sid].skill_id for sid in state.accepted if slots_by_id[sid].section == Section.resume_based}
    for p in state.profiles:
        wanted = any(s.skill_id == p.skill_id and s.section == Section.resume_based for s in state.slots)
        if wanted and p.bucket == SkillBucket.gap and p.importance == Importance.must_have and p.skill_id not in covered:
            state.warnings.append(f"Must-have gap '{p.name}' has no valid question; needs manual attention.")

    issues = [
        f"slot {sid} ({slots_by_id[sid].section.value} / {slots_by_id[sid].skill_name}): " + "; ".join(probs)
        for sid, probs in sorted(state.issues_by_slot.items())
        if sid in state.pending
    ]
    return InterviewScaffold(
        scaffold_id=state.scaffold_id, candidate_label=state.candidate_label, jd_id=state.jd_id,
        candidate_overall_years=state.verified.overall_years if state.verified else None,
        status=ScaffoldStatus.partial if state.pending else ScaffoldStatus.complete,
        disclaimer=svc.cfg.review.disclaimer, skill_profiles=state.profiles, questions=questions,
        unfilled_slots=list(state.pending), warnings=state.warnings, validation_issues=issues,
    )


def step_save(scaffold: InterviewScaffold, svc: Services) -> InterviewScaffold:
    svc.store.save(scaffold)
    return scaffold


# ------------------------------------------------------------------ plain engine
async def run_plain(req: ScaffoldRequest, svc: Services) -> InterviewScaffold:
    state = prepare(req, svc)
    state = step_load_evidence(state, svc)
    state = step_verify_and_classify(state, svc)
    state = step_plan(state, svc)
    while True:
        state = await step_generate(state, svc)
        state = step_validate(state, svc)
        if not state.needs_retry:
            break
    return step_save(step_finalize(state, svc), svc)
