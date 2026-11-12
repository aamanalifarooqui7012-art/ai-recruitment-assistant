"""
merge_score.py - Phase 2.5: joins Phase 1 (JD json) and Phase 2 (resume keywords json).
Reads ONLY the two json files. Does not import or modify any Phase 1 / Phase 2 code.

Scoring (weighted, evidence-based)
  weight  of a JD skill : must_have = 3, nice_to_have = 1
  credit  of the proof  : hands_on = 1.0, theoretical = 0.4, not found = 0
                          (a skill in both lists gets the HIGHEST credit only, never the sum)
  score = 100 * sum(weight * credit) / sum(all weights)      -> denominator comes from the JD only
  must-have coverage    = sum(3 * credit) / sum(3) * 100     -> shown separately, never an auto-reject

Matching is deterministic and strict: same canonical id, or exactly the same text. Related is NOT same
(PostgreSQL != SQL, LangChain != LangGraph). Loose text overlaps are listed as "possible matches" for the
recruiter and are NOT scored.

Defaults for weights/credits are read from config/config.yaml (section `scoring`) (hard-coded fallback if the file is missing).

Usage (from the repo root)
  python -m scoring.merge_score <jd.json> <resume.keywords.json | folder> [--out DIR]
        [--must-weight 3] [--nice-weight 1] [--hands-on-credit 1.0] [--theory-credit 0.4]
  (needs src on the path:  set PYTHONPATH=src   or   pip install -e .)
"""
import argparse
import glob
import json
import math
import os
import re
from pathlib import Path

from project_config import CONFIG_PATH
_FALLBACK_WEIGHTS = {"must_have": 3.0, "nice_to_have": 1.0}
_FALLBACK_CREDITS = {"hands_on": 1.0, "theoretical": 0.4}


def load_scoring_config(path=None):
    """(weights, credits) from the `scoring` section of config/config.yaml. Anything missing or invalid falls back to the plan's defaults."""
    data = {}
    try:
        import yaml
        data = yaml.safe_load(Path(path or CONFIG_PATH).read_text(encoding="utf-8")) or {}
        data = data.get("scoring", data)             # full project config, or a file with just weights / credits
    except Exception:
        pass

    def pick(fallback, given):
        out = dict(fallback)
        for k in fallback:
            try:
                out[k] = float((given or {})[k])
            except (KeyError, TypeError, ValueError):
                pass
        return out

    return pick(_FALLBACK_WEIGHTS, data.get("weights")), pick(_FALLBACK_CREDITS, data.get("credits"))


DEFAULT_WEIGHTS, DEFAULT_CREDITS = load_scoring_config()
LEVELS = ("hands_on", "theoretical")          # checked in this order; the highest credit wins
NO_EVIDENCE = "No evidence found in resume"


# ----------------------------------------------------------------------------- text helpers
def norm(s):
    return re.sub(r"[^a-z0-9+#]+", " ", (s or "").lower()).strip()


def split_composite(raw):
    """'PySpark, Ray, or Dask' / 'TensorFlow/Keras' -> parts. Keeps 'CI/CD' and 'Weights & Biases' whole."""
    t = raw.replace("CI/CD", "CI_CD").replace("Weights & Biases", "Weights_and_Biases")
    t = t.split(":")[-1] if ":" in t else t
    parts = re.split(r"[,/]|\bor\b|\band\b", t)
    parts = [p.replace("CI_CD", "CI/CD").replace("Weights_and_Biases", "Weights & Biases").strip() for p in parts]
    return [p for p in parts if p]


def has_phrase(big, small):
    return len(small) >= 3 and re.search(r"(?<![a-z0-9])" + re.escape(small) + r"(?![a-z0-9])", big) is not None


