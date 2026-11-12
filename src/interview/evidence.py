"""Code step: check every quote the LLM gave against the real texts.
Anything that cannot be verified is dropped, so later steps only see facts."""
from dataclasses import dataclass, field

from .schemas import ExtractionResult, Importance, JDSkill, ProjectUse, SkillEvidence
from .taxonomy import SkillTaxonomy
import re

from .textutil import find_mention, quote_in_text, snap_quote

MAX_YEARS_ON_SKILL = 40.0
MAX_OVERALL_YEARS = 50.0


def resolve_quote(quote: str | None, source: str) -> str | None:
    """The model's quote if it is really in the source; else the real source line it was
    most likely taken from (snap); else None. Result is always genuine source text."""
    if not quote:
        return None
    if quote_in_text(quote, source):
        return quote
    snapped = snap_quote(quote, source)
    if snapped and quote_in_text(snapped, source):
        return snapped
    return None


def _search_terms(skill: JDSkill, taxonomy: SkillTaxonomy) -> list[str]:
    terms = {skill.skill_id, skill.name, re.sub(r"\(.*?\)", "", skill.name).strip()}
    terms.update(re.findall(r"\(([^)]+)\)", skill.name))      # "Supervised Fine-Tuning (SFT)" -> "SFT"
    terms.update(taxonomy.aliases_for(skill.skill_id))
    return [t.strip() for t in terms if t and len(t.strip()) >= 3]


@dataclass
class VerifiedExtraction:
    skills: list[JDSkill] = field(default_factory=list)
    evidence: dict[str, SkillEvidence] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    overall_years: float | None = None


def verify_extraction(
    extraction: ExtractionResult,
    jd_text: str,
    resume_text: str,
    taxonomy: SkillTaxonomy,
    max_skills: int | None = None,
    text_search: bool = True,
) -> VerifiedExtraction:
    out = VerifiedExtraction()

    # ---- overall experience: needs a real supporting quote unless it is 0 (a fresher)
    oe = extraction.overall_experience
    if oe.years is not None:
        if not (0 <= oe.years <= MAX_OVERALL_YEARS):
            out.warnings.append(f"Ignored implausible overall experience ({oe.years} years).")
        elif oe.years > 0 and resolve_quote(oe.quote, resume_text) is None:
            out.warnings.append("Ignored overall experience: its supporting quote was not found in the resume.")
        else:
            out.overall_years = float(oe.years)

    # ---- JD skills: canonical id, verified JD quote, de-duplicated
    by_id: dict[str, JDSkill] = {}
    for s in extraction.jd_skills:
        sid, _ = taxonomy.canonical(s.skill_id or s.name)
        jd_quote = resolve_quote(s.jd_quote, jd_text)
        if jd_quote is None:
            out.warnings.append(f"Dropped JD skill '{s.name}': its jd_quote was not found in the JD.")
            continue
        s = s.model_copy(update={"jd_quote": jd_quote})
        if sid in by_id:
            if s.importance == Importance.must_have:  # strongest wording wins
                by_id[sid] = by_id[sid].model_copy(update={"importance": Importance.must_have})
            continue
        by_id[sid] = s.model_copy(update={"skill_id": sid})
    skills = list(by_id.values())
    if max_skills is not None and len(skills) > max_skills:
        # if a limit is set, never cut a must-have to make room for a nice-to-have
        skills = sorted(skills, key=lambda s: s.importance != Importance.must_have)[:max_skills]
        out.warnings.append(f"JD had more than {max_skills} skills; kept {max_skills}, must-haves first.")
    out.skills = skills
    known = {s.skill_id for s in skills}

    # ---- resume evidence: keep only quotes that really exist in the resume
    for ev in extraction.evidence:
        sid, _ = taxonomy.canonical(ev.skill_id)
        if sid not in known:
            out.warnings.append(f"Ignored evidence for '{ev.skill_id}': not a verified JD skill.")
            continue

        projects: list[ProjectUse] = []
        for p in ev.project_uses:
            real = resolve_quote(p.resume_quote, resume_text)
            if real is not None:
                projects.append(p.model_copy(update={"resume_quote": real}))
            else:
                out.warnings.append(
                    f"Dropped project evidence for '{sid}' ('{p.project_name}'): quote not found in resume."
                )

        listed = resolve_quote(ev.listed_quote, resume_text)
        if ev.listed_quote and listed is None:
            out.warnings.append(f"Dropped listed-skill evidence for '{sid}': quote not found in resume.")

        years = ev.years_on_skill
        if years is not None and not (0 <= years <= MAX_YEARS_ON_SKILL):
            out.warnings.append(f"Ignored implausible years ({years}) for '{sid}'.")
            years = None
        if years is not None and out.overall_years is not None and years > out.overall_years + 0.5:
            out.warnings.append(
                f"Ignored {years:g} years on '{sid}': more than the candidate's overall {out.overall_years:g} years."
            )
            years = None

        prev = out.evidence.get(sid)
        if prev is None:
            out.evidence[sid] = SkillEvidence(
                skill_id=sid, listed_quote=listed, years_on_skill=years,
                years_basis=ev.years_basis if years is not None else "unknown",
                project_uses=projects,
            )
        else:  # merge a duplicate entry for the same skill
            seen = {p.resume_quote for p in prev.project_uses}
            prev.project_uses.extend(p for p in projects if p.resume_quote not in seen)
            prev.listed_quote = prev.listed_quote or listed
            if prev.years_on_skill is None and years is not None:
                prev.years_on_skill, prev.years_basis = years, ev.years_basis

    # ---- code-side search: if the skill name is literally in the resume, it is not a gap,
    #      even when the model gave no usable quote. (Off when the evidence comes from Phase 2: Phase 2 already
    #      scanned the resume, and a skill it did not find must stay a gap, as in the Phase 2.5 score.)
    for skill in out.skills if text_search else []:
        ev = out.evidence.get(skill.skill_id)
        if ev and (ev.project_uses or ev.listed_quote):
            continue
        line = find_mention(_search_terms(skill, taxonomy), resume_text)
        if line is None:
            continue
        if ev is None:
            ev = out.evidence[skill.skill_id] = SkillEvidence(
                skill_id=skill.skill_id, listed_quote=None, years_on_skill=None,
                years_basis="unknown", project_uses=[],
            )
        ev.listed_quote = line
        out.warnings.append(f"Note: '{skill.name}' was found in the resume by text search (no usable quote from the model).")
    return out
