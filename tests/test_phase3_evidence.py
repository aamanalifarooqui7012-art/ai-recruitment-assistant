from interview.evidence import verify_extraction
from interview.schemas import (
    ExtractionResult, Importance, JDSkill, OverallExperience, ProjectUse, SkillEvidence,
)
from interview.taxonomy import SkillTaxonomy

JD = "We need 3+ years of Python experience for building APIs."
RESUME = "Summary: 5 years of experience building APIs.\nBuilt payment APIs in Python and FastAPI for Acme."


def _extraction(overall, years=None, quote="Built payment APIs in Python and FastAPI for Acme."):
    return ExtractionResult(
        jd_skills=[JDSkill(skill_id="Python", name="Python", importance=Importance.must_have,
                           required_experience="3+ years", jd_quote="3+ years of Python experience for building APIs")],
        evidence=[SkillEvidence(skill_id="python", listed_quote=None, years_on_skill=years, years_basis="explicit",
                                project_uses=[ProjectUse(project_name="Acme", what_candidate_did="x", resume_quote=quote)])],
        overall_experience=overall,
    )


def _verify(extraction):
    return verify_extraction(extraction, JD, RESUME, SkillTaxonomy.from_yaml(), 15)


def test_overall_experience_with_real_quote_is_kept():
    oe = OverallExperience(years=5, basis="explicit", quote="5 years of experience building APIs")
    assert _verify(_extraction(oe)).overall_years == 5


def test_overall_experience_with_fake_quote_is_dropped():
    oe = OverallExperience(years=12, basis="explicit", quote="12 years at Google")
    v = _verify(_extraction(oe))
    assert v.overall_years is None and any("overall experience" in w for w in v.warnings)


def test_fresher_zero_years_needs_no_quote():
    assert _verify(_extraction(OverallExperience(years=0, basis="explicit", quote=None))).overall_years == 0


def test_skill_years_larger_than_overall_are_ignored():
    oe = OverallExperience(years=5, basis="explicit", quote="5 years of experience building APIs")
    v = _verify(_extraction(oe, years=9))
    assert v.evidence["python"].years_on_skill is None and any("more than" in w for w in v.warnings)


def test_fabricated_project_quote_is_dropped():
    oe = OverallExperience(years=5, basis="explicit", quote="5 years of experience building APIs")
    v = _verify(_extraction(oe, quote="Led a 50-person Kafka migration"))
    assert v.evidence["python"].project_uses == []


def _many(n_must, n_nice):
    skills = [JDSkill(skill_id=f"nice{i}", name=f"Nice{i}", importance=Importance.nice_to_have,
                      required_experience="Not specified", jd_quote=f"nice to have skill number {i} here")
              for i in range(n_nice)]
    skills += [JDSkill(skill_id=f"must{i}", name=f"Must{i}", importance=Importance.must_have,
                       required_experience="Not specified", jd_quote=f"must have skill number {i} here")
               for i in range(n_must)]
    return ExtractionResult(jd_skills=skills, evidence=[],
                            overall_experience=OverallExperience(years=None, basis="unknown", quote=None))


def _jd_for(ext):
    return " ".join(s.jd_quote for s in ext.jd_skills)


def test_no_limit_keeps_every_skill():
    ext = _many(8, 14)  # 22 skills, like the real JD
    v = verify_extraction(ext, _jd_for(ext), "resume text", SkillTaxonomy.from_yaml(), None)
    assert len(v.skills) == 22 and not v.warnings


def test_when_a_limit_is_set_must_haves_are_kept_first():
    ext = _many(3, 5)  # nice-to-haves come first in the list
    v = verify_extraction(ext, _jd_for(ext), "resume text", SkillTaxonomy.from_yaml(), 4)
    assert len(v.skills) == 4
    assert sum(1 for s in v.skills if s.importance == Importance.must_have) == 3
