"""Phase 3A does NOT extract anything itself. It reuses what the earlier phases already extracted:

    Phase 1  jd["skills"]            -> the JD skills (id, importance, required experience, JD quote)
    Phase 2  keywords json           -> per skill: hands_on (used in work / project text) or theoretical (only listed),
                                        each with a verbatim resume quote, section, entry and dates
    Phase 2  layout json             -> project / job names and dates, used for years and the overall experience

This module only RESHAPES that data into the `ExtractionResult` the later code steps (verify -> classify -> plan)
already understand. No model is called and nothing is searched for again, so Phase 3A can never disagree with the
Phase 2 keywords or the Phase 2.5 score about which skills a candidate has.

Years are plain date arithmetic on dates Phase 2 already parsed (overlapping periods are counted once).
Nothing is guessed: no dates -> years stay unknown and the level falls back to overall experience, then to medium.
"""
import re
from datetime import date

from resume_std.render import TITLES
from resume_std.textutil import DATE_RANGE_RE

from .schemas import (
    ExtractionResult,
    Importance,
    JDSkill,
    OverallExperience,
    ProjectUse,
    SkillEvidence,
)
from .taxonomy import SkillTaxonomy, alias_key

UNMAPPED = "unmapped"
SOFT_SKILL = "soft skill"
_EXPLICIT_YEARS = re.compile(
    r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:[A-Za-z-]+\s+){0,2}?experience", re.I)


# ------------------------------------------------------------------ dates
def _month_index(value: str | None, today: date) -> int | None:
    """'2021-03' -> months since year 0; 'present' -> this month; anything else -> None."""
    if not value:
        return None
    v = value.strip().lower()
    if v == "present":
        return today.year * 12 + today.month - 1
    m = re.fullmatch(r"((?:19|20)\d{2})-(\d{2})", v)
    return int(m.group(1)) * 12 + int(m.group(2)) - 1 if m else None


def _years_covered(periods: list[dict], today: date) -> float | None:
    """Total length of the periods in years; overlapping periods are counted once. None if no period is usable."""
    spans = []
    for p in periods:
        s, e = _month_index((p or {}).get("start"), today), _month_index((p or {}).get("end"), today)
        if s is not None and e is not None and e >= s:
            spans.append((s, e + 1))                      # inclusive of the last month
    if not spans:
        return None
    spans.sort()
    total, (cur_s, cur_e) = 0, spans[0]
    for s, e in spans[1:]:
        if s <= cur_e:
            cur_e = max(cur_e, e)
        else:
            total, cur_s, cur_e = total + (cur_e - cur_s), s, e
    total += cur_e - cur_s
    return round(total / 12, 1)


# ------------------------------------------------------------------ layout helpers
def _entry(layout: dict | None, section: str | None, index: int | None) -> dict | None:
    for sec in (layout or {}).get("sections", []):
        if sec.get("name") == section:
            entries = sec.get("entries", [])
            if isinstance(index, int) and 0 <= index < len(entries):
                return entries[index]
    return None


def _title(block: dict) -> str | None:
    """The block as a short title line (project name / '<Role>, <Company>'), or None if it is not one."""
    if block.get("is_bullet") or (block.get("is_heading") and block.get("label_source") == "heading"):
        return None
    text = DATE_RANGE_RE.sub("", block.get("text", ""))
    text = re.sub(r"[\s()|,\u2013\u2014-]+$", "", " ".join(text.split())).strip()
    return text if text and len(text) <= 100 else None


def _entry_name(layout: dict | None, ev: dict) -> str:
    """Project title / '<Role>, <Company>' line the evidence sits under, as written in the resume: the nearest title
    line above the quoted block (one Phase 2 entry can hold several projects), else the entry's first title line."""
    blocks = (_entry(layout, ev.get("section"), ev.get("entry")) or {}).get("blocks", [])
    at = next((i for i, b in enumerate(blocks) if b.get("id") == ev.get("block_id")), len(blocks) - 1)
    for b in reversed(blocks[: at + 1]):
        if (t := _title(b)):
            return t
    for b in blocks:
        if (t := _title(b)):
            return t
    section = TITLES.get(ev.get("section") or "", (ev.get("section") or "resume").title())
    return f"{section} entry {int(ev.get('entry') or 0) + 1}"


