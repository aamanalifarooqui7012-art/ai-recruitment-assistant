"""Skill names -> one id. Reads the SAME taxonomy file Phase 1 and Phase 2 use (config/skills_taxonomy.yaml),
so a skill has the same id in the HR questions, the resume score and the interview questions.

(Phase 3A started with a small stand-in alias table; this replaces it, as that file promised.)

Words that are also plain English (go, rust, spring, express ...) are listed in config/config.yaml (section phase2) under
`keywords.ambiguous_words`. They are NOT used as search aliases for the "is this skill mentioned in the resume?"
text search, so a sentence like "express interest" can never make a missing skill look present.
"""
import re
from pathlib import Path

import yaml

from .config import DEFAULT_RESUME_CONFIG_PATH, DEFAULT_TAXONOMY_PATH


def alias_key(text: str) -> str:
    return re.sub(r"[^a-z0-9+#]", "", text.lower())


def slugify(text: str) -> str:
    s = re.sub(r"\s+", "-", text.strip().lower())
    return re.sub(r"[^a-z0-9+#.-]", "", s) or "unknown"


class SkillTaxonomy:
    def __init__(self, aliases: dict[str, list[str]] | None = None, ambiguous: set[str] | None = None):
        self._map: dict[str, str] = {}
        self._aliases: dict[str, list[str]] = {}
        self.ambiguous = {alias_key(a) for a in (ambiguous or set())}
        self.warnings: list[str] = []
        for canonical, alts in (aliases or {}).items():
            self._aliases[canonical] = [canonical] + list(alts or [])
            for form in (canonical, *(alts or [])):
                key = alias_key(form)
                if not key:
                    continue
                if self._map.setdefault(key, canonical) != canonical:       # first one wins, never silently
                    self.warnings.append(f"'{form}' is used by both '{self._map[key]}' and '{canonical}'")

    @classmethod
    def from_yaml(cls, path: str | Path | None = None, resume_config: str | Path | None = None) -> "SkillTaxonomy":
        """Load config/skills_taxonomy.yaml  (`id: {name, category, aliases}`)."""
        with open(path or DEFAULT_TAXONOMY_PATH, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        aliases = {str(sid): [str(v["name"]), *map(str, v.get("aliases") or [])] for sid, v in raw.items()}
        ambiguous: set[str] = set()
        try:
            with open(resume_config or DEFAULT_RESUME_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                data = data.get("phase2", data)            # full project config, or a file with just the phase2 settings
                ambiguous = set(map(str, data.get("keywords", {}).get("ambiguous_words", [])))
        except OSError:
            pass
        return cls(aliases, ambiguous)

    def aliases_for(self, canonical: str) -> list[str]:
        """Spellings used to look for the skill in the resume text (plain-English words are left out)."""
        forms = self._aliases.get(canonical, [canonical])
        return [forms[0], *(f for f in forms[1:] if alias_key(f) not in self.ambiguous)]

    def canonical(self, name: str) -> tuple[str, bool]:
        """Return (skill_id, was_mapped). Unknown skills keep a clean slug."""
        key = alias_key(name)
        if key in self._map:
            return self._map[key], True
        return slugify(name), False
