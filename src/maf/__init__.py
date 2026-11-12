"""Microsoft Agent Framework (MAF) layer: ONE workflow for the whole project.

    Phase 1 -> Phase 2 -> Phase 2.5 -> HR -> Phase 3A (interview questions)     run_pipeline(PipelineRequest(...))
    Phase 3A alone, for candidates already processed                            run_interview(jd, resumes, keywords, layouts)
    Phase 3B (interview evaluation from a transcript, same Phase 1 JD)          run_evaluation(EvaluationRequest(...))

    from maf import PipelineRequest, run_pipeline
    result = run_pipeline(PipelineRequest(jd_text="...", resume_files=["a.pdf", "b.docx"], interview=True))
    result.hr           # HR screening questions (Phase 1)       - same for every candidate
    result.interviews   # interview questions (Phase 3A)         - one set per candidate, from their own resume
"""
from .models import EvaluationRequest, PipelineRequest, PipelineResult
from .workflow import (build_workflow, run_evaluation, run_evaluation_async, run_interview, run_interview_async,
                       run_pipeline, run_pipeline_async)

__all__ = ["PipelineRequest", "PipelineResult", "EvaluationRequest", "build_workflow", "run_pipeline", "run_pipeline_async",
           "run_interview", "run_interview_async", "run_evaluation", "run_evaluation_async"]
