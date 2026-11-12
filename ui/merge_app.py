"""Phase 2.5 scoring UI.  Run (from the repo root):  python -m streamlit run ui/merge_app.py
Admin uploads the JD json (Phase 1) and the resume keywords json files (Phase 2); scores appear at once.
Reads only JSON. No Phase 1 / Phase 2 code is touched."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # repo/src on the import path

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from scoring.merge_score import DEFAULT_CREDITS, DEFAULT_WEIGHTS, run_data  # noqa: E402

st.set_page_config(page_title="AI Recruitment - Resume Scoring", layout="wide")
st.title("Resume scoring: Phase 1 JD x Phase 2 resumes")
st.warning("AI-generated, needs human review. The score only advises; a person decides.")

with st.sidebar:
    st.header("Scoring settings")
    st.caption("Score = 100 x sum(weight x credit) / sum(weights). Defaults come from the scoring plan.")
    w_must = st.number_input("Weight: must-have", 0.0, 10.0, DEFAULT_WEIGHTS["must_have"], 0.5)
    w_nice = st.number_input("Weight: good-to-have", 0.0, 10.0, DEFAULT_WEIGHTS["nice_to_have"], 0.5)
    c_hands = st.number_input("Credit: hands-on", 0.0, 1.0, DEFAULT_CREDITS["hands_on"], 0.05)
    c_theory = st.number_input("Credit: theoretical", 0.0, 1.0, DEFAULT_CREDITS["theoretical"], 0.05)
WEIGHTS = {"must_have": w_must, "nice_to_have": w_nice}
CREDITS = {"hands_on": c_hands, "theoretical": c_theory}


def load(f):
    f.seek(0)
    return json.load(f)


st.markdown("#### Step 1 - Upload JD (Phase 1 json, e.g. `jd-xxxx.json`)")
jd_file = st.file_uploader("JD json", type="json", key="jd")
st.markdown("#### Step 2 - Upload resumes (Phase 2 `*.keywords.json`, one or many)")
kw_files = st.file_uploader("Resume keywords json", type="json", accept_multiple_files=True, key="kw")

if not jd_file or not kw_files:
    st.info("Upload the JD file and at least one resume file to see the scores.")
    st.stop()

try:
    jd = load(jd_file)
except Exception:
    st.error("The JD file is not valid json.")
    st.stop()

results = []
for f in kw_files:
    try:
        results.append(run_data(jd, load(f), WEIGHTS, CREDITS))
    except Exception as e:
        st.error(f"`{f.name}`: {e}")
if not results:
    st.stop()
results.sort(key=lambda r: r["score"], reverse=True)

st.subheader(f"{jd['role_title']}  ({jd['jd_id']})")
table = pd.DataFrame([{
    "Rank": i + 1,
    "Resume": r["resume"],
    "Score (/100)": r["score"],
    "Must-have coverage (%)": r["must_have"]["coverage_pct"],
    "Must matched": f"{r['must_have']['matched']}/{r['must_have']['count']}",
    "Missing must-have": len(r["must_have"]["missing"]),
    "Good-to-have matched": f"{r['nice_to_have']['matched']}/{r['nice_to_have']['count']}",
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
        st.dataframe(pd.DataFrame([{
            "Skill": x["skill"], "Importance": x["importance"], "Weight": x["weight"],
            "Evidence": {"hands_on": "Hands-on", "theoretical": "Theoretical", "none": "-"}[x["evidence_level"]],
            "Credit": x["credit"], "Points": x["points"], "Matched resume skill": x["resume_skill"] or "",
            "Match": x["match"] or "", "Evidence sentence": x["evidence"],
        } for x in r["skills"]]), hide_index=True)
        maybe = [(x["skill"], x["possible_matches"]) for x in r["skills"] if x.get("possible_matches")]
        if maybe:
            st.info("Possible matches, NOT scored (related is not the same - recruiter can check): "
                    + "; ".join(f"{a} ~ {', '.join(b)}" for a, b in maybe))
        for w in r["warnings"]:
            st.caption(w)
