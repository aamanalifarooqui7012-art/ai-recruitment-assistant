"""Phase 3B: transcript parsing, quote verification, merge, the MAF workflow, and the UI section. No internet, no API key."""
import json
from pathlib import Path

import pytest

from evaluation import steps
from evaluation.errors import EvalError
from evaluation.transcript import default_roles, parse_transcript, read_upload, unique_speakers
from maf import EvaluationRequest, run_evaluation

REPO = Path(__file__).resolve().parents[1]
TRANSCRIPTS = REPO / "samples" / "transcripts"
JD = json.loads((REPO / "samples" / "jd" / "sample_jd.json").read_text(encoding="utf-8"))


def load(name):
    return parse_transcript(read_upload(name, (TRANSCRIPTS / name).read_bytes()))


def roles_for(turns):
    return {n: ("Interviewer" if n in ("Neha Verma", "Priya Nair") else "Candidate") for n in unique_speakers(turns)}


@pytest.fixture
def fake_groq(monkeypatch):
    """Replaces the Groq call. Returns canned model JSON, which still goes through the REAL validators."""
    calls = []

    def fake(messages, validate, log=print):
        calls.append(messages[0]["content"][:30])
        user = messages[1]["content"]
        if messages[0]["content"].startswith("You are an impartial interview evaluator"):
            sk = steps.skills_from_jd(JD)
            return validate({"skills": [
                {"skill": sk[0], "rating": "Strong", "quotes": ["I profiled it using cProfile"]},       # real quote
                {"skill": sk[1], "rating": "Strong", "quotes": ["this sentence was never said"]},      # invented quote
            ], "quality_notes": ["leading question"]})
        return validate({"recommendation": "Fit", "confidence": "Medium", "reasons": ["ok"],
                         "interview_quality": [], "skills_not_probed": []})

    monkeypatch.setattr(steps, "groq_json", fake)
    fake.calls = calls
    return fake


def test_skills_come_from_phase1_must_have_first_without_duplicates():
    names = steps.skills_from_jd(JD)
    assert names and len(names) == len({n.lower() for n in names})
    first_nice = next(i for i, s in enumerate(JD["skills"]) if s["importance"] != "must_have")
    must = [s["standard_name"] for s in JD["skills"] if s["importance"] == "must_have"]
    assert names[: len(must)] == must or set(names[: len(must)]) == set(must)
    assert first_nice >= 0


def test_all_five_sample_transcripts_behave_as_documented():
    t = load("01_main_interview_long.vtt")
    assert unique_speakers(t) == ["Neha Verma", "Rahul Mehta", "Priya Nair"]
    assert default_roles(unique_speakers(t))["Priya Nair"] == "Candidate"        # the guess is wrong for this name: the person must fix it in the UI
    assert len(load("03_weak_candidate_zoom_style.vtt")) > 5
    assert len(unique_speakers(load("05_single_speaker.vtt"))) == 1
    with pytest.raises(EvalError):
        load("04_broken_not_a_transcript.vtt")


def test_too_short_candidate_is_rejected_before_any_llm_call(fake_groq):
    t = load("02_too_short_candidate.vtt")
    with pytest.raises(EvalError, match="too short"):
        run_evaluation(EvaluationRequest(turns=t, roles=roles_for(t), jd=JD))
    assert fake_groq.calls == []


def test_maf_workflow_verifies_quotes_and_builds_the_report(fake_groq):
    t = load("01_main_interview_long.vtt")
    log = []
    rep = run_evaluation(EvaluationRequest(turns=t, roles=roles_for(t), jd=JD, candidate="rahul.pdf", progress=log.append))
    sk = steps.skills_from_jd(JD)
    assert rep["skills"][sk[0]]["rating"] == "Strong" and rep["skills"][sk[0]]["quotes"]
    assert rep["skills"][sk[1]]["rating"] == "Not covered"                    # invented quote -> downgraded by code
    assert rep["skills"][sk[1]]["dropped"] >= 1
    assert sk[1] in rep["not_covered"]
    assert rep["recommendation"]["recommendation"] == "Fit"
    assert rep["candidate"] == "rahul.pdf" and rep["jd_id"] == JD["jd_id"] and rep["manager"] is None
    assert rep["skills_evaluated"] == sk                                       # the Phase 1 skills, nothing typed in
    assert any("chunk" in m for m in log)


def test_original_error_is_not_hidden_by_the_workflow(monkeypatch):
    def boom(*a, **k):
        raise EvalError("GROQ_API_KEY is not set in .env. Add the key and restart the app.")
    monkeypatch.setattr(steps, "groq_json", boom)
    t = load("01_main_interview_long.vtt")
    with pytest.raises(EvalError, match="GROQ_API_KEY"):
        run_evaluation(EvaluationRequest(turns=t, roles=roles_for(t), jd=JD))


def test_phase_1_to_3a_code_is_untouched():
    """Phase 3B was added beside the earlier phases, not inside them. Hashes were taken before any change."""
    import hashlib
    snap = REPO / "tests" / "phase1_3a_checksums.txt"
    for line in snap.read_text().splitlines():
        digest, rel = line.split("  ", 1)
        assert hashlib.sha256((REPO / rel).read_bytes()).hexdigest() == digest, rel
