"""Phase 1 + Phase 2 + Phase 2.5 + HR + Phase 3A + Phase 3B in ONE app, driven by the Microsoft Agent Framework workflows.
Run (from the repo root):  python -m streamlit run ui/phase_app.py

  Phase 1   JD text            -> skills (must_have / nice_to_have) + HR screening questions
  Phase 2   resume files       -> hands_on / theoretical keywords, each with evidence
  Phase 2.5 weighted score     -> must-have 3 / good-to-have 1  x  hands-on 1.0 / theoretical 0.4  -> /100
  HR step   screening answers  -> marked per candidate (Met / Partly met / Not met)
  Phase 3A  interview questions-> written per candidate from the SAME JD and resume (kept apart from the HR questions)
  Phase 3B  interview evaluation-> interview transcript rated against the SAME Phase 1 JD skills (no JD or skills typed again)

You may also upload a ready-made Phase 1 json and/or Phase 2 keywords json: that skips the LLM for that step.
"""
import json
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # repo/src on the import path

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from evaluation import steps as eval_steps  # noqa: E402
from evaluation.errors import EvalError  # noqa: E402
from evaluation.transcript import default_roles, parse_transcript, read_upload, unique_speakers  # noqa: E402
from interview.runner import usable_jd_text  # noqa: E402
from maf import EvaluationRequest, PipelineRequest, run_evaluation, run_interview, run_pipeline  # noqa: E402
from scoring.hr_screening import HR_POINTS, MARK_CHOICES, NOT_MARKED, hr_score  # noqa: E402
from scoring.merge_score import DEFAULT_CREDITS, DEFAULT_WEIGHTS, run_data  # noqa: E402

st.set_page_config(page_title="AI Recruitment Assistant", layout="wide")

with st.sidebar:
    st.header("Scoring settings")
    st.caption("Score = 100 x sum(weight x credit) / sum(weights). Defaults come from config/config.yaml (scoring). "
               "Changing them re-scores instantly; no LLM call.")
    w_must = st.number_input("Weight: must-have", 0.0, 10.0, DEFAULT_WEIGHTS["must_have"], 0.5)
    w_nice = st.number_input("Weight: good-to-have", 0.0, 10.0, DEFAULT_WEIGHTS["nice_to_have"], 0.5)
    c_hands = st.number_input("Credit: hands-on", 0.0, 1.0, DEFAULT_CREDITS["hands_on"], 0.05)
    c_theory = st.number_input("Credit: theoretical", 0.0, 1.0, DEFAULT_CREDITS["theoretical"], 0.05)
WEIGHTS = {"must_have": w_must, "nice_to_have": w_nice}
CREDITS = {"hands_on": c_hands, "theoretical": c_theory}

st.title("AI Recruitment Assistant")
st.warning("AI-generated, needs human review. The score only advises; a person decides.")

# ----------------------------------------------------------------------------- inputs
st.markdown("## 1. Inputs")
left, right = st.columns(2)
with left:
    st.markdown("**Job description**")
    jd_src = st.radio("JD source", ["Paste JD text (runs Phase 1)", "Upload existing Phase 1 json"], key="jd_src")
    jd_text, jd_json = None, None
    if jd_src.startswith("Paste"):
        jd_text = st.text_area("Paste the job description", height=200)
    else:
        up = st.file_uploader("Phase 1 json (jd-xxxx.json)", type="json", key="jd_json")
        if up:
            try:
                jd_json = json.load(up)
            except Exception:
                st.error("That file is not valid json.")
with right:
    st.markdown("**Resumes**")
    rs_src = st.radio("Resume source", ["Upload resume files (runs Phase 2)", "Upload existing keywords json"], key="rs_src")
    files, kw_uploads = [], {}
    if rs_src.startswith("Upload resume"):
        files = st.file_uploader("Resumes (PDF / DOCX / TXT)", type=["pdf", "docx", "doc", "txt"],
                                 accept_multiple_files=True, key="resumes") or []
    else:
        for u in st.file_uploader("Phase 2 keywords json (*.keywords.json)", type="json",
                                  accept_multiple_files=True, key="kw_json") or []:
            try:
                kw_uploads[u.name] = json.load(u)
            except Exception:
                st.error(f"`{u.name}` is not valid json.")

