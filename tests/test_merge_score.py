"""Run:  python -m pytest -q test_merge_score.py"""
import copy
import random

from scoring.merge_score import NO_EVIDENCE, run_data

CUSTOM_JD = [("Python", "python", "must_have"), ("SQL", "sql", "must_have"), ("RAG", "rag", "must_have"),
             ("LangGraph", "langgraph", "nice_to_have"), ("Docker", "docker", "nice_to_have")]


def jd_of(skills):
    return {"jd_id": "t", "role_title": "T",
            "skills": [{"raw_text": r, "standard_id": i, "standard_name": r, "importance": imp} for r, i, imp in skills]}


def item(sid, name=None, quote="evidence sentence"):
    return {"skill_id": sid, "name": name or sid, "evidence": [{"quote": quote, "section": "experience"}]}


def kw_of(hands=(), theory=(), unmapped=()):
    return {"resume": "r.pdf", "hands_on": [item(*h) if isinstance(h, tuple) else item(h) for h in hands],
            "theoretical": [item(*t) if isinstance(t, tuple) else item(t) for t in theory],
            "unmapped": list(unmapped)}


# ---- the worked example from the scoring document
def example():
    return jd_of(CUSTOM_JD), kw_of(hands=["python", "sql", "docker"], theory=["rag", "langgraph"])


def test_document_example_scores_78_18_and_must_coverage_80():
    r = run_data(*example())
    assert r["score"] == 78.18 and r["points"] == 8.6 and r["max_points"] == 11
    assert r["must_have"]["coverage_pct"] == 80.0


def test_each_skill_has_weight_credit_points_and_evidence():
    rows = {x["skill"]: x for x in run_data(*example())["skills"]}
    assert (rows["RAG"]["weight"], rows["RAG"]["credit"], rows["RAG"]["points"]) == (3, 0.4, 1.2)
    assert rows["Python"]["evidence"] == "evidence sentence" and rows["Docker"]["evidence_level"] == "hands_on"


# ---- credit rules
def test_skill_in_both_lists_gets_highest_credit_not_sum():
    jd = jd_of([("Python", "python", "must_have")])
    r = run_data(jd, kw_of(hands=["python"], theory=["python"]))
    assert r["score"] == 100.0 and r["skills"][0]["credit"] == 1.0


def test_hands_on_scores_higher_than_theoretical():
    jd = jd_of(CUSTOM_JD)
    a = run_data(jd, kw_of(hands=["python"]))["score"]
    b = run_data(jd, kw_of(theory=["python"]))["score"]
    assert a > b > 0


def test_not_found_is_zero_and_says_no_evidence_found():
    r = run_data(jd_of([("Python", "python", "must_have")]), kw_of())
    assert r["score"] == 0 and r["skills"][0]["evidence"] == NO_EVIDENCE
    assert r["must_have"]["missing"] == ["Python"]          # shown, never an auto-reject


# ---- strict matching: related is not same
def test_related_skills_are_not_matched():
    jd = jd_of([("SQL", "sql", "must_have"), ("LangGraph", "langgraph", "must_have")])
    r = run_data(jd, kw_of(hands=["postgresql", "langchain"]))
    assert r["score"] == 0


def test_loose_text_overlap_is_listed_but_not_scored():
    jd = jd_of([("Machine Learning models", "Unmapped", "must_have")])
    r = run_data(jd, kw_of(hands=[("machine_learning", "Machine Learning")]))
    assert r["score"] == 0 and r["skills"][0]["possible_matches"] == ["Machine Learning"]


def test_unmapped_jd_skill_matches_on_exact_text_only():
    jd = jd_of([("Delta Lake", "Unmapped", "must_have")])
    assert run_data(jd, kw_of(hands=[("Unmapped", "Delta Lake")]))["score"] == 100.0


def test_composite_jd_line_is_one_skill_any_part_satisfies_it():
    jd = jd_of([("TensorFlow/Keras", "Unmapped", "must_have"), ("Python", "python", "must_have")])
    r = run_data(jd, kw_of(hands=["python", ("keras", "Keras")]))
    assert r["max_points"] == 6 and r["score"] == 100.0     # still 2 skills, denominator unchanged


# ---- invariance
def test_resume_length_does_not_change_score():
    jd = jd_of(CUSTOM_JD)
    base = kw_of(hands=["python", "sql"])
    padded = kw_of(hands=["python", "sql"] + [f"extra{i}" for i in range(40)])
    assert run_data(jd, base)["score"] == run_data(jd, padded)["score"]


def test_order_and_duplicates_do_not_change_score():
    jd, kw = example()
    base = run_data(jd, kw)["score"]
    for seed in range(5):
        j, k = copy.deepcopy(jd), copy.deepcopy(kw)
        random.Random(seed).shuffle(j["skills"])
        random.Random(seed).shuffle(k["hands_on"])
        j["skills"] += copy.deepcopy(j["skills"][:3])        # duplicate JD lines
        k["hands_on"] += copy.deepcopy(k["hands_on"])        # duplicate resume items
        assert run_data(j, k)["score"] == base


def test_same_input_same_output():
    jd, kw = example()
    assert run_data(jd, kw) == run_data(jd, kw)


def test_skill_listed_as_must_and_nice_counts_once_as_must():
    jd = jd_of([("Python", "python", "nice_to_have"), ("Python", "python", "must_have")])
    r = run_data(jd, kw_of(hands=["python"]))
    assert r["max_points"] == 3 and len(r["skills"]) == 1


# ---- config / edge cases
def test_weights_and_credits_are_tunable():
    jd, kw = example()
    r = run_data(jd, kw, weights={"must_have": 2}, credits={"theoretical": 0.5})
    assert r["config"]["weights"]["must_have"] == 2 and r["score"] != 78.18


def test_empty_jd_scores_zero_with_warning():
    r = run_data(jd_of([]), kw_of(hands=["python"]))
    assert r["score"] == 0 and r["warnings"] and r["must_have"]["coverage_pct"] is None


def test_bad_inputs_raise_clear_errors():
    import pytest
    with pytest.raises(ValueError):
        run_data({"nope": 1}, kw_of())
    with pytest.raises(ValueError):
        run_data(jd_of([]), {"resume": "x"})
