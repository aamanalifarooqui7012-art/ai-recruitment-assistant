"""Plain-text view of one interview scaffold (terminal output of scripts/run_workflow.py / run_interview.py)."""
from .schemas import InterviewScaffold


def _years(v: float | None) -> str:
    return "n/a" if v is None else f"{v:g}y"


def render(s: InterviewScaffold) -> str:
    out = [
        "=" * 72,
        f"[{s.disclaimer}]",
        f"Candidate: {s.candidate_label or '-'}    JD: {s.jd_id or '-'}",
        f"Status: {s.status.value}    Candidate overall experience: {_years(s.candidate_overall_years)}",
        "",
        "How each skill was classified",
        f"  {'Skill':<16}{'JD':<14}{'Resume evidence':<18}{'Skill yrs':<10}{'Level (basis)':<26}Difficulty",
    ]
    for p in s.skill_profiles:
        level = f"{p.experience_level.value} ({p.level_basis.value})"
        out.append(
            f"  {p.name[:15]:<16}{p.importance.value:<14}{p.bucket.value:<18}{_years(p.years_on_skill):<10}{level:<26}{p.difficulty.value}"
        )
    out += ["", "Questions", "-" * 72]
    last_section = None
    for q in s.questions:
        if q.section != last_section:
            last_section = q.section
            n = sum(1 for x in s.questions if x.section == q.section)
            out += [f"## SECTION: {q.section.value.replace('_', ' ').upper()} ({n} questions)", ""]
        out += [
            f"{q.question_id}. [{q.skill_name} | {q.question_type.value} | {q.difficulty.value}]",
            f"   Q: {q.question}",
            f"   Good answer mentions: {', '.join(q.answer_keywords)}",
            "",
        ]
    for title, items in (("Warnings", s.warnings), ("Validation issues", s.validation_issues)):
        if items:
            out += [title] + [f"  ! {i}" for i in items] + [""]
    return "\n".join(out)
