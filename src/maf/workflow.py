"""The ONE Microsoft Agent Framework workflow of the project. Every phase is a step of this single graph:

                                   +--> phase1_jd -> phase2_resumes -> phase25_score -> hr_screening --+
    router --(PipelineRequest)-----+                                                                    |
           --(InterviewJob)--------------------------------------------------------------> phase3a_prepare
           --(EvaluationRequest)--> phase3b_prepare -> analyse -> merge -> recommend                     |
                                                                                                         v
        phase3a_prepare -> load_evidence -> verify_classify -> plan -> generate <-> validate -> finalize

A fresh workflow is built per run (recommended MAF pattern: clean executor state). Three ways in, one graph:

    run_pipeline(PipelineRequest)        Phase 1 -> 2 -> 2.5 -> HR (-> 3A when request.interview)   -> PipelineResult
    run_interview(jd, resumes, ...)      Phase 3A alone, for candidates a finished run already processed -> (scaffolds, errors)
    run_evaluation(EvaluationRequest)    Phase 3B alone, once an interview transcript exists            -> report dict
"""
import asyncio
import threading
from typing import Optional

from agent_framework import WorkflowBuilder

from .executors import (EvalAnalyse, EvalMerge, EvalPrepare, EvalRecommend, HrExecutor, InterviewFinalize,
                        InterviewGenerate, InterviewLoadEvidence, InterviewPlan, InterviewPrepare, InterviewValidate,
                        InterviewVerifyClassify, JdExecutor, ResumeExecutor, Router, ScoreExecutor, needs_regeneration)
from .models import EvaluationRequest, HrStage, InterviewBatch, InterviewJob, InterviewOutcome, PipelineRequest, PipelineResult


def build_workflow(errors: Optional[list] = None):
    """The single workflow. `errors` collects the first exception of each step so callers can re-raise the real one."""
    errors = errors if errors is not None else []
    router = Router(errors)
    jd, resumes, score, hr = JdExecutor(errors), ResumeExecutor(errors), ScoreExecutor(errors), HrExecutor(errors)
    i_prep, i_load, i_verify, i_plan = (InterviewPrepare(errors), InterviewLoadEvidence(errors),
                                        InterviewVerifyClassify(errors), InterviewPlan(errors))
    i_gen, i_val, i_fin = InterviewGenerate(errors), InterviewValidate(errors), InterviewFinalize(errors)
    e_prep, e_an, e_merge, e_reco = EvalPrepare(errors), EvalAnalyse(errors), EvalMerge(errors), EvalRecommend(errors)
    return (
        WorkflowBuilder(
            name="ai_recruitment",
            description="Phase 1 (JD) -> Phase 2 (resumes) -> Phase 2.5 (score) -> HR screening -> Phase 3A (interview) | Phase 3B (evaluation)",
            start_executor=router,
            output_from=[i_fin, e_reco],
            max_iterations=200,                         # Phase 3A loops (generate <-> validate) up to 1 + max_regeneration_rounds times
        )
        # which branch: decided by what the caller handed in
        .add_edge(router, jd, condition=lambda m: isinstance(m, PipelineRequest))
        .add_edge(router, i_prep, condition=lambda m: isinstance(m, InterviewJob))
        .add_edge(router, e_prep, condition=lambda m: isinstance(m, EvaluationRequest))
        # Phase 1 -> 2 -> 2.5 -> HR -> Phase 3A
        .add_chain([jd, resumes, score, hr])
        .add_edge(hr, i_prep, condition=lambda m: isinstance(m, HrStage))
        # Phase 3A
        .add_chain([i_prep, i_load, i_verify, i_plan, i_gen, i_val])
        .add_edge(i_val, i_gen, condition=needs_regeneration)                     # redo only the failed questions
        .add_edge(i_val, i_fin, condition=lambda b: not needs_regeneration(b))
        # Phase 3B
        .add_chain([e_prep, e_an, e_merge, e_reco])
        .build()
    )


