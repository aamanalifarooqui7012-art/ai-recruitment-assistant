"""Phase 3B steps. The LLM only rates skills and writes the recommendation; code verifies every quote against the
real candidate lines, decides which skills are 'not covered', and merges chunks. (Logic unchanged from the standalone app.)"""
import json
import time

from .errors import EvalError
from .groq_client import MODEL, groq_json
from .transcript import chunk_turns, norm

RANK = {"Not covered": 0, "Weak": 1, "Adequate": 2, "Strong": 3}
FITS = ["Strong fit", "Fit", "Weak fit", "Not a fit"]
CONF = ["High", "Medium", "Low"]
MAX_SKILLS = 40
MIN_CANDIDATE_CHARS = 200

CHUNK_SYS = """You are an impartial interview evaluator. You see only a transcript excerpt and a job description. Reply with JSON only.
Rate each listed skill from the CANDIDATE's answers in this excerpt: "Strong", "Adequate", "Weak" or "Not covered".
Rules: base ratings only on the excerpt; never guess; for any rating other than "Not covered", give 1-3 quotes copied EXACTLY, word for word, from CANDIDATE lines (no timestamps, no speaker labels, no editing, max 40 words each; escape any double quote inside a quote as \\").
If you cannot quote it, use "Not covered".
Also list interview-quality problems by the INTERVIEWER in this excerpt (leading questions, interrupting, vague questions), each with a short quote.
Schema: {"skills":[{"skill":"<exact name from list>","rating":"...","quotes":["..."]}],"quality_notes":["..."]}"""

RECO_SYS = """You are an impartial hiring evaluator. Use ONLY the skill evidence provided. Never invent evidence. Skills in "not_covered" were not tested: do not treat them as weaknesses, but lower your confidence if they matter for the role. Reply with JSON only:
{"recommendation":"Strong fit|Fit|Weak fit|Not a fit","confidence":"High|Medium|Low","reasons":["..."],"interview_quality":["..."],"skills_not_probed":["..."]}"""


def skills_from_jd(jd: dict) -> list:
    """The skills Phase 1 found, standard names, must-have first, no duplicates. Nothing is typed in again."""
    skills = [s for s in (jd or {}).get("skills", []) if str(s.get("standard_name", "")).strip()]
    skills.sort(key=lambda s: 0 if s.get("importance") == "must_have" else 1)    # stable: keeps Phase 1 order inside each group
    seen, out = set(), []
    for s in skills:
        name = str(s["standard_name"]).strip()
        if name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def check_inputs(turns, roles, skills):
    if not skills:
        raise EvalError("The Phase 1 JD has no skills to evaluate against.")
    if len(skills) > MAX_SKILLS:
        raise EvalError(f"The JD has {len(skills)} skills; Phase 3B allows at most {MAX_SKILLS}.")
    if "Candidate" not in roles.values():
        raise EvalError("At least one speaker must be the Candidate.")
    tagged = [{**t, "role": roles[t["speaker"]]} for t in turns]
    cand_chars = sum(len(t["text"]) for t in tagged if t["role"] == "Candidate")
    if cand_chars < MIN_CANDIDATE_CHARS:
        raise EvalError(f"The candidate's answers are too short ({cand_chars} characters). Are the roles swapped?")
    return tagged


def _fmt(t):
    return f"[{t['ts'] or '-'}] {t['role']}: {t['text']}"