ready = bool((jd_text and jd_text.strip()) or jd_json) and bool(files or kw_uploads)
if st.button("Run workflow", type="primary", disabled=not ready):
    log = []
    try:
        with st.spinner("Running the workflow: Phase 1 -> Phase 2 -> Phase 2.5 -> HR ..."):
            with tempfile.TemporaryDirectory() as tmp:
                paths, seen = [], {}
                for f in files:                                  # same file name twice must not overwrite
                    n = seen[f.name] = seen.get(f.name, 0) + 1
                    p = Path(tmp) / (f.name if n == 1 else f"{Path(f.name).stem}_{n}{Path(f.name).suffix}")
                    p.write_bytes(f.getbuffer())
                    paths.append(str(p))
                request = PipelineRequest(jd_text=jd_text, jd_json=jd_json, resume_files=paths, keywords_json=kw_uploads,
                                          weights=WEIGHTS, credits=CREDITS, progress=log.append)
                st.session_state["wf"] = run_pipeline(request)
        for k in [k for k in st.session_state if k.startswith("hr_")]:
            del st.session_state[k]                              # fresh HR marks for a new run
        st.session_state["interviews"] = {}                      # fresh Phase 3A results for a new run
        st.session_state["evaluations"] = {}                     # fresh Phase 3B reports for a new run
        st.session_state.pop("ev_turns", None)
        for k in [k for k in st.session_state if k.startswith("evrole_")]:
            del st.session_state[k]
    except Exception as e:
        st.session_state.pop("wf", None)
        st.error(f"The workflow stopped: {e}")

wf = st.session_state.get("wf")
if not wf:
    st.info("Add a JD and at least one resume, then press **Run workflow**.")
    st.stop()

jd = wf.jd
for n in wf.notes:
    st.warning(n)

# ----------------------------------------------------------------------------- Phase 1 view
st.markdown("## 2. Job description (Phase 1)")
st.subheader(jd["role_title"])
for w in jd.get("warnings", []):
    st.info(w)

IMPORTANCE_GROUPS = (("must_have", "Must have"), ("nice_to_have", "Nice to have"))


def show_by_importance(rows, importance_of, render):
    """One table per importance group (must have first). Rows with an unknown importance go in 'Other'."""
    known = {key for key, _ in IMPORTANCE_GROUPS}
    groups = [(title, [r for r in rows if importance_of(r) == key]) for key, title in IMPORTANCE_GROUPS]
    other = [r for r in rows if importance_of(r) not in known]
    if other:
        groups.append(("Other (importance not stated)", other))
    for title, items in groups:
        st.markdown(f"**{title}** - {len(items)} skills")
        if items:
            render(items)
        else:
            st.write("None")


st.markdown("### Skills")
show_by_importance(
    jd["skills"], lambda x: x.get("importance"),
    lambda items: st.dataframe([{
        "JD wording": x["raw_text"],
        "Standard name": x["standard_name"],
        "Category": x["category"],
        "Experience": x.get("required_experience", "Not specified"),
        "Match": x["match_type"],
        "Review": "yes" if x.get("needs_review") else "",
    } for x in items], hide_index=True))

QUESTIONS = jd.get("questions", [])
st.markdown("### HR screening questions")
if not QUESTIONS:
    st.info("This JD json has no screening questions.")
else:
    CANDIDATES = [k["resume"] for k in wf.keywords.values()]
    who = st.selectbox("Candidate", CANDIDATES, key="hr_candidate") if CANDIDATES else None
    for i, q in enumerate(QUESTIONS, start=1):
        with st.container(border=True):
            st.markdown(f"**Q{i}. {q['question']}**")
            st.markdown("**Listen for:**\n" + "\n".join(f"- {k}" for k in q["keywords"]))
            st.markdown(f"**A good answer sounds like:** {q['model_answer']}")
            if who:
                st.radio("Result", MARK_CHOICES, key=f"hr_{who}_{i}", horizontal=True)
    if who:
        got, n = hr_score(st.session_state.get(f"hr_{who}_{i}") for i in range(1, len(QUESTIONS) + 1))
        st.metric("HR screening score", f"{got:g} / {len(QUESTIONS)}", help=f"{n} of {len(QUESTIONS)} questions marked")

# ----------------------------------------------------------------------------- Phase 2 view
st.markdown("## 3. Resumes (Phase 2)")
for f in wf.failures:
    st.error(f"Not scored: **{f['file']}** [{f['status']}] {'; '.join(f['reasons'])[:300]}")
