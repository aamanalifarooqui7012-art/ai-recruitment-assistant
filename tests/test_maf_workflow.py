"""The Microsoft Agent Framework workflow: JD -> resumes -> score -> HR."""
import asyncio
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

import maf.executors as ex
from maf import PipelineRequest, build_workflow, run_pipeline
from scoring.merge_score import run_data

REPO = Path(__file__).resolve().parents[1]
JD = json.loads((REPO / "samples" / "jd" / "sample_jd.json").read_text(encoding="utf-8"))
KW = {p.name.replace(".keywords.json", ""): json.loads(p.read_text(encoding="utf-8"))
      for p in sorted((REPO / "samples" / "keywords").glob("*.keywords.json"))}


def req(**kw):
    return PipelineRequest(jd_json=copy.deepcopy(JD), keywords_json=copy.deepcopy(KW), save_outputs=False, **kw)


# ---- workflow shape + the no-LLM path
def test_one_workflow_holds_every_phase():
    wf = build_workflow()
    ids = set(wf.executors)
    assert {"phase1_jd", "phase2_resumes", "phase25_score", "hr_screening"} <= ids
    assert {"phase3a_prepare", "phase3a_load_evidence", "phase3a_verify_classify", "phase3a_plan",
            "phase3a_generate", "phase3a_validate", "phase3a_finalize"} <= ids
    assert {"phase3b_prepare", "phase3b_analyse", "phase3b_merge", "phase3b_recommend"} <= ids


def test_there_is_only_one_workflow_builder_in_the_project():
    import re
    hits = [p.name for p in (REPO / "src").rglob("*.py") if re.search(r"\bWorkflowBuilder\(", p.read_text(encoding="utf-8"))]
    assert hits == ["workflow.py"]


def test_json_only_run_matches_direct_scoring_and_ranks_best_first():
    r = run_pipeline(req())
    assert [s["resume"] for s in r.scores] == ["Sample_Candidate_Strong.pdf", "Sample_Candidate_Partial.pdf"]
    assert r.scores[0]["score"] > r.scores[1]["score"] > 0
    for s in r.scores:                                                  # the workflow adds nothing to the maths
        assert s == run_data(JD, KW[s["resume"].replace(".pdf", "")])
    assert not r.failures and not r.score_errors


def test_result_carries_jd_keywords_hr_and_settings():
    r = run_pipeline(req())
    assert r.jd["jd_id"] == JD["jd_id"] and set(r.keywords) == set(KW)
    assert len(r.hr["questions"]) == len(JD["questions"]) == 5
    assert set(r.hr["candidates"]) == {s["resume"] for s in r.scores}
    assert r.settings["weights"] == {"must_have": 3.0, "nice_to_have": 1.0}
    assert json.dumps(r.to_dict())                                      # serialisable for saving


def test_weights_and_credits_flow_through_the_workflow():
    base = run_pipeline(req())
    tuned = run_pipeline(req(weights={"must_have": 2}, credits={"theoretical": 0.5}))
    assert tuned.settings["weights"]["must_have"] == 2 and tuned.settings["credits"]["theoretical"] == 0.5
    assert [s["score"] for s in tuned.scores] != [s["score"] for s in base.scores]


def test_progress_messages_are_reported_in_order():
    log = []
    run_pipeline(req(progress=log.append))
    steps = [m.split(":")[0] for m in log]
    assert steps.index("Phase 1") < steps.index("Phase 2.5") < steps.index("HR step")


def test_same_input_same_output():
    assert run_pipeline(req()).scores == run_pipeline(req()).scores


# ---- bad input
def test_request_validation():
    with pytest.raises(ValueError, match="job description"):
        PipelineRequest(keywords_json=KW).validate()
    with pytest.raises(ValueError, match="not both"):
        PipelineRequest(jd_text="x", jd_json=JD, keywords_json=KW).validate()
    with pytest.raises(ValueError, match="resume"):
        PipelineRequest(jd_json=JD).validate()


def test_bad_jd_json_is_a_clear_error():
    with pytest.raises(ValueError, match="Phase 1 json"):
        run_pipeline(PipelineRequest(jd_json={"nope": 1}, keywords_json=KW, save_outputs=False))


