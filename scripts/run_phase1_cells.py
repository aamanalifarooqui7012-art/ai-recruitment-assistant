# %% [markdown]
# # Phase 1 - run step by step in VS Code (Run Cell / Shift+Enter)
# Flow: raw JD -> LLM extract -> normalise (code) -> LLM questions -> save
# Before running: put your Groq key in the `.env` file (GROQ_API_KEY=...).

# %% Setup - imports
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # repo/src on the import path
# run VS Code with the repo root folder open: input/jd.txt and output/ are relative to it

from phase1.extract import extract_skills
from phase1.llm_client import get_client
from phase1.pipeline import run_phase1
from phase1.questions import generate_questions
from phase1.taxonomy import normalise_skills

# %% Check 1 - is the API key found? (key is not printed)
get_client()
print("API key found. Ready.")

# %% Input - paste the JD in input/jd.txt, save, then run this cell
JD_PATH = Path("input/jd.txt")
JD_PATH.parent.mkdir(exist_ok=True)
if not JD_PATH.exists():
    JD_PATH.write_text("", encoding="utf-8")

JD_TEXT = JD_PATH.read_text(encoding="utf-8").strip()
if not JD_TEXT:
    raise ValueError("input/jd.txt is empty. Paste the JD there, press Ctrl+S, run this cell again.")
print(f"JD loaded: {len(JD_TEXT)} characters")

# %% Step A - LLM extracts role, experience, must-have / nice-to-have skills
extraction = extract_skills(JD_TEXT)
print("Role:", extraction.role_title, "| Overall experience:", extraction.overall_experience)
for s in extraction.skills:
    print(f"- {s.raw_text:25} {s.category.value:11} {s.importance.value:13} {s.required_experience}")

# %% Step B - Code normalises skills with the taxonomy (React js -> react)
skills = normalise_skills(extraction.skills)
for s in skills:
    flag = "  <-- review" if s.needs_review else ""
    print(f"{s.raw_text:25} -> {s.standard_id:15} {s.importance.value:13} {s.match_type}{flag}")

# %% Step C - LLM writes screening questions from must-have skills + experience
questions = generate_questions(extraction.role_title, extraction.overall_experience, skills)
for i, q in enumerate(questions, start=1):
    print(f"\nQ{i} [{q.skill_id}] {q.question}")
    print("   keywords:", ", ".join(q.keywords))
    print("   answer  :", q.model_answer)

# %% Step D - Whole flow in one go + save to output/results/<jd_id>.json
result = run_phase1(JD_TEXT)
print("Saved:", result.jd_id, "| questions:", len(result.questions))
print("Warnings:", result.warnings)
print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False)[:1500])
