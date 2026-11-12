"""Messages that travel between the executors of the ONE workflow (plain dataclasses, no LLM here)."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class PipelineRequest:
    """Everything the workflow needs. Give the JD as text (runs Phase 1) OR as a Phase 1 json (skips the LLM).
    Give resumes as files (runs Phase 2) and/or as Phase 2 keywords json (skips the LLM). Both may be mixed."""
    jd_text: Optional[str] = None
    jd_json: Optional[dict] = None
    resume_files: list = field(default_factory=list)            # paths to pdf / docx / doc / txt
    keywords_json: dict = field(default_factory=dict)           # {name: Phase 2 keywords json}
    weights: Optional[dict] = None                              # overrides config/config.yaml (scoring)
    credits: Optional[dict] = None
    save_outputs: bool = True                                   # Phase 1 -> output/results, Phase 2 -> out/workflow
    resume_out_dir: Optional[str] = None
    progress: Optional[Callable[[str], None]] = None            # optional log callback (called from worker threads)
    # ---- Phase 3A: interview questions, written per candidate from the SAME JD + resumes processed above.
    # Separate from the HR screening questions (those are Phase 1, identical for every candidate).
    interview: bool = False                                     # run Phase 3A after the HR step
    interview_top_n: Optional[int] = None                       # only the N best-ranked candidates (None = all scored)
    interview_candidates: Optional[list] = None                 # or exactly these resume names
    interview_llm: Any = None                                   # tests / offline demos: replaces the question-writer agent client

    def validate(self):
        has_text = bool(self.jd_text and self.jd_text.strip())
        has_json = bool(self.jd_json)
        if has_text and has_json:
            raise ValueError("Give the JD either as text or as a Phase 1 json, not both.")
        if not has_text and not has_json:
            raise ValueError("A job description is required (jd_text or jd_json).")
        if not self.resume_files and not self.keywords_json:
            raise ValueError("At least one resume is required (resume_files or keywords_json).")
        if self.interview_top_n is not None and self.interview_top_n < 1:
            raise ValueError("interview_top_n must be 1 or more.")

    def log(self, message: str):
        if self.progress:
            try:
                self.progress(message)
            except Exception:
                pass


@dataclass
class JdStage:                                  # Phase 1 -> Phase 2
    request: PipelineRequest
    jd: dict


@dataclass
class ResumeStage:                              # Phase 2 -> scoring
    request: PipelineRequest
    jd: dict
    keywords: dict                              # {resume name: keywords json}
    failures: list = field(default_factory=list)  # resumes Phase 2 rejected / could not read
    notes: list = field(default_factory=list)
    texts: dict = field(default_factory=dict)     # {resume name: standardised resume text}  (only from resume files)
    review: dict = field(default_factory=dict)    # {resume name: [why Phase 2 asked for a human look]}
    layouts: dict = field(default_factory=dict)   # {resume name: Phase 2 layout json}  (Phase 3A reads project names + dates)


@dataclass
class ScoreStage:                               # scoring -> HR
    request: PipelineRequest
    jd: dict
    keywords: dict
    failures: list
    notes: list
    scores: list = field(default_factory=list)  # ranked, best first
    score_errors: list = field(default_factory=list)
    texts: dict = field(default_factory=dict)
    review: dict = field(default_factory=dict)
    layouts: dict = field(default_factory=dict)


@dataclass
class HrStage:                                  # HR step -> Phase 3A
    request: PipelineRequest
    jd: dict
    keywords: dict
    failures: list
    notes: list
    scores: list
    score_errors: list
    hr: dict
    settings: dict
    texts: dict = field(default_factory=dict)
    review: dict = field(default_factory=dict)
    layouts: dict = field(default_factory=dict)


@dataclass
class PipelineResult:                           # the workflow's final output
    jd: dict
    keywords: dict
    scores: list
    hr: dict                                    # questions + blank mark sheet per candidate
    failures: list = field(default_factory=list)
    score_errors: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    settings: dict = field(default_factory=dict)
    interviews: dict = field(default_factory=dict)         # Phase 3A: {resume name: interview scaffold}. NOT the HR questions
    interview_errors: list = field(default_factory=list)   # candidates Phase 3A could not process, with the reason
    resume_review: dict = field(default_factory=dict)      # {resume name: reasons Phase 2 asked for a human look} (in memory)
    resume_texts: dict = field(default_factory=dict)       # kept in memory for on-demand Phase 3A; never written by to_dict
    resume_layouts: dict = field(default_factory=dict)     # same: Phase 2 layouts, in memory only

    def to_dict(self) -> dict[str, Any]:
        return {"jd_id": self.jd.get("jd_id"), "role_title": self.jd.get("role_title"), "jd": self.jd,
                "keywords": self.keywords, "scores": self.scores, "hr": self.hr, "failures": self.failures,
                "score_errors": self.score_errors, "notes": self.notes, "settings": self.settings,
                "interviews": self.interviews, "interview_errors": self.interview_errors}


# ----------------------------------------------------------------------------- Phase 3A (inside the same workflow)
@dataclass
class InterviewJob:
    """Start message: Phase 3A on its own, for candidates a finished run already processed (nothing is extracted again).
    jd = the Phase 1 dict (needs `jd_text` and `skills`), resumes = {name: Phase 2 text}, keywords / layouts = {name: Phase 2 json}.
    `requests` (ready ScaffoldRequests per candidate) + `svc` skip the checks: used by interview.engine.run_scaffold."""
    jd: dict = field(default_factory=dict)
    resumes: dict = field(default_factory=dict)
    keywords: dict = field(default_factory=dict)
    layouts: Optional[dict] = None
    notes: Optional[dict] = None
    save: bool = True
    llm: Any = None
    cfg: Any = None
    svc: Any = None
    requests: Optional[dict] = None
    progress: Optional[Callable[[str], None]] = None

    def log(self, message: str):
        if self.progress:
            try:
                self.progress(message)
            except Exception:
                pass


@dataclass
class Cand:
    """One candidate travelling through the Phase 3A steps. A failure is stored here, never raised: the others go on."""
    name: str
    request: Any = None                 # interview.schemas.ScaffoldRequest
    state: Any = None                   # interview.pipeline.PipelineState
    scaffold: Any = None                # interview.schemas.InterviewScaffold
    error: Optional[str] = None
    exc: Optional[BaseException] = None

    @property
    def live(self) -> bool:
        return self.error is None


@dataclass
class InterviewBatch:                   # every Phase 3A candidate moves through the steps together, in one message
    source: Any                         # HrStage (full run) or InterviewJob (on demand)
    svc: Any
    cands: list = field(default_factory=list)
    errors: list = field(default_factory=list)      # [{candidate, error}] found before any step ran


@dataclass
class InterviewOutcome:                 # output of an on-demand Phase 3A run
    scaffolds: dict = field(default_factory=dict)   # {name: scaffold json dict}
    errors: list = field(default_factory=list)      # [{candidate, error}]
    objects: dict = field(default_factory=dict)     # {name: InterviewScaffold}
    exceptions: dict = field(default_factory=dict)  # {name: the original exception}


# ----------------------------------------------------------------------------- Phase 3B (inside the same workflow)
@dataclass
class EvaluationRequest:
    turns: list                                   # evaluation.transcript.parse_transcript output
    roles: dict                                   # {speaker: "Interviewer" | "Candidate"}
    jd: dict                                      # the Phase 1 dict: skills + jd_text
    candidate: str = ""                           # resume name, for the log only
    progress: Optional[Callable[[str], None]] = None

    def log(self, message: str):
        if self.progress:
            try:
                self.progress(message)
            except Exception:
                pass


@dataclass
class EvalState:
    request: EvaluationRequest
    skills: list
    jd_text: str
    chunks: list
    results: list = field(default_factory=list)
    merged: Optional[dict] = None
    not_covered: list = field(default_factory=list)
    notes: list = field(default_factory=list)
