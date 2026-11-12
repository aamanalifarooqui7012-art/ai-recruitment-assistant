from resume_std.config import load_config
from resume_std.keywords import extract_keywords
from resume_std.pipeline import process_file
from resume_std.taxonomy import get_taxonomy

CFG = load_config()
TAX = get_taxonomy(CFG)


def blk(i, text, heading=False):
    return {"id": i, "text": text, "page": 1, "is_bullet": False, "is_heading": heading,
            "label_source": "heading" if heading else "section_context", "confidence": 0.9, "flags": [], "links": []}


def layout(*sections):
    return {"source_file": "t.pdf", "sections": [
        {"name": n, "entries": [{"dates": None, "blocks": bs} for bs in entries]} for n, entries in sections]}


class FakeLLM:
    """Stands in for a real model so the safety net can be tested without an API key."""
    def __init__(self, items):
        self.items = items

    def extract(self, kind, text):
        return [i for i in self.items if kind in i.get("kinds", ("hands_on", "theory"))]


def ids(col):
    return {k["skill_id"] for k in col}


def test_spelling_variants_normalise_to_one_id():
    for v in ["ReactJS", "react js", "React.js", "React-JS"]:
        assert TAX.canonical(v) == "react"
    assert TAX.canonical("Node.js") == TAX.canonical("NodeJS") == "nodejs"
    assert TAX.canonical("Zzzlang") is None


def test_scanner_guards_common_words_and_substrings():
    found = lambda t: {m.skill_id for m in TAX.scan(t)}
    assert found("Wrote JavaScript services") == {"javascript"}                # not "java"
    assert found("Go to market strategy and the rest of the team") == set()    # plain English
    assert {"r", "golang"} <= found("Languages & Tools: Python, R, Go, SQL")   # list context
    assert "golang" in found("Experience with Go programming language")        # cue word
    assert found("see https://github.com/someone/repo") == set()               # skill names inside URLs
    assert "nextjs" not in found("Forecast next-day prices with Pandas, XGBoost and NumPy")   # lowercase plain word


def test_hands_on_vs_theory_and_dedupe():
    lay = layout(("experience", [[blk(1, "Built APIs with Python and Kafka for payments.")]]),
                 ("skills", [[blk(2, "Python, Docker, Git, SQL, AWS")]]))
    kw = extract_keywords(lay, CFG)
    assert {"python", "kafka"} <= ids(kw["hands_on"])
    assert {"docker", "git", "sql", "aws"} <= ids(kw["theoretical"])
    assert "python" not in ids(kw["theoretical"])                        # used in work: hands-on only
    assert next(k for k in kw["hands_on"] if k["skill_id"] == "python")["also_in_theory"]


def test_in_progress_project_goes_to_theory():
    lay = layout(("projects", [[blk(1, "GenAI Skill-Building (In Progress)"), blk(2, "Using Docker and FastAPI to deploy models.")],
                               [blk(3, "Stock Predictor"), blk(4, "Built with Pandas and XGBoost.")]]))
    kw = extract_keywords(lay, CFG)
    assert {"docker", "fastapi"} <= ids(kw["theoretical"]) and not {"docker", "fastapi"} & ids(kw["hands_on"])
    assert {"pandas", "xgboost"} <= ids(kw["hands_on"])


def test_llm_fabricated_quote_is_rejected_and_missed_skill_is_added_by_code():
    lay = layout(("experience", [[blk(1, "Built APIs with Python and Kafka for payments.")]]))
    llm = FakeLLM([
        {"keyword": "Python", "quote": "Built APIs with Python and Kafka for payments."},      # valid
        {"keyword": "Terraform", "quote": "Provisioned cloud with Terraform at scale."},      # quote invented
        {"keyword": "Docker", "quote": "Built APIs with Python and Kafka for payments."},      # keyword not in quote
    ])                                                                                        # Kafka forgotten
    kw = extract_keywords(lay, CFG, llm)
    assert ids(kw["hands_on"]) == {"python", "kafka"}
    assert {r["reason"] for r in kw["rejected_llm_items"]} == {"quote_not_in_resume", "keyword_not_in_quote"}
    assert [c["skill_id"] for c in kw["code_added"]] == ["kafka"]
    assert next(k for k in kw["hands_on"] if k["skill_id"] == "python")["sources"] == ["both"]
    assert kw["stats"]["code_found_also_by_llm"] == "1/2"


def test_unknown_tool_kept_as_unmapped_and_review_list_works():
    lay = layout(("projects", [[blk(1, "Clustered users with DBSCAN and tuned RandomizedSearchCV.")]]))
    kw = extract_keywords(lay, CFG, FakeLLM([{"keyword": "DBSCAN", "quote": "Clustered users with DBSCAN and tuned RandomizedSearchCV."}]))
    assert any(k["skill_id"] == "Unmapped" and k["name"] == "DBSCAN" for k in kw["hands_on"])
    assert [u["keyword"] for u in kw["unmapped"]] == ["DBSCAN"]
    assert "RandomizedSearchCV" in [c["token"] for c in kw["review_candidates"]]      # nobody extracted it


def test_works_on_real_pipeline_output():
    from pathlib import Path
    import subprocess, sys, tempfile
    d = Path(tempfile.mkdtemp())
    subprocess.run([sys.executable, str(Path(__file__).resolve().parent.parent / "tools" / "make_samples.py"), str(d)], check=True)
    r = process_file(str(d / "classic_single.pdf"), CFG)
    kw = extract_keywords(r["layout"], CFG)
    assert {"python", "fastapi", "react"} <= ids(kw["hands_on"]) and "docker" in ids(kw["theoretical"])