async def _run(message):
    """Run the one workflow on `message`; raise the original error of a failed step, not MAF's wrapper."""
    errors: list[Exception] = []
    try:
        result = await build_workflow(errors).run(message)          # a fresh workflow per run: no state leaks between runs
    except Exception:
        if errors:
            raise errors[0]
        raise
    outputs = result.get_outputs()
    if not outputs:
        if errors:
            raise errors[0]
        raise RuntimeError("The workflow finished without producing a result (a step failed; check the log).")
    return outputs


# ----------------------------------------------------------------------------- full run
async def run_pipeline_async(request: PipelineRequest) -> PipelineResult:
    request.validate()
    outputs = await _run(request)
    return next(o for o in reversed(outputs) if isinstance(o, PipelineResult))


# ----------------------------------------------------------------------------- Phase 3A on its own
async def run_interview_async(jd: dict, resumes: dict, keywords: dict, layouts: dict | None = None, *,
                              review: dict | None = None, save: bool = True, llm=None, progress=None, cfg=None):
    """Phase 3A for candidates a finished run already processed (e.g. one button in the app).
    jd = the Phase 1 dict (needs `jd_text` and `skills`), resumes = {resume name: text} (PipelineResult.resume_texts),
    keywords = {name: Phase 2 keywords} (PipelineResult.keywords), layouts = {name: Phase 2 layout} (resume_layouts).
    Nothing is extracted again; the model only words the questions.
    Returns ({name: scaffold dict}, [{candidate, error}])."""
    notes = {n: [f"Phase 2 asked for a human look at this resume: {'; '.join(r)}"] for n, r in (review or {}).items()}
    out = await _interview_outcome(InterviewJob(jd=jd, resumes=resumes, keywords=keywords, layouts=layouts, notes=notes,
                                                save=save, llm=llm, cfg=cfg, progress=progress))
    return out.scaffolds, out.errors


async def _interview_outcome(job: InterviewJob) -> InterviewOutcome:
    outputs = await _run(job)
    return next(o for o in reversed(outputs) if isinstance(o, InterviewOutcome))


async def run_scaffold_async(req, svc):
    """One candidate from a ready ScaffoldRequest (the HTTP API). Raises the candidate's original error."""
    out = await _interview_outcome(InterviewJob(requests={"candidate": req}, svc=svc, save=True))
    if out.exceptions.get("candidate") is not None:
        raise out.exceptions["candidate"]
    if out.errors:
        from interview.errors import PipelineError
        raise PipelineError(out.errors[0]["error"])
    return out.objects["candidate"]


# ----------------------------------------------------------------------------- Phase 3B on its own
async def run_evaluation_async(request: EvaluationRequest) -> dict:
    outputs = await _run(request)
    return next(o for o in reversed(outputs) if isinstance(o, dict))


# ----------------------------------------------------------------------------- blocking versions
def _run_blocking(make_coro):
    """Run a coroutine to completion from sync code. Also safe inside an already-running event loop (Jupyter)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(make_coro())
    box = {}

    def target():
        try:
            box["value"] = asyncio.run(make_coro())
        except BaseException as e:                      # re-raised in the caller's thread
            box["error"] = e

    t = threading.Thread(target=target)
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box["value"]


def run_pipeline(request: PipelineRequest) -> PipelineResult:
    """Blocking version for scripts and Streamlit."""
    return _run_blocking(lambda: run_pipeline_async(request))


def run_interview(jd: dict, resumes: dict, keywords: dict, layouts: dict | None = None, **kw):
    """Blocking version of run_interview_async."""
    return _run_blocking(lambda: run_interview_async(jd, resumes, keywords, layouts, **kw))


def run_evaluation(request: EvaluationRequest) -> dict:
    """Blocking version for scripts and Streamlit."""
    return _run_blocking(lambda: run_evaluation_async(request))
