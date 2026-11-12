"""Skill taxonomy: normalise names to standard ids and find skills in free text (code only, no LLM).

Matching ignores case for normal skills, and ignores spaces . - / _ everywhere, so
"React.js", "react js", "React-JS" and "reactjs" are the same skill.
Risky skills (1-2 letter names such as C, R, Go, and common words such as Rest, Node, Cloud) only match when
written with their canonical capitalisation AND they sit in a skill list or next to a cue word like "language"."""
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .mask_pii import URL_RE

_SEP = re.compile(r"[\s.\-/_]")
TOKEN_RE = re.compile(r"[A-Za-z0-9+#]+")
GAP_OK = re.compile(r"^[\s.\-/_]*$")
CUE_WORDS = {"language", "lang", "programming", "framework", "library", "developer", "scripting", "stack"}
ROOT = Path(__file__).resolve().parents[2]      # repo root


def norm_key(s) -> str:
    return _SEP.sub("", str(s).lower())


def norm_cs(s) -> str:
    return _SEP.sub("", str(s))


@dataclass(frozen=True)
class Match:
    skill_id: str
    start: int
    end: int
    surface: str


class Taxonomy:
    def __init__(self, raw: dict, ambiguous_words=()):
        self.skills, self.lookup, self.forms_cs, self.warnings = {}, {}, {}, []
        for sid, v in raw.items():
            if not isinstance(v, dict) or not v.get("name"):
                self.warnings.append(f"{sid}: missing name")
                continue
            aliases = [a for a in (v.get("aliases") or []) if isinstance(a, str)]
            self.skills[sid] = {"skill_id": sid, "name": v["name"], "category": v.get("category", "Tool")}
            forms = [sid, v["name"], *aliases]
            self.forms_cs[sid] = {norm_cs(f) for f in forms}
            for f in forms:
                k = norm_key(f)
                if not k:
                    self.warnings.append(f"{sid}: empty form {f!r}")
                elif self.lookup.get(k, sid) != sid:
                    self.warnings.append(f"collision: {f!r} in {sid} and {self.lookup[k]}")
                else:
                    self.lookup[k] = sid
        self.ambiguous = {norm_key(w) for w in ambiguous_words}
        longest = max((len(TOKEN_RE.findall(str(f))) for sid, v in raw.items() if isinstance(v, dict)
                       for f in [v.get("name", ""), *(v.get("aliases") or [])]), default=1)
        self.max_ngram = min(max(longest, 1), 6)

    def is_risky(self, key: str) -> bool:
        return len(key) <= 2 or key in self.ambiguous

    def canonical(self, text):
        """'ReactJS' -> 'react'. None if the text is not a known skill (Unmapped)."""
        return self.lookup.get(norm_key(text))

    def info(self, sid):
        return self.skills[sid]

    def find(self, text: str):
        """All taxonomy hits, longest phrase first, no filtering of risky words."""
        text = URL_RE.sub(lambda m: " " * len(m.group(0)), text)      # skill names inside URLs are not skills
        toks = [m.span() for m in TOKEN_RE.finditer(text)]
        out, i = [], 0
        while i < len(toks):
            hit = None
            for n in range(min(self.max_ngram, len(toks) - i), 0, -1):
                j = i + n - 1
                if not all(GAP_OK.match(text[toks[k][1]:toks[k + 1][0]]) for k in range(i, j)):
                    continue
                span = text[toks[i][0]:toks[j][1]]
                sid = self.lookup.get(norm_key(span))
                if sid:
                    hit = Match(sid, toks[i][0], toks[j][1], span)
                    break
            if hit:
                out.append(hit)
                i += len(TOKEN_RE.findall(hit.surface))
            else:
                i += 1
        return out

    def scan(self, text: str):
        """find() plus the guard rules for risky words."""
        hits = self.find(text)
        safe_ids = {h.skill_id for h in hits if not self.is_risky(norm_key(h.surface))}
        keep = []
        for h in hits:
            key = norm_key(h.surface)
            if not self.is_risky(key):
                keep.append(h)
                continue
            if norm_cs(h.surface) not in self.forms_cs[h.skill_id]:
                continue                                         # wrong capitalisation, e.g. "Rest", "NODE"
            if key in self.ambiguous and h.surface.islower() and not self.skills[h.skill_id]["name"].islower():
                continue                                         # lowercase plain word, e.g. "next-day", "worker node"
            others = len(safe_ids - {h.skill_id})
            before = re.findall(r"[A-Za-z]+", text[:h.start])[-1:]
            after = re.findall(r"[A-Za-z]+", text[h.end:])[:1]
            cue = any(w.lower() in CUE_WORDS for w in before + after)
            if others >= 2 or cue:
                keep.append(h)
        return keep


_CACHE = {}


def load_taxonomy(path, ambiguous_words=()) -> Taxonomy:
    p = Path(path)
    p = p if p.is_absolute() else ROOT / p
    key = (str(p), p.stat().st_mtime, tuple(ambiguous_words))
    if key not in _CACHE:
        _CACHE[key] = Taxonomy(yaml.safe_load(p.read_text("utf-8")), ambiguous_words)
    return _CACHE[key]


def get_taxonomy(cfg) -> Taxonomy:
    k = cfg["keywords"]
    return load_taxonomy(k["taxonomy_path"], k.get("ambiguous_words", []))


if __name__ == "__main__":      # python -m resume_std.taxonomy  -> checks your taxonomy file
    from .config import load_config
    t = get_taxonomy(load_config())
    print(f"{len(t.skills)} skills, {len(t.lookup)} spellings, {len(t.warnings)} warnings")
    for w in t.warnings[:30]:
        print(" -", w)
