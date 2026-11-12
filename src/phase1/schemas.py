"""Fixed output formats for Phase 1."""
import re
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class Category(str, Enum):
    LANGUAGE = "Language"
    FRAMEWORK = "Framework"
    TOOL = "Tool"
    DOMAIN = "Domain"
    SOFT_SKILL = "Soft skill"


class Importance(str, Enum):
    MUST_HAVE = "must_have"
    NICE_TO_HAVE = "nice_to_have"


def _enum_from_text(value, enum_cls):
    """Accept 'Must have', 'must-have', 'Soft Skill' etc. from the LLM."""
    if isinstance(value, str):
        key = re.sub(r"[\s\-]+", "_", value.strip().lower())
        for m in enum_cls:
            if key in (m.value.lower().replace(" ", "_"), m.name.lower()):
                return m
    return value


def _text_or_default(value, default):
    return default if value is None or str(value).strip() == "" else value


# ---------- LLM step 1: extraction ----------
class ExtractedSkill(BaseModel):
    raw_text: str                       # wording exactly as in the JD
    category: Category
    importance: Importance
    required_experience: str = "Not specified"
    evidence_quote: str = ""            # JD sentence it came from

    @field_validator("category", mode="before")
    @classmethod
    def _cat(cls, v):
        return _enum_from_text(v, Category)

    @field_validator("importance", mode="before")
    @classmethod
    def _imp(cls, v):
        return _enum_from_text(v, Importance)

    @field_validator("required_experience", mode="before")
    @classmethod
    def _exp(cls, v):
        return _text_or_default(v, "Not specified")

    @field_validator("evidence_quote", mode="before")
    @classmethod
    def _quote(cls, v):
        return _text_or_default(v, "")


class JDExtraction(BaseModel):
    role_title: str = "Not specified"
    overall_experience: str = "Not specified"
    skills: list[ExtractedSkill]

    @field_validator("role_title", "overall_experience", mode="before")
    @classmethod
    def _default(cls, v):
        return _text_or_default(v, "Not specified")


# ---------- Code step: normalisation ----------
MatchType = Literal["exact", "cleaned", "fuzzy", "unmapped"]


class NormalisedSkill(BaseModel):
    raw_text: str                       # original JD wording is always kept
    standard_id: str                    # "react" or "Unmapped"
    standard_name: str                  # "React" or "Unmapped"
    category: Category
    importance: Importance
    required_experience: str = "Not specified"
    evidence_quote: str = ""
    match_type: MatchType
    needs_review: bool                  # True for fuzzy and unmapped


# ---------- LLM step 2: screening questions ----------
class GeneratedQuestion(BaseModel):
    skill_id: str
    question: str
    keywords: list[str] = Field(min_length=1)
    model_answer: str


class QuestionSet(BaseModel):
    questions: list[GeneratedQuestion]


class ScreeningQuestion(GeneratedQuestion):
    hr_result: Optional[Literal["Met", "Partly met", "Not met"]] = None  # HR fills this


# ---------- Final saved result ----------
class Phase1Result(BaseModel):
    jd_id: str
    jd_text: str
    role_title: str = "Not specified"
    overall_experience: str = "Not specified"
    skills: list[NormalisedSkill]
    unmapped: list[str]
    questions: list[ScreeningQuestion] = []
    warnings: list[str] = []
    model_name: str = "TBD"
    disclaimer: str = "AI-generated, needs human review"