# ----------------------------------------------------------------------------- resume index
def build_index(kw):
    """{level: {"id": {skill_id: item}, "name": {norm name: item}, "unmapped": {norm keyword: keyword}}}"""
    idx = {lv: {"id": {}, "name": {}, "unmapped": {}} for lv in LEVELS}
    for lv in LEVELS:
        for it in kw.get(lv, []):
            sid = it.get("skill_id")
            if sid and sid != "Unmapped":
                idx[lv]["id"].setdefault(sid, it)
            idx[lv]["name"].setdefault(norm(it.get("name")), it)
    for u in kw.get("unmapped", []):                       # keywords the resume has but the taxonomy lacks
        lv = "theoretical" if u.get("column") in ("theoretical", "theory") else "hands_on"
        idx[lv]["unmapped"].setdefault(norm(u.get("keyword")), u.get("keyword"))
    return idx


def evidence_of(item):
    ev = (item or {}).get("evidence") or []
    if not ev:
        return "", "", 0
    return ev[0].get("quote", ""), ev[0].get("section", ""), len(ev)


def find_in_level(jd_skill, lv_idx):
    """Strict match inside ONE column. Returns dict or None."""
    sid = jd_skill.get("standard_id")
    if sid and sid != "Unmapped" and sid in lv_idx["id"]:
        return {"item": lv_idx["id"][sid], "method": "id"}
    raw = jd_skill.get("raw_text", "")
    parts = split_composite(raw) if re.search(r"[,/:]|\bor\b|\band\b", raw) else []
    for text, method in [(raw, "text")] + [(p, "part") for p in parts]:
        n = norm(text)
        if n in lv_idx["name"]:
            return {"item": lv_idx["name"][n], "method": method}
        if n in lv_idx["unmapped"]:
            return {"item": {"name": lv_idx["unmapped"][n], "evidence": []}, "method": method + "(not in taxonomy)"}
    return None


def possible_matches(jd_skill, kw):
    """Loose overlaps, shown to the recruiter but NEVER scored."""
    raw = jd_skill.get("raw_text", "")
    cands = [norm(raw)] + [norm(p) for p in split_composite(raw)]
    out = []
    for lv in LEVELS:
        for it in kw.get(lv, []):
            rn = norm(it.get("name"))
            if any(has_phrase(c, rn) or has_phrase(rn, c) for c in cands if c) and it["name"] not in out:
                out.append(it["name"])
    return out


# ----------------------------------------------------------------------------- JD skills
def jd_key(s):
    sid = s.get("standard_id")
    return sid if sid and sid != "Unmapped" else "text:" + norm(s.get("raw_text"))


def prepare_jd_skills(jd, weights):
    """One entry per canonical skill. A skill listed twice keeps its higher weight. Unknown importance is ignored."""
    seen, ignored = {}, []
    for s in jd.get("skills", []):
        w = weights.get(s.get("importance"))
        if w is None:
            ignored.append(s.get("raw_text"))
            continue
        k = jd_key(s)
        if k not in seen or w > seen[k]["weight"]:
            seen[k] = {**s, "weight": float(w)}
    rows = sorted(seen.values(), key=lambda s: (-s["weight"], norm(s.get("raw_text"))))   # order-independent
    return rows, ignored


