"""Runs the whole Phase 1 flow with a fake LLM (no API key, no internet)."""
import phase1.extract as extract_mod
import phase1.questions as questions_mod
from phase1.pipeline import run_phase1
from phase1.schemas import GeneratedQuestion, JDExtraction, QuestionSet


def fake_llm(system, user, model_cls):
    if model_cls is JDExtraction:
        return JDExtraction.model_validate({
            "role_title": "Python Developer",
            "overall_experience": "3+ years",
            "skills": [
                {"raw_text": "Python", "category": "language", "importance": "Must have",
                 "required_experience": "3+ years", "evidence_quote": "Python, 3+ years"},
                {"raw_text": "React js", "category": "Framework", "importance": "must_have",
                 "required_experience": None, "evidence_quote": "React js"},
                {"raw_text": "AWS", "category": "Tool", "importance": "nice_to_have",
                 "evidence_quote": "AWS is a plus"},
                {"raw_text": "Zzzlang", "category": "Language", "importance": "nice_to_have"},
            ],
        })
    return QuestionSet(questions=[
        GeneratedQuestion(skill_id="python", question=f"Q{i}", keywords=["a", "b"], model_answer="x")
        for i in range(7)
    ])


def test_full_flow_with_fake_llm(monkeypatch):
    monkeypatch.setattr(extract_mod, "call_json", fake_llm)
    monkeypatch.setattr(questions_mod, "call_json", fake_llm)

    res = run_phase1("Python developer. React js needed. AWS plus.", save=False)

    assert res.role_title == "Python Developer"
    ids = {s.raw_text: s.standard_id for s in res.skills}
    assert ids == {"Python": "python", "React js": "react", "AWS": "aws", "Zzzlang": "Unmapped"}
    assert res.unmapped == ["Zzzlang"]
    assert len(res.questions) == 5            # trimmed to settings value
    assert res.disclaimer == "AI-generated, needs human review"
    assert any("Zzzlang" in w for w in res.warnings)
