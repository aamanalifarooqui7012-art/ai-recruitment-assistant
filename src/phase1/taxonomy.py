"""Skill taxonomy + normalise(). Pure code, no LLM.

Matching order (first hit wins, nothing is guessed):
  1. exact wording      "React"
  2. cleaned wording    "React.js", "react js", "React-JS"
  3. filler removed     "Python programming", "Strong knowledge of SQL", "ML models"
  4. split (see normalise_many)   "Pandas/NumPy", "Matplotlib & Seaborn"
  5. strict fuzzy       typos only, flagged for review
  6. Unmapped
"""
import difflib
import json
import re

import yaml

from .schemas import Category, ExtractedSkill, Importance, MatchType, NormalisedSkill
from .settings import SETTINGS, path

UNMAPPED = "Unmapped"

# Words around a skill name that are not part of the skill ("Python programming").
_LEADING = [
    "strong", "good", "basic", "advanced", "solid", "excellent", "hands on", "working knowledge of",
    "knowledge of", "experience with", "experience in", "proficiency in", "proficient in",
    "expertise in", "expert in", "understanding of", "familiarity with", "exposure to",
]
_TRAILING = {
    "programming", "language", "framework", "frameworks", "library", "libraries", "package",
    "packages", "development", "developer", "skills", "skill", "knowledge", "experience",
    "proficiency", "expertise", "basics", "fundamentals", "concepts", "tools", "technologies",
    "technology", "platform", "platforms", "stack", "scripting", "models", "model", "algorithms", "techniques",
}
# Separators for items written together: "Pandas/NumPy", "A & B", "A, B", "A and B", "A or B"
_SPLIT = re.compile(r"\s*(?:/|,|;|&|\||\band\b|\bor\b)\s*", re.IGNORECASE)