# ----------------------------------------------------------------------------- scoring
def score_resume(jd, kw, weights=None, credits=None):
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    credits = {**DEFAULT_CREDITS, **(credits or {})}
    idx = build_index(kw)
    jd_skills, ignored = prepare_jd_skills(jd, weights)

    rows = []
    for s in jd_skills:
        best = None                                         # highest credit across both columns
        for lv in LEVELS:
            hit = find_in_level(s, idx[lv])
            if hit and (best is None or credits[lv] > credits[best["level"]]):
                best = {**hit, "level": lv}
        row = {"skill": s["raw_text"], "standard_id": s.get("standard_id"), "importance": s["importance"],
               "weight": s["weight"]}
        if best:
            quote, section, n_ev = evidence_of(best["item"])
            row.update(evidence_level=best["level"], credit=credits[best["level"]],
                       resume_skill=best["item"].get("name"), match=best["method"],
                       evidence=quote or "(keyword found by Phase 2, no quote stored)",
                       evidence_section=section, evidence_count=n_ev,
                       also_in=[lv for lv in LEVELS if lv != best["level"] and find_in_level(s, idx[lv])])
        else:
            row.update(evidence_level="none", credit=0.0, resume_skill=None, match=None,
                       evidence=NO_EVIDENCE, evidence_section="", evidence_count=0, also_in=[],
                       possible_matches=possible_matches(s, kw))
        row["points"] = round(row["weight"] * row["credit"], 4)
        rows.append(row)

    def part(imp):
        r = [x for x in rows if x["importance"] == imp]
        mx = math.fsum(x["weight"] for x in r)
        got = math.fsum(x["weight"] * x["credit"] for x in r)
        return {"count": len(r), "matched": sum(1 for x in r if x["credit"] > 0),
                "points": round(got, 4), "max_points": mx,
                "coverage_pct": round(100 * got / mx, 2) if mx else None,
                "missing": [x["skill"] for x in r if x["credit"] == 0]}

    max_points = math.fsum(x["weight"] for x in rows)
    points = math.fsum(x["weight"] * x["credit"] for x in rows)
    warnings = []
    if not rows:
        warnings.append("The JD has no scorable skills, so the score is 0.")
    if ignored:
        warnings.append("Skills with an unknown importance were ignored: " + ", ".join(map(str, ignored)))
    unm = [x["skill"] for x in rows if x["standard_id"] in (None, "Unmapped")]
    if unm:
        warnings.append("Unmapped JD skills are matched by exact text only: " + ", ".join(unm))
    return {"jd_id": jd.get("jd_id"), "role_title": jd.get("role_title"), "resume": kw.get("resume"),
            "score": round(100 * points / max_points, 2) if max_points else 0.0,
            "points": round(points, 4), "max_points": max_points,
            "must_have": part("must_have"), "nice_to_have": part("nice_to_have"),
            "skills": rows, "warnings": warnings, "config": {"weights": weights, "credits": credits}}


def validate(jd, kw):
    if not isinstance(jd, dict) or "skills" not in jd or "jd_id" not in jd:
        raise ValueError("Not a Phase 1 json (needs `jd_id` and `skills`).")
    if not isinstance(kw, dict) or "hands_on" not in kw or "theoretical" not in kw:
        raise ValueError("Not a Phase 2 keywords json (needs `hands_on` and `theoretical`).")


def run_data(jd, kw, weights=None, credits=None):
    validate(jd, kw)
    return score_resume(jd, kw, weights, credits)


def run(jd_path, kw_path, weights=None, credits=None):
    jd = json.load(open(jd_path, encoding="utf-8"))
    kw = json.load(open(kw_path, encoding="utf-8"))
    return run_data(jd, kw, weights, credits)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("jd_json")
    ap.add_argument("resume_json_or_folder")
    ap.add_argument("--out", default=None, help="folder to write <jd>__<resume>.score.json")
    ap.add_argument("--must-weight", type=float, default=DEFAULT_WEIGHTS["must_have"])
    ap.add_argument("--nice-weight", type=float, default=DEFAULT_WEIGHTS["nice_to_have"])
    ap.add_argument("--hands-on-credit", type=float, default=DEFAULT_CREDITS["hands_on"])
    ap.add_argument("--theory-credit", type=float, default=DEFAULT_CREDITS["theoretical"])
    a = ap.parse_args()
    W = {"must_have": a.must_weight, "nice_to_have": a.nice_weight}
    C = {"hands_on": a.hands_on_credit, "theoretical": a.theory_credit}
    paths = (glob.glob(os.path.join(a.resume_json_or_folder, "*.keywords.json"))
             if os.path.isdir(a.resume_json_or_folder) else [a.resume_json_or_folder])
    for p in sorted(paths):
        r = run(a.jd_json, p, W, C)
        mh = r["must_have"]
        cov = "n/a" if mh["coverage_pct"] is None else f"{mh['coverage_pct']}%"
        print(f"{r['resume']:<45} SCORE {r['score']:>6}/100  must-have coverage {cov:>7} "
              f"({mh['matched']}/{mh['count']})  missing must-have: {len(mh['missing'])}")
        if a.out:
            os.makedirs(a.out, exist_ok=True)
            name = f"{r['jd_id']}__{os.path.basename(p).replace('.keywords.json', '')}.score.json"
            json.dump(r, open(os.path.join(a.out, name), "w", encoding="utf-8"), indent=2, ensure_ascii=False)