def overall_experience(layout: dict | None, today: date) -> OverallExperience:
    """Total professional years, from the Phase 2 layout only. Two sources, the larger one wins (so a skill's years,
    which come from the same dates, can never exceed the overall figure):
      - the resume states it in its summary (\"5 years of experience\")
      - the dates of the work-experience entries (overlaps counted once)
    Neither -> unknown (the level then falls back to medium)."""
    if not layout:
        return OverallExperience(years=None, basis="unknown", quote=None)
    sections = layout.get("sections", [])
    found: list[OverallExperience] = []
    for sec in sections:
        if sec.get("name") != "summary":
            continue
        for e in sec.get("entries", []):
            for b in e.get("blocks", []):
                m = _EXPLICIT_YEARS.search(b.get("text", ""))
                if m and not (b.get("is_heading") and b.get("label_source") == "heading"):
                    found.append(OverallExperience(years=float(m.group(1)), basis="explicit", quote=b["text"].strip()))
                    break
    periods, quote = [], None
    for sec in sections:
        if sec.get("name") != "experience":
            continue
        for e in sec.get("entries", []):
            if e.get("dates"):
                periods.append(e["dates"])
                if quote is None:
                    quote = next((b["text"].strip() for b in e.get("blocks", []) if DATE_RANGE_RE.search(b.get("text", ""))), None)
    years = _years_covered(periods, today)
    if years is not None and quote:
        found.append(OverallExperience(years=years, basis="inferred_from_dates", quote=quote))
    return max(found, key=lambda o: o.years or 0) if found else OverallExperience(years=None, basis="unknown", quote=None)


# ------------------------------------------------------------------ Phase 1 + Phase 2 -> ExtractionResult
def jd_skills_from_phase1(jd_skills: list[dict], taxonomy: SkillTaxonomy) -> list[JDSkill]:
    out = []
    for s in jd_skills:
        raw = str(s.get("raw_text") or s.get("standard_name") or "").strip()
        if not raw or str(s.get("category", "")).strip().lower() == SOFT_SKILL:
            continue                                       # interviews here are about technical skills
        sid = str(s.get("standard_id") or "")
        if not sid or sid.lower() == UNMAPPED:
            sid = taxonomy.canonical(raw)[0]               # Phase 1 could not map it: keep a clean slug
        out.append(JDSkill(
            skill_id=sid, name=raw, importance=Importance(s.get("importance", "nice_to_have")),
            required_experience=str(s.get("required_experience") or "Not specified"),
            jd_quote=str(s.get("evidence_quote") or raw),
        ))
    return out


def _index(items: list[dict], taxonomy: SkillTaxonomy) -> dict[str, dict]:
    """Phase 2 column -> {skill id (or the wording, for unmapped items): item}."""
    out: dict[str, dict] = {}
    for it in items:
        key = it.get("skill_id")
        out.setdefault(alias_key(it.get("name", "")) if not key or str(key).lower() == UNMAPPED else key, it)
    return out


def build_extraction(
    jd_skills: list[dict],
    keywords: dict,
    layout: dict | None,
    taxonomy: SkillTaxonomy,
    today: date | None = None,
) -> ExtractionResult:
    today = today or date.today()
    skills = jd_skills_from_phase1(jd_skills, taxonomy)
    hands = _index(keywords.get("hands_on", []), taxonomy)
    theory = _index(keywords.get("theoretical", []), taxonomy)

    evidence: list[SkillEvidence] = []
    for s in skills:
        keys = [s.skill_id, alias_key(s.name)]             # mapped skills by id; unmapped ones by their wording
        h = next((hands[k] for k in keys if k in hands), None)
        t = next((theory[k] for k in keys if k in theory), None)
        if not h and not t:
            continue                                       # Phase 2 found nothing -> the skill is a gap
        projects, seen = [], set()
        for ev in (h or {}).get("evidence", []):
            quote = (ev.get("quote") or "").strip()
            if quote and quote not in seen:
                seen.add(quote)
                projects.append(ProjectUse(project_name=_entry_name(layout, ev),
                                           what_candidate_did=quote[:200], resume_quote=quote))
        years = _years_covered([ev.get("dates") for ev in (h or {}).get("evidence", []) if ev.get("dates")], today)
        listed = next((e["quote"].strip() for e in (t or {}).get("evidence", []) if e.get("quote")), None)
        evidence.append(SkillEvidence(
            skill_id=s.skill_id, listed_quote=listed, years_on_skill=years,
            years_basis="inferred_from_dates" if years is not None else "unknown", project_uses=projects))

    return ExtractionResult(jd_skills=skills, evidence=evidence, overall_experience=overall_experience(layout, today))
