"""Paste-box UI for Phase 1.  Run:  streamlit run ui/phase1_app.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # repo/src on the import path

import streamlit as st

from phase1.pipeline import run_phase1

st.set_page_config(page_title="AI Recruitment - Phase 1", layout="wide")
st.title("Phase 1: JD to screening questions")
st.warning("AI-generated, needs human review. The AI only advises; a person decides.")

jd_text = st.text_area("Paste the job description here", height=300)

if st.button("Generate", type="primary", disabled=not jd_text.strip()):
    with st.spinner("Reading the JD..."):
        try:
            st.session_state["result"] = run_phase1(jd_text)
            for key in [k for k in st.session_state if k.startswith("hr_")]:
                del st.session_state[key]          # fresh HR marks for a new JD
        except Exception as e:                     # show a clear message, not a crash
            st.session_state.pop("result", None)
            st.error(str(e))

res = st.session_state.get("result")
if res:
    st.subheader(res.role_title)
    st.caption(f"Overall experience: {res.overall_experience}  |  Saved as {res.jd_id}  |  Model: {res.model_name}")

    for w in res.warnings:
        st.info(w)

    st.markdown("### Skills")
    st.dataframe(
        [
            {
                "JD wording": s.raw_text,
                "Standard name": s.standard_name,
                "Category": s.category.value,
                "Importance": s.importance.value,
                "Experience": s.required_experience,
                "Match": s.match_type,
                "Review": "yes" if s.needs_review else "",
            }
            for s in res.skills
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("### Screening questions")
    st.caption("Mark each answer: most keywords = Met, some = Partly met, none or wrong = Not met. "
               "Equivalent wording also counts.")
    points = {"Met": 1.0, "Partly met": 0.5, "Not met": 0.0}
    total, marked = 0.0, 0
    for i, q in enumerate(res.questions, start=1):
        with st.container(border=True):
            st.markdown(f"**Q{i}. {q.question}**")
            st.markdown("**Listen for:**\n" + "\n".join(f"- {k}" for k in q.keywords))
            st.markdown(f"**A good answer sounds like:** {q.model_answer}")
            choice = st.radio("Result", ["Not marked", *points], key=f"hr_{i}", horizontal=True)
            if choice in points:                   # total is plain arithmetic in code
                total += points[choice]
                marked += 1
    st.metric("HR screening score", f"{total:g} / {len(res.questions)}",
              help=f"{marked} of {len(res.questions)} questions marked")
