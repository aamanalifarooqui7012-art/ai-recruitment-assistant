"""Phase 3A inside the main project: same JD + resume as Phase 1/2, MAF agents, HR questions kept separate."""
import copy
import json
import sys
from pathlib import Path

import pytest

import maf.executors as ex
from fake_openai_server import FakeOpenAIServer
from interview.config import PROJECT_ROOT, load_settings
from interview.errors import LLMCallError, LLMOutputError
from interview.fakes import OfflineDemoLLM
from interview.llm import MafLLMClient
from interview.prompts import slots_from_prompt
from interview.resume_text import layout_to_text
from interview.runner import MIN_JD_NOTE
from interview.schemas import GeneratedQuestionSet, ScaffoldStatus
from interview.taxonomy import SkillTaxonomy
from maf import PipelineRequest, run_interview, run_pipeline
from phase3_helpers import DATA, demo_llm, load, make_svc, run, sample_request

REPO = PROJECT_ROOT
JD = json.loads((REPO / "samples" / "jd" / "sample_jd.json").read_text(encoding="utf-8"))
JD_WITH_TEXT = json.loads((DATA / "sample_phase1.json").read_text(encoding="utf-8"))   # Phase 1 json whose text + skills match the sample JD
KW = {p.name.replace(".keywords.json", ""): json.loads(p.read_text(encoding="utf-8"))
      for p in sorted((REPO / "samples" / "keywords").glob("*.keywords.json"))}
RESUME = (DATA / "sample_resume.txt").read_text(encoding="utf-8")
PHASE2_KW, PHASE2_LAYOUT = load("sample_phase2_keywords.json"), load("sample_phase2_layout.json")
GOOD = GeneratedQuestionSet(questions=[OfflineDemoLLM._question(
    {"slot_id": 1, "skill_name": "Python", "bucket": "gap", "question_type": "technical", "jd_quote": "Python for APIs"})]).model_dump(mode="json")


@pytest.fixture
def no_phase2_llm(monkeypatch):
    """Phase 2 keywords by code only (no network). Phase 2 code itself is untouched."""
    orig = ex._phase2_config

    def cfg():
        c = orig()
        c["llm"]["enabled"] = False
        return c
    monkeypatch.setattr(ex, "_phase2_config", cfg)


def _docx(path, text):
    from docx import Document                      # Phase 2 reads PDF and DOCX
    d = Document()
    for line in text.split("\n"):
        d.add_paragraph(line)
    d.save(path)


@pytest.fixture
def resumes(tmp_path):
    a, b = tmp_path / "rahul.docx", tmp_path / "second.docx"
    _docx(a, RESUME)
    _docx(b, RESUME.replace("Rahul Sharma", "Second Person"))
    return a, b


def pipe(files, **kw):
    return run_pipeline(PipelineRequest(jd_json=copy.deepcopy(JD_WITH_TEXT), resume_files=[str(f) for f in files],
                                        save_outputs=False, **kw))


# ---------------------------------------------------------------- the real MAF agent client, against a fake Groq server
def fake_groq(body):
    user = body["messages"][-1]["content"]
    slots = slots_from_prompt(user)
    return GeneratedQuestionSet(questions=[OfflineDemoLLM._question(s) for s in slots]).model_dump(mode="json")


