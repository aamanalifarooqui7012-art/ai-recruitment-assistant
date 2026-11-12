"""Code step: decide WHICH questions get written (skill, type, difficulty).
The LLM only fills in the wording, so count and mix are guaranteed by construction."""
from .config import Settings
from .schemas import (
    SECTION_ORDER, Difficulty, Importance, QuestionSlot, QuestionType, Section, SkillBucket, SkillProfile,
)

_TYPE_ORDER = [QuestionType.technical, QuestionType.scenario, QuestionType.behavioural]


def priority_rank(p: SkillProfile) -> int:
    must = p.importance == Importance.must_have
    if p.bucket == SkillBucket.gap:
        return 0 if must else 5
    if p.bucket == SkillBucket.project_hands_on:
        return 1 if must else 3
    return 2 if must else 4


def apportion(mix: dict[QuestionType, float], n: int) -> dict[QuestionType, int]:
    """Largest-remainder rounding so counts always add up to n."""
    raw = {t: mix.get(t, 0.0) * n for t in _TYPE_ORDER}
    counts = {t: int(raw[t]) for t in _TYPE_ORDER}
    leftover = n - sum(counts.values())
    by_fraction = sorted(_TYPE_ORDER, key=lambda t: (-(raw[t] - counts[t]), _TYPE_ORDER.index(t)))
    for t in by_fraction[:leftover]:
        counts[t] += 1
    return counts


def _gap_allowed(p: SkillProfile, policy: str) -> bool:
    if policy == "none":
        return False
    if policy == "all":
        return True
    return p.importance == Importance.must_have


def build_plan(profiles: list[SkillProfile], cfg: Settings) -> tuple[list[QuestionSlot], list[str]]:
    q = cfg.questions
    warnings: list[str] = []

    eligible = [p for p in profiles if p.bucket != SkillBucket.gap or _gap_allowed(p, q.gap_questions)]
    ordered = sorted(eligible, key=lambda p: (priority_rank(p), p.jd_order))
    if not ordered:
        return [], ["No skills are eligible for questions."]

    # must-have gaps are never dropped, even if that means more questions than 'total'
    mandatory = [p for p in ordered if p.bucket == SkillBucket.gap and p.importance == Importance.must_have]
    total = max(q.total, len(mandatory))
    if total > q.total:
        warnings.append(
            f"{len(mandatory)} must-have gaps exceed the requested {q.total} questions; "
            f"plan was raised to {total} so every must-have gap is covered."
        )

    # pass 1: one question per skill by priority; pass 2+: extra questions on non-gap skills
    chosen: list[tuple[SkillProfile, int]] = [(p, 0) for p in ordered[:total]]
    non_gap = [p for p in ordered if p.bucket != SkillBucket.gap]
    for occurrence in range(1, q.max_per_skill):
        for p in non_gap:
            if len(chosen) >= total:
                break
            chosen.append((p, occurrence))
    if len(chosen) < total:
        warnings.append(
            f"Only {len(chosen)} distinct questions are possible with {len(eligible)} eligible skills "
            f"(max_per_skill={q.max_per_skill}); requested {total}."
        )

    types = _assign_types(chosen, apportion(q.mix, len(chosen)), warnings)

    # display order: by priority, then JD order, then occurrence
    idx_sorted = sorted(
        range(len(chosen)),
        key=lambda i: (priority_rank(chosen[i][0]), chosen[i][0].jd_order, chosen[i][1]),
    )
    slots: list[QuestionSlot] = []
    for slot_id, i in enumerate(idx_sorted, start=1):
        p = chosen[i][0]
        slots.append(
            QuestionSlot(
                slot_id=slot_id, skill_id=p.skill_id, skill_name=p.name, importance=p.importance,
                section=Section.resume_based, bucket=p.bucket, experience_level=p.experience_level, level_basis=p.level_basis,
                years_on_skill=p.years_on_skill,
                difficulty=p.difficulty, question_type=types[i], jd_quote=p.jd_quote,
                required_experience=p.required_experience, projects=p.projects,
                listed_quote=p.listed_quote,
            )
        )
    return slots, warnings