for e in wf.score_errors:
    st.error(f"Not scored: **{e['resume']}** - {e['error']}")
if not wf.keywords:
    st.info("No resume could be read, so there is nothing to score.")
    st.stop()
st.success(f"{len(wf.keywords)} resume(s) read: " + ", ".join(wf.keywords))


def kw_rows(items):
    rows = []
    for k in items:
        ev = k.get("evidence") or [{}]
        rows.append({
            "Skill": k["name"] + (" (also listed)" if k.get("also_in_theory") else ""),
            "Category": k.get("category", ""),
            "Evidence": ev[0].get("quote", ""),
            "Section": ev[0].get("section", ""),
        })
    return rows


st.markdown("### Keywords and evidence")
pick = st.selectbox("Resume", list(wf.keywords), key="view_resume")
kw = wf.keywords[pick]
for col, title in (("hands_on", "Hands-on (named in experience/projects)"),
                   ("theoretical", "Theoretical (everything else stated)")):
    st.markdown(f"**{title}** - {len(kw[col])} skills")
    if kw[col]:
        st.dataframe(kw_rows(kw[col]), hide_index=True)
    else:
        st.write("None")
if kw.get("unmapped"):
    with st.expander(f"Keywords not in the taxonomy ({len(kw['unmapped'])})"):
        st.dataframe(kw["unmapped"], hide_index=True)

# ----------------------------------------------------------------------------- Phase 2.5 view
st.markdown("## 4. Score (Phase 2.5)")
results = sorted((run_data(jd, k, WEIGHTS, CREDITS) for k in wf.keywords.values()),
                 key=lambda r: (-r["score"], str(r["resume"])))


def hr_of(resume):
    return hr_score(st.session_state.get(f"hr_{resume}_{i}") for i in range(1, len(QUESTIONS) + 1))


table = pd.DataFrame([{
    "Rank": i + 1,
    "Resume": r["resume"],
    "Score (/100)": r["score"],
    "Must-have coverage (%)": r["must_have"]["coverage_pct"],
    "Must matched": f"{r['must_have']['matched']}/{r['must_have']['count']}",
    "Missing must-have": len(r["must_have"]["missing"]),
    "Good-to-have matched": f"{r['nice_to_have']['matched']}/{r['nice_to_have']['count']}",
    "HR screening": f"{hr_of(r['resume'])[0]:g} / {len(QUESTIONS)} ({hr_of(r['resume'])[1]} marked)",
} for i, r in enumerate(results)])
st.dataframe(table, hide_index=True)
st.download_button("Download ranking CSV", table.to_csv(index=False), "ranking.csv", "text/csv")

st.subheader("Why this score?")
for r in results:
    with st.expander(f"{r['resume']}  -  {r['score']} / 100   (must-have coverage {r['must_have']['coverage_pct']}%)"):
        st.caption(f"{r['points']:g} of {r['max_points']:g} weighted points")
        if r["must_have"]["missing"]:
            st.warning("Must-have skills with no evidence in the resume (recruiter decides, not an auto-reject): "
                       + ", ".join(r["must_have"]["missing"]))
        show_by_importance(
            r["skills"], lambda x: x.get("importance"),
            lambda items: st.dataframe(pd.DataFrame([{
                "Skill": x["skill"], "Weight": x["weight"],
                "Evidence": {"hands_on": "Hands-on", "theoretical": "Theoretical", "none": "-"}[x["evidence_level"]],
                "Credit": x["credit"], "Points": x["points"], "Matched resume skill": x["resume_skill"] or "",
                "Match": x["match"] or "", "Evidence sentence": x["evidence"],
            } for x in items]), hide_index=True))
        maybe = [(x["skill"], x["possible_matches"]) for x in r["skills"] if x.get("possible_matches")]
        if maybe:
            st.info("Possible matches, NOT scored (related is not the same - recruiter can check): "
                    + "; ".join(f"{a} ~ {', '.join(b)}" for a, b in maybe))
        for w in r["warnings"]:
            st.caption(w)

# ----------------------------------------------------------------------------- HR step
# ----------------------------------------------------------------------------- Phase 3A
# Display only: these two notes are not shown. They stay in the saved scaffold json.
HIDDEN_WARNING_PREFIXES = ("Phase 2 asked for a human look at this resume", "Ignored overall experience")

