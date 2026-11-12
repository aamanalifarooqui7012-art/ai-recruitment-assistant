"""Every step of the project as an MAF Executor, all wired into ONE workflow (see workflow.py).

  Router            start      sends the request to the right branch (full run / Phase 3A only / Phase 3B only)
  JdExecutor        Phase 1    JD text -> skills (must_have / nice_to_have) + HR questions      [LLM, via phase1]
  ResumeExecutor    Phase 2    resume files -> hands_on / theoretical keywords + evidence       [LLM, via resume_std]
  ScoreExecutor     Phase 2.5  must_have 3 / nice 1  x  hands-on 1.0 / theoretical 0.4  -> /100 [plain code]
  HrExecutor        HR step    screening questions + a blank mark sheet per candidate           [plain code]
  Phase 3A          prepare -> load_evidence -> verify_classify -> plan -> generate[LLM] <-> validate -> finalize
                    interview questions PER CANDIDATE, built on the Phase 1 skills + Phase 2 keywords. All candidates
                    move through the steps together; one failing candidate never stops the others.
  Phase 3B          prepare -> analyse[LLM] -> merge -> recommend[LLM]   (interview transcript -> ratings -> recommendation)

HR screening (Phase 1 questions) and interview questions (Phase 3A) are kept apart on purpose: different questions,
different purpose, different keys in the result (`hr` vs `interviews`).

LLM calls are blocking or async; blocking ones run in a worker thread (asyncio.to_thread) and never freeze the event loop.
Every step records its exception in `errors` before re-raising, so the caller sees the real reason (e.g.
"GROQ_API_KEY_2 is not set") and not a generic "workflow failed".
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing_extensions import Never

from agent_framework import Executor, WorkflowContext, handler

from scoring.hr_screening import blank_packet
from scoring.merge_score import DEFAULT_CREDITS, DEFAULT_WEIGHTS, run_data

from .models import (REPO_ROOT, Cand, EvalState, EvaluationRequest, HrStage, InterviewBatch, InterviewJob,
                     InterviewOutcome, JdStage, PipelineRequest, PipelineResult, ResumeStage, ScoreStage)


MAX_RESUME_WORKERS = 4                                           # resumes read at the same time in Phase 2


# ----------------------------------------------------------------------------- helpers (blocking, run in threads)
def _check_jd_json(jd: dict) -> dict:
    if not isinstance(jd, dict) or "skills" not in jd or "jd_id" not in jd:
        raise ValueError("Not a Phase 1 json (needs `jd_id` and `skills`).")
    return jd


def _run_phase1(jd_text: str, save: bool) -> dict:
    from phase1.pipeline import run_phase1          # imported here so scoring-only use needs no LLM packages
    return run_phase1(jd_text, save=save).model_dump(mode="json")


def _phase2_config() -> dict:
    """Phase 2 config with every relative path made absolute, so nothing depends on the current folder."""
    from resume_std.config import load_config
    cfg = load_config()
    llm = cfg.setdefault("llm", {})
    llm["cache_dir"] = str(REPO_ROOT / ".cache" / "llm")
    cfg["keywords"]["taxonomy_path"] = str(REPO_ROOT / cfg["keywords"]["taxonomy_path"])
    Path(llm["cache_dir"]).mkdir(parents=True, exist_ok=True)
    return cfg


def _unique(name: str, taken: dict) -> str:
    if name not in taken:
        return name
    stem, suffix, i = Path(name).stem, Path(name).suffix, 2
    while f"{stem}_{i}{suffix}" in taken:
        i += 1
    return f"{stem}_{i}{suffix}"


def _run_phase2(request: PipelineRequest) -> tuple[dict, list, list, dict, dict, dict]:
    """-> keywords, failures, notes, texts, review, layouts.  texts/review/layouts feed Phase 3A; Phase 2 itself is untouched."""
    from interview.resume_text import layout_to_text
    from resume_std.pipeline import process_file, write_outputs
    cfg = _phase2_config()
    keywords, failures, notes, results, texts, review, layouts = {}, [], [], [], {}, {}, {}
    total = len(request.resume_files)
    done = [0]

    def _one(path):
        r = process_file(str(path), cfg)
        done[0] += 1
        request.log(f"Phase 2: finished resume {done[0]}/{total} - {Path(path).name}")
        return r

    # resumes are independent, so read them in parallel (LLM/OCR waits overlap); map() keeps the input order
    with ThreadPoolExecutor(max_workers=max(1, min(MAX_RESUME_WORKERS, total))) as pool:
        all_results = list(pool.map(_one, request.resume_files))
    for path, r in zip(request.resume_files, all_results):
        results.append(r)
        if r.get("keywords"):
            name = _unique(Path(path).name, keywords)
            keywords[name] = r["keywords"]
            if r.get("layout"):
                texts[name] = layout_to_text(r["layout"])
                layouts[name] = r["layout"]
            if r.get("status") != "ok" and r.get("reasons"):
                review[name] = list(r["reasons"])
        else:
            failures.append({"file": r["file"], "status": r["status"], "reasons": list(r["reasons"])})
    if request.save_outputs and results:
        out_dir = Path(request.resume_out_dir) if request.resume_out_dir else REPO_ROOT / "out" / "workflow"
        try:
            write_outputs(results, out_dir)                      # same files as scripts/resume_cli.py
        except Exception as e:
            notes.append(f"Phase 2 files could not be saved to {out_dir}: {e}")
    return keywords, failures, notes, texts, review, layouts


def _interview_names(request: PipelineRequest, scores: list) -> list:
    """Which candidates get interview questions: ranked order, optionally only some of them or only the top N."""
    ranked = [s["resume"] for s in scores]
    if request.interview_candidates is not None:
        wanted = set(request.interview_candidates)
        ranked = [n for n in ranked if n in wanted]
    if request.interview_top_n is not None:
        ranked = ranked[: request.interview_top_n]
    return ranked


# ----------------------------------------------------------------------------- executors
class _Step(Executor):
    """Base: remembers the first exception of the run (the caller re-raises it instead of MAF's generic wrapper)."""

    def __init__(self, id: str, errors: list | None = None):
        super().__init__(id=id)
        self.errors = errors if errors is not None else []

    def _fail(self, e: Exception):
        self.errors.append(e)


class Router(_Step):
    """Start of the workflow. One request type per branch; nothing is changed, only sent on."""

    def __init__(self, errors=None):
        super().__init__("router", errors)

    @handler
    async def pipeline(self, m: PipelineRequest, ctx: WorkflowContext[PipelineRequest | InterviewJob | EvaluationRequest]) -> None:
        await ctx.send_message(m)

    @handler
    async def interview(self, m: InterviewJob, ctx: WorkflowContext[PipelineRequest | InterviewJob | EvaluationRequest]) -> None:
        await ctx.send_message(m)

    @handler
    async def evaluation(self, m: EvaluationRequest, ctx: WorkflowContext[PipelineRequest | InterviewJob | EvaluationRequest]) -> None:
        await ctx.send_message(m)


class JdExecutor(_Step):
    def __init__(self, errors=None):
        super().__init__("phase1_jd", errors)

    @handler
    async def run(self, request: PipelineRequest, ctx: WorkflowContext[JdStage]) -> None:
        try:
            request.validate()
            if request.jd_json:
                request.log("Phase 1: using the uploaded JD json (no LLM call)")
                jd = _check_jd_json(request.jd_json)
            else:
                request.log("Phase 1: reading the JD with the LLM")
                jd = await asyncio.to_thread(_run_phase1, request.jd_text, request.save_outputs)
            await ctx.send_message(JdStage(request=request, jd=jd))
        except Exception as e:
            self._fail(e)
            raise


class ResumeExecutor(_Step):
    def __init__(self, errors=None):
        super().__init__("phase2_resumes", errors)

    @handler
    async def run(self, stage: JdStage, ctx: WorkflowContext[ResumeStage]) -> None:
        try:
            request = stage.request
            keywords, failures, notes, texts, review, layouts = {}, [], [], {}, {}, {}
            for name, kw in request.keywords_json.items():          # ready-made Phase 2 json: no LLM call
                if not isinstance(kw, dict) or "hands_on" not in kw or "theoretical" not in kw:
                    failures.append({"file": name, "status": "invalid",
                                     "reasons": ["Not a Phase 2 keywords json (needs `hands_on` and `theoretical`)."]})
                else:
                    keywords[name] = kw
            if request.resume_files:
                got, bad, more, got_texts, got_review, got_layouts = await asyncio.to_thread(_run_phase2, request)
                for name, kw in got.items():
                    final = _unique(name, keywords)
                    keywords[final] = kw
                    if name in got_texts:
                        texts[final] = got_texts[name]
                    if name in got_review:
                        review[final] = got_review[name]
                    if name in got_layouts:
                        layouts[final] = got_layouts[name]
                failures += bad
                notes += more
            await ctx.send_message(ResumeStage(request=request, jd=stage.jd, keywords=keywords,
                                               failures=failures, notes=notes, texts=texts, review=review, layouts=layouts))
        except Exception as e:
            self._fail(e)
            raise


class ScoreExecutor(_Step):
    def __init__(self, errors=None):
        super().__init__("phase25_score", errors)

    @handler
    async def run(self, stage: ResumeStage, ctx: WorkflowContext[ScoreStage]) -> None:
        try:
            request = stage.request
            request.log("Phase 2.5: scoring")
            scores, errors = [], []
            for name, kw in stage.keywords.items():
                try:
                    scores.append(run_data(stage.jd, kw, request.weights, request.credits))
                except Exception as e:                               # one bad resume must not stop the rest
                    errors.append({"resume": name, "error": str(e)})
            scores.sort(key=lambda r: (-r["score"], str(r["resume"])))
            await ctx.send_message(ScoreStage(request=request, jd=stage.jd, keywords=stage.keywords,
                                              failures=stage.failures, notes=stage.notes,
                                              scores=scores, score_errors=errors, texts=stage.texts, review=stage.review,
                                              layouts=stage.layouts))
        except Exception as e:
            self._fail(e)
            raise


class HrExecutor(_Step):
    def __init__(self, errors=None):
        super().__init__("hr_screening", errors)

    @handler
    async def run(self, stage: ScoreStage, ctx: WorkflowContext[HrStage]) -> None:
        try:
            stage.request.log("HR step: preparing screening questions")
            hr = blank_packet(stage.jd.get("questions", []), [s["resume"] for s in stage.scores])
            settings = {"weights": {**DEFAULT_WEIGHTS, **(stage.request.weights or {})},
                        "credits": {**DEFAULT_CREDITS, **(stage.request.credits or {})}}
            await ctx.send_message(HrStage(request=stage.request, jd=stage.jd, keywords=stage.keywords,
                                           failures=stage.failures, notes=stage.notes, scores=stage.scores,
                                           score_errors=stage.score_errors, hr=hr, settings=settings,
                                           texts=stage.texts, review=stage.review, layouts=stage.layouts))
        except Exception as e:
            self._fail(e)
            raise


# ----------------------------------------------------------------------------- Phase 3A steps (same workflow)
def _cand_failed(c: Cand, e: Exception):
    """Phase 3A keeps going for the other candidates: the failure is stored on this one."""
    from interview.errors import ScaffoldError
    c.error = str(e) if isinstance(e, ScaffoldError) else f"{type(e).__name__}: {e}"
    c.exc = e


def _run_each(batch: InterviewBatch, fn) -> InterviewBatch:
    for c in batch.cands:
        if c.live:
            try:
                c.state = fn(c.state, batch.svc)
            except Exception as e:
                _cand_failed(c, e)
    return batch


class InterviewPrepare(_Step):
    """Phase 3A start. From a full run (HrStage) or on its own (InterviewJob): checks the input, builds the services and
    one Cand per candidate. Does nothing (empty batch) unless the full run asked for interviews."""

    def __init__(self, errors=None):
        super().__init__("phase3a_prepare", errors)

    @handler
    async def from_run(self, stage: HrStage, ctx: WorkflowContext[InterviewBatch]) -> None:
        try:
            request = stage.request
            if not request.interview:
                await ctx.send_message(InterviewBatch(source=stage, svc=None))
                return
            names = _interview_names(request, stage.scores)
            pre = [{"candidate": n, "error": "Phase 3A needs the resume file; only a keywords json was given."}
                   for n in names if n not in stage.texts]
            job = InterviewJob(
                jd=stage.jd, resumes={n: stage.texts[n] for n in names if n in stage.texts},
                keywords=stage.keywords, layouts=stage.layouts,
                notes={n: [f"Phase 2 asked for a human look at this resume: {'; '.join(r)}"] for n, r in stage.review.items()},
                save=request.save_outputs, llm=request.interview_llm, progress=request.log)
            batch = self._build(job, stage)
            batch.errors = pre + batch.errors
            await ctx.send_message(batch)
        except Exception as e:
            self._fail(e)
            raise

    @handler
    async def on_demand(self, job: InterviewJob, ctx: WorkflowContext[InterviewBatch]) -> None:
        try:
            await ctx.send_message(self._build(job, job))
        except Exception as e:
            self._fail(e)
            raise

    @staticmethod
    def _build(job: InterviewJob, source) -> InterviewBatch:
        from interview import pipeline as p
        from interview.config import load_settings
        from interview.runner import NO_KEYWORDS_NOTE, precheck
        from interview.schemas import ScaffoldRequest
        from interview.services import build_services

        if job.requests is not None:                                # ready requests (the API path): no checks
            names, jd_text, jd_skills, early = list(job.requests), None, [], []
        else:
            names = list(job.resumes)
            jd_text, jd_skills, early = precheck(job.jd, names)
        if early or not names:
            return InterviewBatch(source=source, svc=None, errors=early)
        try:
            svc = job.svc or build_services(cfg=job.cfg or load_settings(), llm=job.llm, save=job.save)
        except Exception as e:                                      # e.g. bad config file
            return InterviewBatch(source=source, svc=None, errors=[{"candidate": n, "error": str(e)} for n in names])
        cands, total = [], len(names)
        for i, name in enumerate(names, start=1):
            job.log(f"Phase 3A: interview questions for {name} ({i}/{total})")
            c = Cand(name=name)
            try:
                if job.requests is not None:
                    c.request = job.requests[name]
                else:
                    if name not in job.keywords:
                        from interview.errors import InputError
                        raise InputError(NO_KEYWORDS_NOTE)
                    c.request = ScaffoldRequest(
                        jd_text=jd_text, resume_text=job.resumes[name], candidate_label=name, jd_id=job.jd.get("jd_id"),
                        notes=list((job.notes or {}).get(name, [])), jd_skills=jd_skills,
                        resume_keywords=job.keywords[name], resume_layout=(job.layouts or {}).get(name))
                c.state = p.prepare(c.request, svc)
            except Exception as e:
                _cand_failed(c, e)
            cands.append(c)
        return InterviewBatch(source=source, svc=svc, cands=cands)


class _BatchStep(_Step):
    FN = None

    @handler
    async def run(self, batch: InterviewBatch, ctx: WorkflowContext[InterviewBatch]) -> None:
        try:
            await ctx.send_message(_run_each(batch, type(self).FN) if batch.cands else batch)
        except Exception as e:
            self._fail(e)
            raise


class InterviewLoadEvidence(_BatchStep):       # no LLM: reuses the Phase 1 skills + Phase 2 keywords
    def __init__(self, errors=None):
        super().__init__("phase3a_load_evidence", errors)

    @staticmethod
    def FN(state, svc):
        from interview import pipeline as p
        return p.step_load_evidence(state, svc)


class InterviewVerifyClassify(_BatchStep):
    def __init__(self, errors=None):
        super().__init__("phase3a_verify_classify", errors)

    @staticmethod
    def FN(state, svc):
        from interview import pipeline as p
        return p.step_verify_and_classify(state, svc)


class InterviewPlan(_BatchStep):
    def __init__(self, errors=None):
        super().__init__("phase3a_plan", errors)

    @staticmethod
    def FN(state, svc):
        from interview import pipeline as p
        return p.step_plan(state, svc)


class InterviewGenerate(_Step):                # [LLM] candidates run side by side, at most max_parallel_candidates at once
    def __init__(self, errors=None):
        super().__init__("phase3a_generate", errors)

    @handler
    async def run(self, batch: InterviewBatch, ctx: WorkflowContext[InterviewBatch]) -> None:
        try:
            from interview import pipeline as p
            todo = [c for c in batch.cands if c.live and c.state.needs_retry]
            if todo:
                sem = asyncio.Semaphore(batch.svc.cfg.workflow.max_parallel_candidates)

                async def one(c: Cand):
                    async with sem:
                        try:
                            c.state = await p.step_generate(c.state, batch.svc)
                        except Exception as e:
                            _cand_failed(c, e)

                await asyncio.gather(*(one(c) for c in todo))
            await ctx.send_message(batch)
        except Exception as e:
            self._fail(e)
            raise


class InterviewValidate(_BatchStep):
    def __init__(self, errors=None):
        super().__init__("phase3a_validate", errors)

    @staticmethod
    def FN(state, svc):
        from interview import pipeline as p
        return p.step_validate(state, svc)


def needs_regeneration(batch: InterviewBatch) -> bool:
    """The loop edge validate -> generate: some candidate still has slots to redo and rounds left."""
    return any(c.live and c.state is not None and c.state.needs_retry for c in batch.cands)


class InterviewFinalize(_Step):
    """Last Phase 3A step and the end of the workflow for a full run / an on-demand Phase 3A run."""

    def __init__(self, errors=None):
        super().__init__("phase3a_finalize", errors)

    @handler
    async def run(self, batch: InterviewBatch, ctx: WorkflowContext[Never, PipelineResult | InterviewOutcome]) -> None:
        try:
            from interview import pipeline as p
            objects, scaffolds, errors, excs = {}, {}, list(batch.errors), {}
            for c in batch.cands:
                if c.live:
                    try:
                        c.scaffold = p.step_save(p.step_finalize(c.state, batch.svc), batch.svc)
                    except Exception as e:
                        _cand_failed(c, e)
                if c.live:
                    objects[c.name] = c.scaffold
                    scaffolds[c.name] = c.scaffold.model_dump(mode="json")
                else:
                    errors.append({"candidate": c.name, "error": c.error})
                    excs[c.name] = c.exc
            stage = batch.source
            if not isinstance(stage, HrStage):
                await ctx.yield_output(InterviewOutcome(scaffolds=scaffolds, errors=errors, objects=objects, exceptions=excs))
                return
            notes = list(stage.notes)
            if errors:
                notes.append(f"Phase 3A could not prepare interview questions for {len(errors)} candidate(s); see interview_errors.")
            await ctx.yield_output(PipelineResult(
                jd=stage.jd, keywords=stage.keywords, scores=stage.scores, hr=stage.hr, failures=stage.failures,
                score_errors=stage.score_errors, notes=notes, settings=stage.settings,
                interviews=scaffolds, interview_errors=errors, resume_texts=stage.texts, resume_review=stage.review,
                resume_layouts=stage.layouts))
        except Exception as e:
            self._fail(e)
            raise


# ----------------------------------------------------------------------------- Phase 3B steps (same workflow)
def _jd_context(jd: dict) -> str:
    """JD text if Phase 1 kept it, otherwise a short stand-in (the model only uses it as role context)."""
    text = (jd or {}).get("jd_text")
    if text and text.strip():
        return text
    return f"Role: {(jd or {}).get('role_title', '')}"


class EvalPrepare(_Step):
    def __init__(self, errors=None):
        super().__init__("phase3b_prepare", errors)

    @handler
    async def run(self, req: EvaluationRequest, ctx: WorkflowContext[EvalState]) -> None:
        try:
            from evaluation import steps
            from evaluation.transcript import chunk_turns
            skills = steps.skills_from_jd(req.jd)
            tagged = steps.check_inputs(req.turns, req.roles, skills)
            chunks = chunk_turns(tagged)
            req.log(f"Phase 3B: {len(chunks)} transcript chunk(s), {len(skills)} JD skills")
            await ctx.send_message(EvalState(request=req, skills=skills, jd_text=_jd_context(req.jd), chunks=chunks))
        except Exception as e:
            self._fail(e)
            raise


class EvalAnalyse(_Step):                        # [LLM]
    def __init__(self, errors=None):
        super().__init__("phase3b_analyse", errors)

    @handler
    async def run(self, st: EvalState, ctx: WorkflowContext[EvalState]) -> None:
        try:
            from evaluation import steps
            total = len(st.chunks)
            for i, chunk in enumerate(st.chunks, 1):
                st.request.log(f"Phase 3B: analysing chunk {i}/{total}")
                st.results.append(await asyncio.to_thread(
                    steps.analyse_chunk, chunk, st.skills, st.jd_text, i, total, st.request.log))
            await ctx.send_message(st)
        except Exception as e:
            self._fail(e)
            raise


class EvalMerge(_Step):
    def __init__(self, errors=None):
        super().__init__("phase3b_merge", errors)

    @handler
    async def run(self, st: EvalState, ctx: WorkflowContext[EvalState]) -> None:
        try:
            from evaluation import steps
            st.merged, st.not_covered, st.notes = steps.merge_chunks(st.results, st.skills)
            await ctx.send_message(st)
        except Exception as e:
            self._fail(e)
            raise


class EvalRecommend(_Step):                      # [LLM]
    def __init__(self, errors=None):
        super().__init__("phase3b_recommend", errors)

    @handler
    async def run(self, st: EvalState, ctx: WorkflowContext[Never, dict]) -> None:
        try:
            from evaluation import steps
            st.request.log("Phase 3B: writing the recommendation")
            reco = await asyncio.to_thread(steps.recommend, st.skills, st.merged, st.not_covered, st.notes,
                                           st.jd_text, st.request.log)
            await ctx.yield_output(steps.build_report(
                st.skills, st.merged, st.not_covered, reco, st.notes,
                candidate=st.request.candidate, jd_id=st.request.jd.get("jd_id")))
        except Exception as e:
            self._fail(e)
            raise
