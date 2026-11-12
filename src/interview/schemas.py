"""All data shapes. LLM-facing schemas have NO defaults (all fields required)
so they work with strict structured-output modes."""
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- enums
class Importance(str, Enum):
    must_have = "must_have"
    nice_to_have = "nice_to_have"


class SkillBucket(str, Enum):
    project_hands_on = "project_hands_on"  # used in a project / work entry -> deep questions
    listed_only = "listed_only"            # only claimed in skills list/summary -> medium
    gap = "gap"                            # in JD, no verified evidence in resume


class Section(str, Enum):
    """The four parts of one interview paper (order = order shown to the interviewer)."""
    basic = "basic"                  # core fundamentals of the JD skills
    resume_based = "resume_based"    # driven by the candidate's resume evidence (the old 3A behaviour)
    scenario_case = "scenario_case"  # situational / case questions that are hard to think of from the resume alone
    coding = "coding"                # short coding / logic problems


SECTION_ORDER = [Section.basic, Section.resume_based, Section.scenario_case, Section.coding]


class ExperienceLevel(str, Enum):
    fresher = "fresher"
    mid = "mid"
    senior = "senior"
    unknown = "unknown"


class LevelBasis(str, Enum):
    skill_years = "skill_years"      # level comes from years on THIS skill
    overall_years = "overall_years"  # skill years not stated -> candidate's total experience
    unknown = "unknown"              # nothing known -> medium


class Difficulty(str, Enum):
    easy = "easy"
    medium = "medium"
    hard = "hard"


class QuestionType(str, Enum):
    technical = "technical"
    scenario = "scenario"
    behavioural = "behavioural"


class ScaffoldStatus(str, Enum):
    complete = "complete"
    partial = "partial"  # some slots could not be filled with a valid question


# ---------------------------------------------------------------- API input
class ScaffoldRequest(BaseModel):
    jd_text: str = Field(min_length=1)
    resume_text: str = Field(min_length=1)
    # What Phase 1 and Phase 2 already extracted. Phase 3A reuses it and runs NO extraction of its own.
    jd_skills: list[dict] = Field(min_length=1)       # Phase 1 json `skills`
    resume_keywords: dict                             # Phase 2 keywords json (hands_on / theoretical, with quotes)
    resume_layout: dict | None = None                 # Phase 2 layout json: project names + dates (years of experience)
    candidate_label: str | None = None  # free label for HR (e.g. "Candidate #12"); never sent to the LLM
    jd_id: str | None = None            # Phase 1 id of the JD (traceability only; never sent to the LLM)
    notes: list[str] = Field(default_factory=list)  # e.g. "Phase 2 flagged this resume for review"; shown as warnings


# ---------------------------------------------------------------- evidence (built from Phase 1 + Phase 2, no LLM)
class JDSkill(BaseModel):
    skill_id: str            # lowercase slug, e.g. "react", "postgresql"
    name: str                # as written in the JD
    importance: Importance
    required_experience: str  # e.g. "4+ years" or "Not specified"
    jd_quote: str            # verbatim JD sentence/phrase (verified by code)


class ProjectUse(BaseModel):
    project_name: str        # project title, or "<Company> - <Role>" if no project title
    what_candidate_did: str  # one short sentence
    resume_quote: str        # verbatim resume text (verified by code)


class SkillEvidence(BaseModel):
    skill_id: str                      # must match a jd_skills.skill_id
    listed_quote: str | None           # verbatim text where skill is merely listed/claimed (outside project descriptions)
    years_on_skill: float | None       # years on THIS skill only; null if unknown
    years_basis: Literal["explicit", "inferred_from_dates", "unknown"]
    project_uses: list[ProjectUse]     # concrete projects / work entries that used the skill


class OverallExperience(BaseModel):
    years: float | None                                   # total professional years; 0 for a fresher; null if unknown
    basis: Literal["explicit", "inferred_from_dates", "unknown"]
    quote: str | None                                     # verbatim resume text supporting it (verified by code)


class ExtractionResult(BaseModel):
    jd_skills: list[JDSkill]
    evidence: list[SkillEvidence]
    overall_experience: OverallExperience


# ---------------------------------------------------------------- code-made plan
class SkillProfile(BaseModel):
    skill_id: str
    name: str
    importance: Importance
    jd_order: int
    bucket: SkillBucket
    years_on_skill: float | None = None
    experience_level: ExperienceLevel
    level_basis: LevelBasis = LevelBasis.unknown
    difficulty: Difficulty
    jd_quote: str
    required_experience: str = "Not specified"
    projects: list[ProjectUse] = Field(default_factory=list)
    listed_quote: str | None = None


class QuestionSlot(BaseModel):
    """One question to be written. Code decides everything except the wording."""
    slot_id: int
    skill_id: str
    skill_name: str
    importance: Importance
    bucket: SkillBucket
    experience_level: ExperienceLevel
    level_basis: LevelBasis = LevelBasis.unknown
    years_on_skill: float | None = None
    difficulty: Difficulty
    question_type: QuestionType
    section: Section = Section.resume_based
    jd_quote: str
    required_experience: str = "Not specified"
    projects: list[ProjectUse] = Field(default_factory=list)
    listed_quote: str | None = None


# ---------------------------------------------------------------- LLM step 2: wording
class GeneratedQuestion(BaseModel):
    slot_id: int
    question: str                # coding questions carry their code snippet inside this text
    answer_keywords: list[str]   # 3-6, checked by code


class GeneratedQuestionSet(BaseModel):
    questions: list[GeneratedQuestion]


# ---------------------------------------------------------------- final output
class InterviewQuestion(BaseModel):
    question_id: int
    skill_id: str
    skill_name: str
    importance: Importance
    bucket: SkillBucket
    experience_level: ExperienceLevel
    level_basis: LevelBasis
    years_on_skill: float | None
    section: Section = Section.resume_based
    question_type: QuestionType
    difficulty: Difficulty
    question: str
    answer_keywords: list[str]


class InterviewScaffold(BaseModel):
    scaffold_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    candidate_label: str | None = None
    jd_id: str | None = None
    candidate_overall_years: float | None = None
    status: ScaffoldStatus
    review_status: Literal["pending_human_review"] = "pending_human_review"
    disclaimer: str
    skill_profiles: list[SkillProfile]
    questions: list[InterviewQuestion]
    unfilled_slots: list[int] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    validation_issues: list[str] = Field(default_factory=list)
