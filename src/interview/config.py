"""Phase 3A settings = the `phase3a` section of config/config.yaml (override the file with env PROJECT_CONFIG)."""
import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from project_config import CONFIG_PATH, ROOT, TAXONOMY_PATH, config_path

from .schemas import Difficulty, ExperienceLevel, QuestionType, Section, SkillBucket

PROJECT_ROOT = ROOT                                      # repo root

try:  # load OPENAI_API_KEY etc. from <project>/.env (real environment variables win)
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=True)
except ImportError:
    pass
DEFAULT_SETTINGS_PATH = CONFIG_PATH
DEFAULT_TAXONOMY_PATH = TAXONOMY_PATH                    # the ONE taxonomy shared with Phase 1 and Phase 2
DEFAULT_RESUME_CONFIG_PATH = CONFIG_PATH                 # the ambiguous-word list lives in the phase2 section


def resolve_path(p: str | Path) -> Path:
    p = Path(p)
    return p if p.is_absolute() else PROJECT_ROOT / p


class LLMCfg(BaseModel):
    provider: Literal["groq"] = "groq"
    model: str = "openai/gpt-oss-120b"
    base_url: str = "https://api.groq.com/openai/v1"
    api_key_env: str = "GROQ_API_KEY_2"
    temperature: float = Field(0.2, ge=0, le=2)
    reasoning_effort: Literal["low", "medium", "high"] = "low"
    max_output_tokens: int = Field(10000, ge=500, le=32000)
    max_schema_retries: int = Field(2, ge=0, le=5)


class WorkflowCfg(BaseModel):
    engine: Literal["maf", "plain"] = "maf"
    max_regeneration_rounds: int = Field(2, ge=0, le=5)
    max_parallel_candidates: int = Field(1, ge=1, le=8)   # candidates processed at the same time (mind Groq rate limits)


class InputCfg(BaseModel):
    min_chars_jd: int = 50
    min_chars_resume: int = 80
    max_chars_jd: int = 20000
    max_chars_resume: int = 30000
    max_skills: int | None = Field(None, ge=1, le=500)  # None = no limit
    mask_pii: bool = True


class ExperienceCfg(BaseModel):
    mid_from_years: float = 1.0
    senior_from_years: float = 3.0

    @model_validator(mode="after")
    def _order(self):
        if self.senior_from_years <= self.mid_from_years:
            raise ValueError("senior_from_years must be greater than mid_from_years")
        return self


def _default_extra_sections() -> dict[Section, int]:
    return {Section.basic: 5, Section.scenario_case: 5, Section.coding: 5}


class QuestionsCfg(BaseModel):
    total: int = Field(5, ge=1, le=30)   # number of RESUME-BASED questions (section "resume_based")
    mix: dict[QuestionType, float]       # technical / scenario / behavioural mix of the resume-based questions
    max_per_skill: int = Field(2, ge=1, le=5)
    gap_questions: Literal["must_have_only", "all", "none"] = "must_have_only"
    # JD-driven sections that do not depend on the resume. 0 switches a section off.
    extra_sections: dict[Section, int] = Field(default_factory=_default_extra_sections)

    @model_validator(mode="after")
    def _extra_sections_valid(self):
        if Section.resume_based in self.extra_sections:
            raise ValueError("extra_sections must not contain 'resume_based' (its size is questions.total)")
        if any(v < 0 or v > 30 for v in self.extra_sections.values()):
            raise ValueError("extra_sections counts must be between 0 and 30")
        return self

    @property
    def grand_total(self) -> int:
        """Questions in the whole paper (resume-based + all extra sections), before must-have-gap raises."""
        return self.total + sum(self.extra_sections.values())

    @model_validator(mode="after")
    def _mix_sums_to_one(self):
        total = sum(self.mix.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"questions.mix must sum to 1.0 (got {total})")
        if any(v < 0 for v in self.mix.values()):
            raise ValueError("questions.mix values must be >= 0")
        return self


class StorageCfg(BaseModel):
    output_dir: str = "output/scaffolds"


class ReviewCfg(BaseModel):
    disclaimer: str = "AI-generated, needs human review"


class Settings(BaseModel):
    llm: LLMCfg = Field(default_factory=LLMCfg)
    workflow: WorkflowCfg = Field(default_factory=WorkflowCfg)
    input: InputCfg = Field(default_factory=InputCfg)
    experience_levels: ExperienceCfg = Field(default_factory=ExperienceCfg)
    questions: QuestionsCfg
    difficulty_matrix: dict[SkillBucket, dict[ExperienceLevel, Difficulty]]
    storage: StorageCfg = Field(default_factory=StorageCfg)
    review: ReviewCfg = Field(default_factory=ReviewCfg)

    @model_validator(mode="after")
    def _matrix_complete(self):
        for bucket in SkillBucket:
            row = self.difficulty_matrix.get(bucket)
            if row is None:
                raise ValueError(f"difficulty_matrix is missing bucket '{bucket.value}'")
            for level in ExperienceLevel:
                if level not in row:
                    raise ValueError(f"difficulty_matrix.{bucket.value} is missing level '{level.value}'")
        return self


def load_settings(path: str | Path | None = None) -> Settings:
    path = Path(path or config_path())
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return Settings.model_validate(raw.get("phase3a", raw))      # the full project config, or a file with just the phase3a settings