def _assign_types(
    chosen: list[tuple[SkillProfile, int]],
    counts: dict[QuestionType, int],
    warnings: list[str],
) -> dict[int, QuestionType]:
    """Gap -> technical only. Behavioural -> project_hands_on only (needs a real project to talk about)."""
    assigned: dict[int, QuestionType] = {}
    quota = dict(counts)

    gap_idx = [i for i, (p, _) in enumerate(chosen) if p.bucket == SkillBucket.gap]
    rest_idx = [i for i in range(len(chosen)) if i not in set(gap_idx)]
    for i in gap_idx:
        assigned[i] = QuestionType.technical
        quota[QuestionType.technical] = max(0, quota[QuestionType.technical] - 1)

    while sum(quota.values()) > len(rest_idx):  # trim so quotas fit the remaining slots
        for t in _TYPE_ORDER:
            if quota[t] > 0:
                quota[t] -= 1
                break

    hands = [i for i in rest_idx if chosen[i][0].bucket == SkillBucket.project_hands_on]
    want_beh = quota[QuestionType.behavioural]
    beh = hands[-want_beh:] if want_beh > 0 else []
    if len(beh) < want_beh:
        quota[QuestionType.scenario] += want_beh - len(beh)
        warnings.append(
            "Not enough project-backed skills for the requested behavioural questions; "
            "the rest were turned into scenario questions."
        )
    for i in beh:
        assigned[i] = QuestionType.behavioural

    remaining = [i for i in rest_idx if i not in set(beh)]
    tech_n = quota[QuestionType.technical]
    for i in remaining[:tech_n]:
        assigned[i] = QuestionType.technical
    for i in remaining[tech_n:]:
        assigned[i] = QuestionType.scenario
    return assigned


# ---------------------------------------------------------------- sections without project questions (basic / scenario_case / coding)
_SECTION_TYPE = {
    Section.basic: QuestionType.technical,
    Section.scenario_case: QuestionType.scenario,
    Section.coding: QuestionType.technical,
}
_ALWAYS_EASY = {Section.basic, Section.coding}


def _slot_order(profiles: list[SkillProfile], section: Section) -> list[SkillProfile]:
    """Which skills a JD-driven section draws from, in order.
    basic: skills the RESUME shows (project_hands_on first, then listed_only) so the basic questions are about what the
    candidate claims to know; skills with no resume evidence are used only when the resume shows none at all.
    other sections: every JD skill, must-have first, then JD order."""
    if section == Section.basic:
        claimed = [p for p in profiles if p.bucket != SkillBucket.gap]
        if claimed:
            rank = {SkillBucket.project_hands_on: 0, SkillBucket.listed_only: 1}
            return sorted(claimed, key=lambda p: (p.importance != Importance.must_have, rank[p.bucket], p.jd_order))
    return sorted(profiles, key=lambda p: (p.importance != Importance.must_have, p.jd_order))


def build_jd_section_slots(profiles: list[SkillProfile], section: Section, n: int) -> list[QuestionSlot]:
    """n slots for a section that does not use the candidate's project details. See _slot_order for which skills are
    used; when there are fewer skills than n the list is cycled (the model is told to cover a different concept each
    time). No project text or resume quote goes into these slots, so the questions never depend on the candidate's projects.
    basic and coding are always easy."""
    if n <= 0 or not profiles:
        return []
    ordered = _slot_order(profiles, section)
    slots: list[QuestionSlot] = []
    for i in range(n):
        p = ordered[i % len(ordered)]
        slots.append(
            QuestionSlot(
                slot_id=0,  # numbered later by build_full_plan
                skill_id=p.skill_id, skill_name=p.name, importance=p.importance, section=section,
                bucket=p.bucket, experience_level=p.experience_level, level_basis=p.level_basis,
                years_on_skill=p.years_on_skill,
                difficulty=Difficulty.easy if section in _ALWAYS_EASY else p.difficulty,
                question_type=_SECTION_TYPE[section], jd_quote=p.jd_quote,
                required_experience=p.required_experience, projects=[], listed_quote=None,
            )
        )
    return slots


def build_full_plan(profiles: list[SkillProfile], cfg: Settings) -> tuple[list[QuestionSlot], list[str]]:
    """The whole paper: basic -> resume_based -> scenario_case -> coding. slot_ids run 1..N in that order."""
    q = cfg.questions
    resume_slots, warnings = build_plan(profiles, cfg)
    by_section: dict[Section, list[QuestionSlot]] = {Section.resume_based: resume_slots}
    for section in SECTION_ORDER:
        if section != Section.resume_based:
            by_section[section] = build_jd_section_slots(profiles, section, q.extra_sections.get(section, 0))

    ordered: list[QuestionSlot] = []
    for section in SECTION_ORDER:
        ordered.extend(by_section[section])
    slots = [sl.model_copy(update={"slot_id": i}) for i, sl in enumerate(ordered, start=1)]
    return slots, warnings