def test_one_bad_resume_json_does_not_stop_the_others():
    r = run_pipeline(PipelineRequest(jd_json=JD, keywords_json={**KW, "broken": {"resume": "x"}}, save_outputs=False))
    assert len(r.scores) == 2
    assert r.failures and r.failures[0]["file"] == "broken" and r.failures[0]["status"] == "invalid"


def test_runs_inside_an_already_running_event_loop():
    async def inside():
        return run_pipeline(req())          # e.g. Jupyter / an async web server
    assert len(asyncio.run(inside()).scores) == 2


# ---- Phase 1 and Phase 2 are really called (LLM faked, no network)
def test_jd_text_runs_phase1_with_the_save_flag(monkeypatch):
    calls = []
    monkeypatch.setattr(ex, "_run_phase1", lambda text, save: calls.append((text, save)) or copy.deepcopy(JD))
    r = run_pipeline(PipelineRequest(jd_text="Some JD", keywords_json=KW, save_outputs=False))
    assert calls == [("Some JD", False)] and r.jd["jd_id"] == JD["jd_id"]


def test_real_phase1_code_with_a_fake_llm():
    import phase1.extract as extract_mod
    import phase1.questions as questions_mod
    from test_phase1_pipeline_mock import fake_llm
    mp = pytest.MonkeyPatch()
    mp.setattr(extract_mod, "call_json", fake_llm)
    mp.setattr(questions_mod, "call_json", fake_llm)
    try:
        r = run_pipeline(PipelineRequest(jd_text="Python developer. React js needed.", keywords_json=KW, save_outputs=False))
    finally:
        mp.undo()
    assert r.jd["role_title"] == "Python Developer" and len(r.jd["questions"]) == 5
    assert {s["standard_id"] for s in r.jd["skills"]} >= {"python", "react"}


@pytest.fixture(scope="module")
def sample_resumes(tmp_path_factory):
    d = tmp_path_factory.mktemp("resumes")
    subprocess.run([sys.executable, str(REPO / "tools" / "make_samples.py"), str(d)], check=True)
    return d


def test_real_phase2_code_end_to_end_without_an_llm(monkeypatch, sample_resumes):
    orig = ex._phase2_config

    def no_llm():
        cfg = orig()
        cfg["llm"]["enabled"] = False                                   # keyword extraction by code only: no network
        return cfg
    monkeypatch.setattr(ex, "_phase2_config", no_llm)
    files = [str(sample_resumes / "classic_single.pdf"), str(sample_resumes / "classic_styles.docx"),
             str(sample_resumes / "corrupt.pdf")]
    r = run_pipeline(PipelineRequest(jd_json=JD, resume_files=files, save_outputs=False))
    assert {"classic_single.pdf", "classic_styles.docx"} <= set(r.keywords)
    assert [f["file"] for f in r.failures] == ["corrupt.pdf"] and r.failures[0]["reasons"]   # rejected, with a reason
    assert len(r.scores) == len(r.keywords) and all(0 <= s["score"] <= 100 for s in r.scores)
    assert set(r.hr["candidates"]) == {s["resume"] for s in r.scores}


def test_phase2_paths_are_made_absolute_so_the_folder_you_run_from_does_not_matter(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cfg = ex._phase2_config()
    assert Path(cfg["llm"]["cache_dir"]).is_absolute() and Path(cfg["keywords"]["taxonomy_path"]).is_absolute()
    assert Path(cfg["keywords"]["taxonomy_path"]).is_file()


def test_phase2_outputs_are_saved_like_the_old_cli(monkeypatch, sample_resumes, tmp_path):
    orig = ex._phase2_config
    monkeypatch.setattr(ex, "_phase2_config", lambda: {**orig(), "llm": {**orig()["llm"], "enabled": False}})
    out = tmp_path / "saved"
    run_pipeline(PipelineRequest(jd_json=JD, resume_files=[str(sample_resumes / "classic_single.pdf")],
                                 save_outputs=True, resume_out_dir=str(out)))
    for sub in ("layout", "debug", "pii", "keywords"):
        assert any((out / sub).iterdir()), sub
    assert (out / "review_log.csv").exists()


def test_same_file_name_twice_does_not_overwrite():
    assert ex._unique("a.pdf", {"a.pdf": 1}) == "a_2.pdf"
    assert ex._unique("a.pdf", {"a.pdf": 1, "a_2.pdf": 1}) == "a_3.pdf"
    assert ex._unique("b.pdf", {"a.pdf": 1}) == "b.pdf"