def _clean(text: str) -> str:
    """Lowercase, drop (...) notes, keep only a-z 0-9 + # . / - and spaces."""
    t = re.sub(r"\(.*?\)", " ", text.lower())
    t = t.replace("&", " and ")
    t = re.sub(r"[^a-z0-9+#.\s/\-_]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _squash(text: str) -> str:
    """Matching key: 'React.js', 'react js', 'React-JS' -> 'reactjs'."""
    return re.sub(r"[\s.\-_/]", "", _clean(text))


def _strip_fillers(raw: str, leading: bool = True, trailing: bool = True) -> str:
    """'Strong knowledge of SQL' -> 'sql'.  'Python programming' -> 'python'."""
    words = _clean(raw).replace("-", " ").split()
    changed = True
    while changed and words:
        changed = False
        text = " ".join(words)
        if leading:
            for prefix in _LEADING:
                if text.startswith(prefix + " "):
                    words = words[len(prefix.split()):]
                    changed = True
                    break
        if not changed and trailing and len(words) > 1 and words[-1] in _TRAILING:
            words = words[:-1]
            changed = True
    return " ".join(words)


class Taxonomy:
    def __init__(self, file):
        raw = yaml.safe_load(file.read_text(encoding="utf-8"))
        self.skills: dict[str, dict] = {}
        self.exact: dict[str, str] = {}      # lowercase wording -> id
        self.squashed: dict[str, str] = {}   # squashed key -> id

        for sid, info in raw.items():
            sid = str(sid)
            self.skills[sid] = {"name": info["name"], "category": Category(info["category"])}
            for w in {sid, info["name"], *(info.get("aliases") or [])}:
                w = str(w)
                self.exact[w.lower().strip()] = sid
                key = _squash(w)
                if not key:
                    continue
                if key in self.squashed and self.squashed[key] != sid:
                    raise ValueError(
                        f"Taxonomy clash: '{w}' -> '{key}' is used by "
                        f"both '{self.squashed[key]}' and '{sid}'"
                    )
                self.squashed[key] = sid


TAXONOMY = Taxonomy(path("taxonomy"))


def _lookup(raw: str, taxonomy: Taxonomy, fuzzy: bool) -> tuple[str, MatchType]:
    exact_id = taxonomy.exact.get(raw.lower().strip())
    if exact_id:
        return exact_id, "exact"

    stripped = _strip_fillers(raw)
    # also try with only the leading words removed: "Strong analytical skills" -> "analytical skills"
    for candidate in (raw, _strip_fillers(raw, trailing=False), stripped):
        key = _squash(candidate)
        if key and key in taxonomy.squashed:
            return taxonomy.squashed[key], "cleaned"

    cfg = SETTINGS["normalise"]
    key = _squash(stripped)
    if fuzzy and cfg["fuzzy_enabled"] and len(key) >= cfg["fuzzy_min_length"]:
        pool = [k for k in taxonomy.squashed if len(k) >= cfg["fuzzy_min_length"]]
        hits = difflib.get_close_matches(key, pool, n=1, cutoff=cfg["fuzzy_cutoff"])
        if hits:
            return taxonomy.squashed[hits[0]], "fuzzy"
    return UNMAPPED, "unmapped"


def normalise(raw: str, taxonomy: Taxonomy = TAXONOMY) -> tuple[str, MatchType]:
    """One wording -> (standard_id, match_type). Never guesses: no match = 'Unmapped'."""
    return _lookup(raw, taxonomy, fuzzy=True)


def normalise_many(raw: str, taxonomy: Taxonomy = TAXONOMY) -> list[tuple[str, str, MatchType]]:
    """Like normalise(), but also handles several skills written as one item.
    Returns a list of (wording, standard_id, match_type).
      "Pandas/NumPy"          -> Pandas, NumPy
      "AI/ML models"          -> AI, ML
      "CI/CD", "UI/UX"        -> one skill (they are in the taxonomy as a whole)
      "Zzzlang/Qqqlang"       -> kept whole as one Unmapped item
    A split is accepted only if every part maps, or at least one part with 3+ letters maps
    (so "R&D" is not turned into the language R)."""
    sid, mtype = _lookup(raw, taxonomy, fuzzy=False)
    if sid != UNMAPPED:
        return [(raw, sid, mtype)]

    parts = [p.strip() for p in _SPLIT.split(raw) if p.strip()]
    if len(parts) >= 2:
        results = [(p, *normalise(p, taxonomy)) for p in parts]
        mapped = [r for r in results if r[1] != UNMAPPED]
        if mapped and (len(mapped) == len(results) or any(len(_squash(r[0])) >= 3 for r in mapped)):
            return results

    sid, mtype = normalise(raw, taxonomy)
    return [(raw, sid, mtype)]


def normalise_skills(extracted: list[ExtractedSkill]) -> list[NormalisedSkill]:
    """Map every skill to its standard name. Same skill twice -> one entry
    (must_have wins over nice_to_have; keeps the first stated experience).
    A combined item like "Pandas/NumPy" becomes separate skills."""
    merged: dict[str, NormalisedSkill] = {}

    for sk in extracted:
        for wording, sid, mtype in normalise_many(sk.raw_text):
            if sid == UNMAPPED:
                name, category, dedupe_key = UNMAPPED, sk.category, f"?{_squash(wording)}"
            else:
                name = TAXONOMY.skills[sid]["name"]
                category = TAXONOMY.skills[sid]["category"]
                dedupe_key = sid

            item = NormalisedSkill(
                raw_text=wording,
                standard_id=sid,
                standard_name=name,
                category=category,
                importance=sk.importance,
                required_experience=sk.required_experience,
                evidence_quote=sk.evidence_quote,
                match_type=mtype,
                needs_review=mtype in ("fuzzy", "unmapped"),
            )

            old = merged.get(dedupe_key)
            if old is None:
                merged[dedupe_key] = item
            elif old.importance == Importance.NICE_TO_HAVE and item.importance == Importance.MUST_HAVE:
                if item.required_experience == "Not specified":
                    item.required_experience = old.required_experience
                merged[dedupe_key] = item
            elif old.required_experience == "Not specified":
                old.required_experience = item.required_experience

    return list(merged.values())


def save_unmapped(skills: list[NormalisedSkill], jd_id: str) -> list[str]:
    """Add unmapped skill names to the review queue file. Returns the list."""
    unmapped = sorted({s.raw_text for s in skills if s.standard_id == UNMAPPED})
    if not unmapped:
        return []
    file = path("unmapped_queue")
    file.parent.mkdir(parents=True, exist_ok=True)
    queue = json.loads(file.read_text(encoding="utf-8")) if file.exists() else {}
    for raw in unmapped:
        if jd_id not in queue.setdefault(raw, []):
            queue[raw].append(jd_id)
    file.write_text(json.dumps(queue, indent=2, ensure_ascii=False), encoding="utf-8")
    return unmapped