st.markdown("## 5. Interview questions (Phase 3A)")
INTERVIEWS = st.session_state.setdefault("interviews", {})
for name, sc in wf.interviews.items():
    INTERVIEWS.setdefault(name, sc)
ranked = [r["resume"] for r in results]
with_text = [n for n in ranked if n in wf.resume_texts and n in wf.keywords]
if not with_text:
    st.info("Interview questions need the resume files (Phase 2). They cannot be written from an uploaded keywords json.")
elif not usable_jd_text(jd):
    st.info("Interview questions need the JD text, but this Phase 1 json has none (the text was removed). "
            "Paste the JD text instead of uploading the json.")
else:
    def make(names):
        with st.spinner("Phase 3A: writing questions from the Phase 1 skills and Phase 2 keywords ..."):
            got, bad = run_interview(jd, {n: wf.resume_texts[n] for n in names}, {n: wf.keywords[n] for n in names},
                                     wf.resume_layouts, review=wf.resume_review, save=True)
        INTERVIEWS.update(got)
        for e in bad:
            st.error(f"No interview questions for **{e['candidate']}**: {e['error']}")

    iv_who = st.selectbox("Candidate", with_text, key="iv_candidate")
    c1, c2 = st.columns(2)
    if c1.button("Generate interview questions for this candidate", key="iv_one"):
        make([iv_who])
    if c2.button("Generate for all candidates", key="iv_all"):
        make(with_text)
    sc = INTERVIEWS.get(iv_who)
    if sc:
        st.warning(sc["disclaimer"])
        st.caption(f"Status: {sc['status']}  |  Candidate overall experience: "
                   f"{'n/a' if sc['candidate_overall_years'] is None else str(sc['candidate_overall_years']) + ' years'}  |  "
                   f"Saved scaffold id: {sc['scaffold_id']}")
        st.markdown("**How each JD skill was classified for this candidate**")
        st.dataframe([{
            "Skill": p["name"], "JD": p["importance"], "Resume evidence": p["bucket"],
            "Skill years": p["years_on_skill"], "Level": f"{p['experience_level']} ({p['level_basis']})",
            "Difficulty": p["difficulty"],
        } for p in sc["skill_profiles"]], hide_index=True)
        SECTION_TITLES = {"basic": "Basic", "resume_based": "Resume based",
                          "scenario_case": "Case / situational", "coding": "Coding"}
        last_section = None
        for q in sc["questions"]:
            sec = q.get("section", "resume_based")
            if sec != last_section:
                last_section = sec
                count = sum(1 for x in sc["questions"] if x.get("section", "resume_based") == sec)
                st.subheader(f"{SECTION_TITLES.get(sec, sec)} ({count} questions)")
            with st.container(border=True):
                st.markdown(f"**{q['question_id']}. [{q['skill_name']} | {q['question_type']} | {q['difficulty']}]**  \n{q['question']}")
                st.write("Good answer mentions: " + ", ".join(q["answer_keywords"]))
        for w in sc["warnings"] + sc["validation_issues"]:
            if not w.startswith(HIDDEN_WARNING_PREFIXES):
                st.caption(f"! {w}")
    else:
        st.info("Press a button above to write interview questions for this candidate.")


# ----------------------------------------------------------------------------- Phase 3B
COLOR = {"Strong": "green", "Adequate": "blue", "Weak": "orange", "Not covered": "red",
         "Strong fit": "green", "Fit": "blue", "Weak fit": "orange", "Not a fit": "red"}


def badge(label):
    return f":{COLOR[label]}[**{label}**]"


def md_escape(text):
    return re.sub(r"([\\`*_{}\[\]()#+\-.!$<>|~])", r"\\\1", text)


st.markdown("## 6. Interview evaluation (Phase 3B)")
EVALS = st.session_state.setdefault("evaluations", {})
ev_skills = eval_steps.skills_from_jd(jd)
if not ev_skills:
    st.info("This JD has no skills, so there is nothing to evaluate the interview against.")
