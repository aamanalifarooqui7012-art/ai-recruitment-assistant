"""Phase 1 flow: raw JD -> LLM extract -> normalise (code) -> LLM questions -> save."""
import hashlib
import json

from .extract import extract_skills
from .questions import generate_questions
from .schemas import Phase1Result
from .settings import SETTINGS, path
from .taxonomy import UNMAPPED, normalise_skills, save_unmapped


def run_phase1(jd_text: str, jd_id: str | None = None, save: bool = True) -> Phase1Result:
    jd_text = jd_text.strip()
    jd_id = jd_id or "jd-" + hashlib.sha1(jd_text.encode("utf-8")).hexdigest()[:8]

    extraction = extract_skills(jd_text)                      # LLM
    skills = normalise_skills(extraction.skills)              # code
    questions = generate_questions(                           # LLM
        extraction.role_title, extraction.overall_experience, skills
    )

    warnings = []
    want = SETTINGS["screening"]["questions_count"]
    if not skills:
        warnings.append("No skills were found in this JD.")
    if len(questions) != want:
        warnings.append(f"Expected {want} questions, got {len(questions)}. HR please review.")
    questions = questions[:want]

    if save:
        unmapped = save_unmapped(skills, jd_id)
    else:
        unmapped = sorted({s.raw_text for s in skills if s.standard_id == UNMAPPED})
    if unmapped:
        warnings.append("Unmapped skills (add to taxonomy later): " + ", ".join(unmapped))

    result = Phase1Result(
        jd_id=jd_id, jd_text=jd_text,
        role_title=extraction.role_title, overall_experience=extraction.overall_experience,
        skills=skills, unmapped=unmapped, questions=questions, warnings=warnings,
        model_name=SETTINGS["llm"]["model"],
    )

    if save:
        out = path("results_dir")
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{jd_id}.json").write_text(
            json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    return result
