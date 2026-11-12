"""The combined app's Phase 3A section: its own section, per candidate, apart from the HR screening."""
import io
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import maf.executors as ex
from phase3_helpers import DATA, demo_llm
from test_phase3_integration import JD_WITH_TEXT, RESUME, _docx

REPO = Path(__file__).resolve().parents[1]
APP = REPO / "ui" / "phase_app.py"

HARNESS = '''
import io, runpy
import streamlit as st

class Up(io.BytesIO):
    def __init__(self, name, data):
        super().__init__(data); self.name = name; self.size = len(data)

def fake_uploader(label, type=None, accept_multiple_files=False, key=None, **kw):
    if key == "resumes":
        return [Up("rahul.docx", open(r"__DOCX__", "rb").read())]
    return [] if accept_multiple_files else None

st.file_uploader = fake_uploader
runpy.run_path(r"__APP__", run_name="__main__")
'''


@pytest.fixture
def app(tmp_path, monkeypatch):
    docx = tmp_path / "rahul.docx"
    _docx(docx, RESUME)
    orig = ex._phase2_config

    def cfg():
        c = orig()
        c["llm"]["enabled"] = False
        return c
    import copy
    monkeypatch.setattr(ex, "_phase2_config", cfg)                                   # Phase 2 without an LLM
    monkeypatch.setattr(ex, "_run_phase1", lambda text, save: copy.deepcopy(JD_WITH_TEXT))   # Phase 1 without an LLM
    import interview.services as svcs
    monkeypatch.setattr(svcs, "MafLLMClient", lambda cfg: demo_llm())                # Phase 3A without an LLM
    at = AppTest.from_string(HARNESS.replace("__DOCX__", str(docx)).replace("__APP__", str(APP)), default_timeout=90).run()
    at.text_area[0].set_value((DATA / "sample_jd.txt").read_text(encoding="utf-8")).run()
    [b for b in at.button if b.label == "Run workflow"][0].click()
    return at.run()


def test_phase3a_is_its_own_section_after_the_score(app):
    assert not app.exception and not app.error
    headers = [m.value for m in app.markdown if m.value.startswith("## ")]
    score = next(i for i, h in enumerate(headers) if h.startswith("## 4. Score"))
    assert headers[score + 1].startswith("## 5. Interview questions (Phase 3A)")
    assert [b.label for b in app.button if b.key in ("iv_one", "iv_all")] == [
        "Generate interview questions for this candidate", "Generate for all candidates"]
    assert any("Press a button above" in i.value for i in app.info)               # nothing generated until asked


def test_generate_for_one_candidate_shows_questions(app):
    [b for b in app.button if b.key == "iv_one"][0].click()
    app.run()
    assert not app.exception and not app.error
    questions = [m.value for m in app.markdown if m.value.startswith("**") and " | " in m.value.split("\n")[0] and "]**" in m.value]
    assert len(questions) == 20                                                      # config/config.yaml phase3a: 4 sections x 5
    assert any("[Python | " in q for q in questions)
    assert not [m.value for m in app.markdown if m.value.startswith(("Why asked:", "Follow-ups:"))]   # removed from every section
    assert any("AI-generated, needs human review" in w.value for w in app.warning)
    skills = next(d.value for d in app.dataframe if "Resume evidence" in d.value.columns)
    assert set(skills.columns) >= {"Skill", "JD", "Resume evidence", "Level", "Difficulty"}
    # buckets follow what Phase 2 found in this resume (no second extraction); on this sample Phase 2 reads the
    # "Languages:" line as a section heading and skips the skills lines, so nothing is listed-only here
    assert {"project_hands_on", "gap"} <= set(skills["Resume evidence"])


def test_hr_marks_are_not_touched_by_phase3a(app):
    who = app.selectbox(key="hr_candidate").value
    app.radio(key=f"hr_{who}_1").set_value("Met")
    app.run()
    [b for b in app.button if b.key == "iv_all"][0].click()
    app.run()
    assert not app.exception
    assert app.metric[0].value == "1 / 5"                                              # HR score unchanged by 3A
    assert app.radio(key=f"hr_{who}_1").value == "Met"


def test_keywords_json_users_are_told_why_there_are_no_interview_questions():
    from test_ui_smoke import run_combined_app
    at = run_combined_app()
    assert not at.exception
    assert any("need the resume files" in i.value for i in at.info)