def analyse_chunk(chunk, skills, jd, idx, total, log):
    user = (f"JOB DESCRIPTION:\n{jd[:6000] or '(not provided)'}\n\nSKILLS:\n"
            + "\n".join("- " + s for s in skills)
            + f"\n\nTRANSCRIPT EXCERPT ({idx}/{total}):\n" + "\n".join(_fmt(t) for t in chunk))
    cand = norm(" ".join(t["text"] for t in chunk if t["role"] == "Candidate"))
    by_lower = {s.lower(): s for s in skills}

    def validate(j):
        if not isinstance(j.get("skills"), list):
            raise ValueError("skills array missing")
        out = {}
        for it in j["skills"]:
            if not isinstance(it, dict):
                continue
            sk = by_lower.get(str(it.get("skill", "")).strip().lower())
            if not sk:
                continue
            rating = it.get("rating") if it.get("rating") in RANK else "Not covered"
            quotes, dropped = [], 0
            qs = it.get("quotes") if isinstance(it.get("quotes"), list) else []
            for q in qs:
                nq = norm(q)
                if len(nq) >= 8 and nq in cand:            # quote verification (code, not LLM)
                    quotes.append(str(q).strip())
                else:
                    dropped += 1
            if rating != "Not covered" and not quotes:
                rating, dropped = "Not covered", max(dropped, 1)
            out[sk] = {"rating": rating, "quotes": quotes, "dropped": dropped}
        notes = [str(x) for x in (j.get("quality_notes") if isinstance(j.get("quality_notes"), list) else []) if x]
        return {"skills": out, "notes": notes[:8]}

    return groq_json([{"role": "system", "content": CHUNK_SYS}, {"role": "user", "content": user}], validate, log)


def merge_chunks(results, skills):
    merged = {s: {"rating": "Not covered", "quotes": [], "dropped": 0} for s in skills}
    notes = []
    for r in results:
        for sk in skills:
            x = r["skills"].get(sk)
            if not x:
                continue
            if RANK[x["rating"]] > RANK[merged[sk]["rating"]]:
                merged[sk]["rating"] = x["rating"]
            merged[sk]["quotes"].extend(x["quotes"])
            merged[sk]["dropped"] += x["dropped"]
        notes.extend(r["notes"])
    covered = {s for s in skills if merged[s]["rating"] != "Not covered"}
    not_covered = [s for s in skills if s not in covered]      # set difference, in code
    return merged, not_covered, list(dict.fromkeys(notes))[:12]


def recommend(skills, merged, not_covered, notes, jd, log):
    ev = [{"skill": s, "rating": merged[s]["rating"], "quotes": merged[s]["quotes"][:3]} for s in skills]
    user = (f"JD:\n{jd[:4000] or '(not provided)'}\n\nSKILL EVIDENCE:\n{json.dumps(ev)}\n\n"
            f"not_covered: {json.dumps(not_covered)}\n\nINTERVIEW QUALITY OBSERVATIONS:\n{json.dumps(notes)}")

    def validate(j):
        if j.get("recommendation") not in FITS:
            raise ValueError("invalid recommendation")
        arr = lambda v: [str(x) for x in v] if isinstance(v, list) else []
        return {"recommendation": j["recommendation"],
                "confidence": j.get("confidence") if j.get("confidence") in CONF else "Low",
                "reasons": arr(j.get("reasons")), "interview_quality": arr(j.get("interview_quality")),
                "skills_not_probed": arr(j.get("skills_not_probed"))}

    return groq_json([{"role": "system", "content": RECO_SYS}, {"role": "user", "content": user}], validate, log)


def build_report(skills, merged, not_covered, reco, notes, **extra):
    return {"model": MODEL, "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "skills": merged,
            "not_covered": not_covered, "recommendation": reco, "quality_notes": notes, "manager": None,
            "skills_evaluated": list(skills), **extra}


def evaluate(turns, roles, skills, jd, log=print):
    """Plain (no MAF) run of every step. Used by tests and scripts; the app runs the same steps through src/maf/evaluation.py."""
    tagged = check_inputs(turns, roles, skills)
    chunks = chunk_turns(tagged)
    results = [analyse_chunk(ch, skills, jd, i, len(chunks), log) for i, ch in enumerate(chunks, 1)]
    merged, not_covered, notes = merge_chunks(results, skills)
    reco = recommend(skills, merged, not_covered, notes, jd, log)
    return build_report(skills, merged, not_covered, reco, notes)
