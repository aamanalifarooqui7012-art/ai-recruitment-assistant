"""Code step: decide bucket, experience level and difficulty per skill.
This is the rule the LLM is NOT allowed to decide.

Level rule:
  1. years on THIS skill (if the resume gives them)
  2. else the candidate's overall experience
  3. else unknown -> medium difficulty
"""
from .config import Settings
from .evidence import VerifiedExtraction
from .schemas import ExperienceLevel, LevelBasis, SkillBucket, SkillProfile


def level_from_years(years: float | None, cfg: Settings) -> ExperienceLevel:
    if years is None:
        return ExperienceLevel.unknown
    if years >= cfg.experience_levels.senior_from_years:
        return ExperienceLevel.senior
    if years >= cfg.experience_levels.mid_from_years:
        return ExperienceLevel.mid
    return ExperienceLevel.fresher


def resolve_level(
    skill_years: float | None, overall_years: float | None, cfg: Settings
) -> tuple[ExperienceLevel, LevelBasis]:
    if skill_years is not None:
        return level_from_years(skill_years, cfg), LevelBasis.skill_years
    if overall_years is not None:
        return level_from_years(overall_years, cfg), LevelBasis.overall_years
    return ExperienceLevel.unknown, LevelBasis.unknown


def classify_skills(verified: VerifiedExtraction, cfg: Settings) -> list[SkillProfile]:
    profiles: list[SkillProfile] = []
    for order, skill in enumerate(verified.skills):
        ev = verified.evidence.get(skill.skill_id)
        if ev and ev.project_uses:
            bucket = SkillBucket.project_hands_on
        elif ev and ev.listed_quote:
            bucket = SkillBucket.listed_only
        else:
            bucket = SkillBucket.gap

        if bucket == SkillBucket.gap:  # no evidence of the skill -> no experience level to speak of
            years, level, basis = None, ExperienceLevel.unknown, LevelBasis.unknown
        else:
            years = ev.years_on_skill if ev else None
            level, basis = resolve_level(years, verified.overall_years, cfg)

        profiles.append(
            SkillProfile(
                skill_id=skill.skill_id,
                name=skill.name,
                importance=skill.importance,
                jd_order=order,
                bucket=bucket,
                years_on_skill=years,
                experience_level=level,
                level_basis=basis,
                difficulty=cfg.difficulty_matrix[bucket][level],
                jd_quote=skill.jd_quote,
                required_experience=skill.required_experience,
                projects=list(ev.project_uses) if ev else [],
                listed_quote=ev.listed_quote if ev else None,
            )
        )
    return profiles
