"""Input checks for Phase 3A, shared by every way of starting it.

Phase 3A runs as steps of the ONE workflow in src/maf (executors phase3a_*). This module only holds the cheap checks
done before any step runs. Input is what Phase 1 and Phase 2 already produced: the Phase 1 json (JD text + skills) and,
per candidate, the Phase 2 standardised resume text, keywords and layout. Nothing is typed in or extracted again.
"""
from typing import Optional

MIN_SKILLS_NOTE = "Phase 3A needs the skills Phase 1 found, but this Phase 1 json has none."
MIN_JD_NOTE = "Phase 3A needs the JD text, but this Phase 1 json has none (an uploaded json may have it removed)."
NO_KEYWORDS_NOTE = "Phase 3A needs the Phase 2 keywords of this resume; they were not given."


def usable_jd_text(jd: dict) -> Optional[str]:
    """The JD text from a Phase 1 result, or None when it is missing or only a placeholder.
    Cheap check before any LLM call: at least one skill Phase 1 found must literally appear in the text."""
    text = (jd or {}).get("jd_text")
    if not (text and text.strip()):
        return None
    wordings = [str(s.get("raw_text", "")).strip().lower() for s in jd.get("skills", [])]
    wordings = [w for w in wordings if w]
    if wordings and not any(w in text.lower() for w in wordings):
        return None
    return text


def precheck(jd: Optional[dict], names: list) -> tuple[Optional[str], list, list]:
    """-> (jd_text, jd_skills, errors). When the JD cannot be used, every candidate gets the same error."""
    jd = jd or {}
    jd_text, jd_skills = usable_jd_text(jd), list(jd.get("skills") or [])
    if not names:
        return jd_text, jd_skills, []
    if not (jd_text and jd_text.strip()):
        return jd_text, jd_skills, [{"candidate": n, "error": MIN_JD_NOTE} for n in names]
    if not jd_skills:
        return jd_text, jd_skills, [{"candidate": n, "error": MIN_SKILLS_NOTE} for n in names]
    return jd_text, jd_skills, []
