"""Each Streamlit app starts, and the combined app's whole button -> workflow -> tables path works (no LLM)."""
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

REPO = Path(__file__).resolve().parents[1]
UI = REPO / "ui"
JD_BYTES = (REPO / "samples" / "jd" / "sample_jd.json").read_bytes()
KW_BYTES = {p.name: p.read_bytes() for p in sorted((REPO / "samples" / "keywords").glob("*.keywords.json"))}

# AppTest cannot drive file_uploader, so this wrapper fakes it and then runs the REAL app file.
HARNESS = f'''
import io, runpy
import streamlit as st

class Up(io.BytesIO):
    def __init__(self, name, data):
        super().__init__(data); self.name = name; self.size = len(data)

def fake_uploader(label, type=None, accept_multiple_files=False, key=None, **kw):
    if key in ("jd_json", "jd"):
        return Up("sample_jd.json", {JD_BYTES!r})
    if key in ("kw_json", "kw"):
        return [Up(n, b) for n, b in {KW_BYTES!r}.items()]
    return [] if accept_multiple_files else None

st.file_uploader = fake_uploader
runpy.run_path(r"__APP__", run_name="__main__")
'''


def harness(app):
    return AppTest.from_string(HARNESS.replace("__APP__", str(UI / app)), default_timeout=60)


@pytest.mark.parametrize("app", ["phase_app.py", "merge_app.py", "phase1_app.py"])
def test_every_app_starts_without_an_error(app):
    at = AppTest.from_file(str(UI / app), default_timeout=60).run()
    assert not at.exception


def test_combined_app_waits_for_inputs():
    at = AppTest.from_file(str(UI / "phase_app.py"), default_timeout=60).run()
    assert any("Run workflow" in i.value for i in at.info)
    assert [b for b in at.button if b.label == "Run workflow"][0].disabled


def run_combined_app():
    at = harness("phase_app.py").run()
    at.radio(key="jd_src").set_value("Upload existing Phase 1 json")
    at.radio(key="rs_src").set_value("Upload existing keywords json")
    at.run()
    [b for b in at.button if b.label == "Run workflow"][0].click()
    at.run()
    return at


def test_combined_app_runs_the_workflow_and_shows_every_section():
    at = run_combined_app()
    assert not at.exception and not at.error
    headers = " | ".join(m.value for m in at.markdown)
    for section in ("## 1. Inputs", "## 2. Job description (Phase 1)", "## 3. Resumes (Phase 2)",
                    "## 4. Score (Phase 2.5)", "### HR screening questions"):
        assert section in headers, section
    frames = {tuple(d.value.columns)[:3]: d.value for d in at.dataframe}
    skill_tables = [d.value for d in at.dataframe if d.value.columns[0] == "JD wording"]
    assert len(skill_tables) == 2                      # one table for must have, one for nice to have
    for t in skill_tables:
        assert list(t.columns) == ["JD wording", "Standard name", "Category", "Experience", "Match", "Review"]
    headers_text = " | ".join(m.value for m in at.markdown)
    assert "**Must have**" in headers_text and "**Nice to have**" in headers_text
    rank = next(v for k, v in frames.items() if k[0] == "Rank")
    assert list(rank["Resume"]) == ["Sample_Candidate_Strong.pdf", "Sample_Candidate_Partial.pdf"]
    assert any(set(v.columns) >= {"Skill", "Category", "Evidence", "Section"} for v in frames.values())


def test_combined_app_hr_marks_are_per_candidate_and_feed_the_ranking_table():
    at = run_combined_app()
    who = at.selectbox(key="hr_candidate").value
    other = [o for o in at.selectbox(key="hr_candidate").options if o != who][0]
    at.radio(key=f"hr_{who}_1").set_value("Met")
    at.radio(key=f"hr_{who}_2").set_value("Partly met")
    at.run()
    assert not at.exception
    assert at.metric[0].value == "1.5 / 5"
    rank = next(d.value for d in at.dataframe if "Rank" in d.value.columns)
    assert rank.set_index("Resume").loc[who, "HR screening"].startswith("1.5 / 5")
    assert rank.set_index("Resume").loc[other, "HR screening"].startswith("0 / 5")      # other candidate untouched