@pytest.fixture
def groq_env(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY_2", "test-key-never-sent")


def client_for(srv):
    cfg = load_settings()
    cfg.llm.base_url = srv.base_url
    return cfg, MafLLMClient(cfg.llm)


def test_the_maf_question_writer_runs_the_whole_phase3a_flow_over_http(tmp_path, groq_env):
    with FakeOpenAIServer(fake_groq) as srv:
        cfg, llm = client_for(srv)
        sc = run(__import__("interview.pipeline", fromlist=["x"]).run_plain(sample_request(), make_svc(tmp_path, llm=llm, cfg=cfg)))
        assert sc.status == ScaffoldStatus.complete and len(sc.questions) == 20
        assert len(srv.requests) == 4                                       # question writer only: one call per section. No extraction call.
        first = srv.requests[0]
        assert first["model"] == "openai/gpt-oss-120b" and first["temperature"] == 0.2
        assert first["max_completion_tokens"] == 10000 and first["reasoning_effort"] == "low"
        assert first["response_format"]["type"] == "json_schema" and first["response_format"]["json_schema"]["strict"]
        assert first["messages"][0]["role"] == "system" and "<jd>" in first["messages"][1]["content"]
        assert "SLOTS_JSON" in first["messages"][1]["content"]
        assert "extract skill evidence" not in first["messages"][0]["content"]


def test_schema_mode_rejected_falls_back_to_json_mode(tmp_path, groq_env):
    def responder(body):
        if body["response_format"]["type"] == "json_schema":
            return {"status": 400, "message": "json_schema not supported"}
        return GOOD
    with FakeOpenAIServer(responder) as srv:
        cfg, llm = client_for(srv)
        out = run(llm.generate(GeneratedQuestionSet, role="generate", instructions="x", prompt="<jd>a</jd><resume>b</resume>"))
        assert out.questions and [r["response_format"]["type"] for r in srv.requests] == ["json_schema", "json_object"]


def test_invalid_json_is_re_asked_with_the_error_then_accepted(groq_env):
    def responder(body):
        return '{"questions": "oops"}' if len(body["messages"]) == 2 else GOOD
    with FakeOpenAIServer(responder) as srv:
        cfg, llm = client_for(srv)
        out = run(llm.generate(GeneratedQuestionSet, role="generate", instructions="x", prompt="p"))
        assert out.questions and len(srv.requests) == 2
        retry = srv.requests[1]["messages"]
        assert retry[2]["role"] == "assistant" and "invalid" in retry[3]["content"]


def test_cut_off_output_is_a_clear_error(groq_env):
    with FakeOpenAIServer(lambda b: {"content": '{"questions": [', "finish_reason": "length"}) as srv:
        cfg, llm = client_for(srv)
        with pytest.raises(LLMOutputError, match="cut off"):
            run(llm.generate(GeneratedQuestionSet, role="generate", instructions="x", prompt="p"))


@pytest.mark.parametrize("status,text", [(401, "rejected the API key"), (429, "rate limit")])
def test_provider_errors_keep_their_clear_messages(groq_env, status, text):
    with FakeOpenAIServer(lambda b: {"status": status, "message": "nope"}) as srv:
        cfg, llm = client_for(srv)
        llm.cfg.max_schema_retries = 0
        with pytest.raises(LLMCallError, match=text):
            run(llm.generate(GeneratedQuestionSet, role="generate", instructions="x", prompt="p"))


def test_missing_api_key_says_where_to_put_it(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    with pytest.raises(LLMCallError, match="GROQ_API_KEY_2 is not set"):
        run(MafLLMClient(load_settings().llm).generate(GeneratedQuestionSet, role="generate", instructions="x", prompt="p"))


# ---------------------------------------------------------------- Phase 2 text -> Phase 3A (no second upload)
def test_resume_text_comes_from_the_phase2_layout(no_phase2_llm, resumes):
    from resume_std.pipeline import process_file
    r = process_file(str(resumes[0]), ex._phase2_config())
    text = layout_to_text(r["layout"])
    assert "Built payment reconciliation APIs in Python and FastAPI" in text
    assert "SKILLS" in text and "EXPERIENCE" in text           # fixed section titles
    assert "rahul.sharma@example.com" not in text              # Phase 2 already masked the e-mail
    assert "Link:" not in text and " to present" not in text   # only real resume lines


# ---------------------------------------------------------------- the whole MAF workflow with Phase 3A
def test_workflow_runs_phase3a_on_the_same_jd_and_resumes(no_phase2_llm, resumes):
    r = pipe(resumes, interview=True, interview_llm=demo_llm())
    assert set(r.interviews) == {"rahul.docx", "second.docx"} and not r.interview_errors
    sc = r.interviews["rahul.docx"]
    assert sc["status"] == "complete" and sc["candidate_label"] == "rahul.docx" and sc["jd_id"] == JD["jd_id"]
    assert sc["review_status"] == "pending_human_review" and len(sc["questions"]) == 20
    ids = {p["skill_id"]: p for p in sc["skill_profiles"]}
    assert ids["python"]["bucket"] == "project_hands_on" and ids["kubernetes"]["bucket"] == "gap"


def test_hr_questions_and_interview_questions_stay_separate(no_phase2_llm, resumes):
    off, on = pipe(resumes), pipe(resumes, interview=True, interview_llm=demo_llm())
    assert off.interviews == {} and off.interview_errors == []
    assert on.hr == off.hr and on.hr["questions"] == JD["questions"]           # HR step identical with or without 3A
    assert len(on.hr["questions"]) == 5 and len(on.interviews["rahul.docx"]["questions"]) == 20
    hr_texts = {q["question"] for q in on.hr["questions"]}
    assert not hr_texts & {q["question"] for q in on.interviews["rahul.docx"]["questions"]}
    d = on.to_dict()
    assert set(d) >= {"hr", "interviews"} and "resume_texts" not in d and RESUME[:40] not in json.dumps(d)


def test_phase3a_is_off_by_default_and_calls_no_llm(no_phase2_llm, resumes):
    class Boom:
        async def generate(self, *a, **k):
            raise AssertionError("LLM must not be called")
    assert pipe(resumes, interview_llm=Boom()).interviews == {}


def test_top_n_and_named_candidates(no_phase2_llm, resumes):
    ranked = [s["resume"] for s in pipe(resumes).scores]
    top = pipe(resumes, interview=True, interview_top_n=1, interview_llm=demo_llm())
    assert list(top.interviews) == ranked[:1]
    named = pipe(resumes, interview=True, interview_candidates=[ranked[1]], interview_llm=demo_llm())
    assert list(named.interviews) == [ranked[1]]
    with pytest.raises(ValueError, match="interview_top_n"):
        PipelineRequest(jd_json=JD, keywords_json=KW, interview_top_n=0).validate()


def test_keywords_json_only_cannot_get_interview_questions():
    r = run_pipeline(PipelineRequest(jd_json=JD_WITH_TEXT, keywords_json=copy.deepcopy(KW), save_outputs=False,
                                     interview=True, interview_llm=demo_llm()))
    assert r.interviews == {} and len(r.scores) == 2 and len(r.interview_errors) == 2
    assert "needs the resume file" in r.interview_errors[0]["error"] and any("Phase 3A" in n for n in r.notes)


def test_jd_json_without_text_is_reported_not_crashed(no_phase2_llm, resumes):
    r = run_pipeline(PipelineRequest(jd_json=copy.deepcopy(JD), resume_files=[str(f) for f in resumes],
                                     save_outputs=False, interview=True, interview_llm=demo_llm()))
    assert r.interviews == {} and {e["error"] for e in r.interview_errors} == {MIN_JD_NOTE}   # placeholder text: no LLM call
    assert len(r.scores) == 2                                              # Phases 1-2.5 are not affected
    no_key = {k: v for k, v in JD.items() if k != "jd_text"}
    r2 = run_pipeline(PipelineRequest(jd_json=no_key, resume_files=[str(f) for f in resumes], save_outputs=False,
                                      interview=True, interview_llm=demo_llm()))
    assert {e["error"] for e in r2.interview_errors} == {MIN_JD_NOTE}


class _FailsFor(OfflineDemoLLM):
    async def generate(self, schema, *, role, instructions, prompt):
        if schema is GeneratedQuestionSet and "Second Person" in prompt:
            raise LLMCallError("Groq rate limit reached (test)")
        return await super().generate(schema, role=role, instructions=instructions, prompt=prompt)


def test_one_failing_candidate_does_not_stop_the_others(no_phase2_llm, resumes):
    r = pipe(resumes, interview=True, interview_llm=_FailsFor())
    assert list(r.interviews) == ["rahul.docx"]
    assert r.interview_errors == [{"candidate": "second.docx", "error": "Groq rate limit reached (test)"}]
    assert len(r.scores) == 2


def test_phase2_review_reasons_become_warnings_in_the_scaffold(no_phase2_llm, resumes):
    scaffolds, errors = run_interview(JD_WITH_TEXT, {"rahul.docx": RESUME}, {"rahul.docx": PHASE2_KW}, {"rahul.docx": PHASE2_LAYOUT},
                                      review={"rahul.docx": ["ocr_used"]}, save=False, llm=demo_llm())
    assert not errors and any("human look" in w and "ocr_used" in w for w in scaffolds["rahul.docx"]["warnings"])


def test_on_demand_interview_without_rerunning_phase1_or_2(no_phase2_llm, resumes):
    r = pipe(resumes)                                                      # the workflow ran without 3A ...
    got, bad = run_interview(r.jd, {"rahul.docx": r.resume_texts["rahul.docx"]}, {"rahul.docx": r.keywords["rahul.docx"]},
                             {"rahul.docx": r.resume_layouts["rahul.docx"]}, save=False, llm=demo_llm())
    assert not bad and got["rahul.docx"]["status"] == "complete"            # ... one candidate is asked for later


def test_save_flag_controls_whether_scaffolds_are_written(no_phase2_llm, resumes, tmp_path, monkeypatch):
    import interview.services as svcs
    monkeypatch.setattr(svcs, "JsonFileStore", lambda d: __import__("interview.store", fromlist=["x"]).JsonFileStore(tmp_path / "saved"))
    args = (JD_WITH_TEXT, {"rahul.docx": RESUME}, {"rahul.docx": PHASE2_KW}, {"rahul.docx": PHASE2_LAYOUT})
    run_interview(*args, save=False, llm=demo_llm())
    assert not (tmp_path / "saved").exists()
    got, _ = run_interview(*args, save=True, llm=demo_llm())
    assert (tmp_path / "saved" / f"{got['rahul.docx']['scaffold_id']}.json").is_file()


# ---------------------------------------------------------------- shared taxonomy, CLI, layout
def test_phase3a_uses_the_one_shared_taxonomy():
    import phase1.taxonomy as t1
    t = SkillTaxonomy.from_yaml()
    assert t.warnings == []
    for name, sid in (("k8s", "kubernetes"), ("React.js", "react"), ("Apache Kafka", "kafka"), ("Postgres", "postgresql")):
        assert t.canonical(name) == (sid, True) and sid in t1.TAXONOMY.skills


def test_plain_english_aliases_are_not_used_to_search_the_resume():
    t = SkillTaxonomy.from_yaml()
    assert t.ambiguous
    for sid in t._aliases:
        shown = {a.lower() for a in t.aliases_for(sid)[1:]}
        assert not shown & {a.lower() for a in t.ambiguous if len(a) > 1 and a in shown}, sid


def test_command_line_runs_phase3a_and_saves_files(no_phase2_llm, resumes, tmp_path, monkeypatch, capsys):
    import interview.services as svcs
    import maf.run as cli
    monkeypatch.setattr(svcs, "MafLLMClient", lambda cfg: demo_llm())
    jd_file = tmp_path / "jd.json"
    jd_file.write_text(json.dumps(JD_WITH_TEXT), encoding="utf-8")
    out = tmp_path / "res"
    cli.main(["--jd-json", str(jd_file), "--resumes", str(resumes[0]), "--interview", "--no-save", "--out", str(out)])
    text = capsys.readouterr().out
    assert "Interview questions (Phase 3A) - rahul.docx" in text and "HR screening: 5 questions" in text
    assert list((out / "interview").glob("*.interview.json"))
    assert "interviews" in json.loads((out / "workflow_result.json").read_text(encoding="utf-8"))


def test_run_interview_script_uses_saved_phase1_and_phase2_files(no_phase2_llm, resumes, tmp_path, monkeypatch, capsys):
    from resume_std.pipeline import process_file, write_outputs
    import interview.services as svcs
    monkeypatch.setattr(svcs, "MafLLMClient", lambda cfg: demo_llm())
    write_outputs([process_file(str(resumes[0]), ex._phase2_config())], tmp_path / "p2")
    jd_file = tmp_path / "jd.json"
    jd_file.write_text(json.dumps(JD_WITH_TEXT), encoding="utf-8")
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import run_interview as script
        with pytest.raises(SystemExit) as e:
            script.main(["--jd-json", str(jd_file), "--layout", str(tmp_path / "p2" / "layout"), "--no-save",
                         "--out", str(tmp_path / "iv")])
    finally:
        sys.path.remove(str(REPO / "scripts"))
    assert e.value.code == 0 and "[AI-generated, needs human review]" in capsys.readouterr().out
    assert (tmp_path / "iv" / "rahul_docx.interview.json").is_file()
    with pytest.raises(SystemExit, match="jd_text"):
        sys.path.insert(0, str(REPO / "scripts"))
        import run_interview as script2
        bad = tmp_path / "nojd.json"
        bad.write_text(json.dumps(JD), encoding="utf-8")
        script2.main(["--jd-json", str(bad), "--layout", str(tmp_path / "p2" / "layout")])


def test_phase3a_paths_resolve_inside_the_repo():
    from interview.config import DEFAULT_SETTINGS_PATH, DEFAULT_TAXONOMY_PATH, resolve_path
    assert DEFAULT_SETTINGS_PATH == REPO / "config" / "config.yaml" and DEFAULT_SETTINGS_PATH.is_file()
    assert DEFAULT_TAXONOMY_PATH == REPO / "config" / "skills_taxonomy.yaml"
    assert REPO in resolve_path(load_settings().storage.output_dir).parents