else:
    st.write("**Skills from Phase 1:** " + ", ".join(ev_skills))
    if not (jd.get("jd_text") or "").strip():
        st.caption("This Phase 1 json has no JD text, so only the role title and the skills are used as context.")
    ev_who = st.selectbox("Candidate", ranked, key="ev_candidate")
    ev_up = st.file_uploader("Transcript (.vtt, .docx or .txt)", type=["vtt", "docx", "txt"], key="ev_file")
    ev_paste = st.text_area("Or paste the transcript", height=120, key="ev_paste",
                            placeholder="[00:01:05] Interviewer: Tell me about...\n[00:01:20] Candidate: I worked on...")
    if st.button("Read transcript", key="ev_parse"):
        st.session_state.pop("ev_turns", None)
        for k in [k for k in st.session_state if k.startswith("evrole_")]:
            del st.session_state[k]
        try:
            raw = read_upload(ev_up.name, ev_up.getvalue()) if ev_up else ev_paste
            st.session_state["ev_turns"] = parse_transcript(raw)
            st.session_state["ev_turns_for"] = ev_who
        except EvalError as e:
            st.error(str(e))
    turns = st.session_state.get("ev_turns")
    if turns:
        names = unique_speakers(turns)
        if st.session_state.get("ev_turns_for") != ev_who:
            st.warning(f"This transcript was read for **{st.session_state.get('ev_turns_for')}**, "
                       f"but **{ev_who}** is selected. The report is saved under the selected candidate.")
        if len(names) < 2:
            st.warning("Only 1 speaker found. Either the transcript has no speaker labels or it was parsed wrongly.")
        else:
            st.caption(f"{len(turns)} turns, {len(names)} speakers. Check the roles; change any that are wrong.")
        defaults, roles = default_roles(names), {}
        for n in names:
            count = sum(1 for t in turns if t["speaker"] == n)
            roles[n] = st.selectbox(f"{n} ({count} turns)", ["Interviewer", "Candidate"],
                                    index=0 if defaults[n] == "Interviewer" else 1, key=f"evrole_{n}")
        if st.button("Evaluate interview", type="primary", key="ev_run"):
            EVALS.pop(ev_who, None)
            status = st.status("Phase 3B: evaluating ...", expanded=True)
            try:
                EVALS[ev_who] = run_evaluation(EvaluationRequest(turns=turns, roles=roles, jd=jd, candidate=ev_who,
                                                                 progress=status.write))
                status.update(label="Done.", state="complete", expanded=False)
            except EvalError as e:
                status.update(label="Error", state="error")
                st.error(str(e))
            except Exception as e:
                status.update(label="Error", state="error")
                st.error(f"Unexpected problem: {e}")

    rep = EVALS.get(ev_who)
    if rep:
        st.markdown(f"### Report for {ev_who}")
        for skill, x in rep["skills"].items():
            st.markdown(f"**{md_escape(skill)}** {badge(x['rating'])}")
            if x["dropped"]:
                st.caption(f"{x['dropped']} quote(s) did not match the transcript and were removed.")
            for q in x["quotes"][:3]:
                st.markdown("> " + md_escape(q))
        o = rep["recommendation"]
        st.markdown(f"### {badge(o['recommendation'])}  |  Confidence: **{o['confidence']}**")
        st.markdown("**Reasons**")
        for x in o["reasons"] or ["-"]:
            st.markdown("- " + md_escape(x))
        st.markdown("**Skills not covered** (worked out by code)")
        for x in rep["not_covered"] or ["All skills were covered"]:
            st.markdown("- " + md_escape(x))
        st.markdown("**Interview quality**")
        for x in (o["interview_quality"] + rep["quality_notes"]) or ["No issues found"]:
            st.markdown("- " + md_escape(x))

        st.markdown("**Manager decision**")
        dec = st.radio("Decision", ["Agree", "Disagree"], index=None, horizontal=True, key=f"evdec_{ev_who}")
        why = st.text_area("Reason", height=80, key=f"evwhy_{ev_who}")
        if st.button("Save decision", key=f"evsave_{ev_who}"):
            if not dec:
                st.error("Choose Agree or Disagree.")
            elif dec == "Disagree" and len(why.strip()) < 5:
                st.error("A reason is required when you disagree.")
            else:
                rep["manager"] = {"decision": dec, "reason": why.strip(), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
                st.success("Decision saved. Download the report below.")
        if rep.get("manager"):
            st.download_button("Download report (JSON)", json.dumps(rep, indent=2, ensure_ascii=False),
                               file_name=f"interview-report-{Path(ev_who).stem}.json", mime="application/json",
                               key=f"evdl_{ev_who}")