def test_combined_app_rescoring_follows_the_sidebar_without_rerunning_the_workflow():
    at = run_combined_app()
    before = next(d.value for d in at.dataframe if "Rank" in d.value.columns)["Score (/100)"].tolist()
    at.sidebar.number_input[2].set_value(0.0).run()                 # hands-on credit -> 0
    after = next(d.value for d in at.dataframe if "Rank" in d.value.columns)["Score (/100)"].tolist()
    assert not at.exception and after != before


def test_merge_app_scores_uploaded_json():
    at = harness("merge_app.py").run()
    assert not at.exception and not at.error
    rank = next(d.value for d in at.dataframe if "Rank" in d.value.columns)
    assert list(rank["Resume"]) == ["Sample_Candidate_Strong.pdf", "Sample_Candidate_Partial.pdf"]


# ----------------------------------------------------------------------------- title, hidden warnings, Phase 3B section
def test_combined_app_title_has_no_phase_list():
    at = run_combined_app()
    assert [t.value for t in at.title] == ["AI Recruitment Assistant"]
    assert not any("Phase 2.5) + HR" in t.value for t in at.title)


def test_combined_app_shows_phase_3b_section_with_the_phase1_skills():
    at = run_combined_app()
    headers = " | ".join(m.value for m in at.markdown)
    assert "## 6. Interview evaluation (Phase 3B)" in headers
    assert any(m.value.startswith("**Skills from Phase 1:**") for m in at.markdown)
    assert not [t for t in at.text_area if "skills" in (t.label or "").lower()]       # no second JD / skills box
    assert [b for b in at.button if b.label == "Read transcript"]


def test_the_two_unwanted_warnings_match_the_hidden_prefixes():
    """The filter is a prefix list in the app. Check it against the REAL texts the code produces (rendering itself needs a
    resume file + LLM, so this is a text-matching check, not a screen check)."""
    import re
    src = (UI / "phase_app.py").read_text(encoding="utf-8")
    prefixes = eval(re.search(r"HIDDEN_WARNING_PREFIXES = (\(.*?\))\n", src).group(1))
    ev = (REPO / "src" / "interview" / "evidence.py").read_text(encoding="utf-8")
    ex = (REPO / "src" / "maf" / "executors.py").read_text(encoding="utf-8")
    real_1 = re.search(r'"(Ignored overall experience[^"]*)"', ev).group(1)
    real_2 = "Phase 2 asked for a human look at this resume: unknown_headings(1)"
    assert "Phase 2 asked for a human look at this resume" in ex
    assert real_1.startswith(prefixes) and real_2.startswith(prefixes)
    assert not "Some other warning".startswith(prefixes)             # everything else is still shown


def test_combined_app_shows_hr_questions_only_once_with_met_not_met_choices():
    at = run_combined_app()
    sample_qs = json.loads(JD_BYTES)["questions"]
    all_text = "\n".join(m.value for m in at.markdown)
    for q in sample_qs:
        assert all_text.count(q["question"]) == 1          # not repeated in Phase 1 and again in the HR section
    first = at.radio(key=f"hr_{at.selectbox(key='hr_candidate').value}_1")
    assert list(first.options) == ["Not marked", "Met", "Partly met", "Not met"]


def test_hr_questions_sit_right_below_the_phase1_skills_tables_and_tag_lines_are_gone():
    at = run_combined_app()
    texts = [m.value for m in at.markdown]
    heads = [t for t in texts if t in ("### HR screening questions", "## 3. Resumes (Phase 2)")]
    assert heads == ["### HR screening questions", "## 3. Resumes (Phase 2)"]
    everything = "\n".join(texts) + "\n".join(c.value for c in at.caption)
    for gone in ("LLM used", "Saved as", "Mark each answer", "Different from the HR screening", "After the interview",
                 "HR marks the answers"):
        assert gone not in everything, gone
