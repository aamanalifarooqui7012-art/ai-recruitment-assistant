# AI Recruitment Assistant

One repo for the whole flow, run as a **Microsoft Agent Framework (MAF)** workflow:

```
 JD text ──► Phase 1 ──► skills (must-have / good-to-have) + HR screening questions
                                   │
 Resumes ──► Phase 2 ──► hands-on / theoretical keywords, each with evidence
                                   │
                         Phase 2.5 weighted score /100 + must-have coverage
                                   │
                         HR step: marks per candidate (Met / Partly met / Not met)
                                   │
 same JD + same resumes ──► Phase 3A  interview questions per candidate, built on the Phase 1 skills + Phase 2 keywords (optional, kept apart from the HR step)
                                   │
 interview transcript    ──► Phase 3B  skill ratings with verified quotes + recommendation, against the SAME Phase 1 skills
```

> AI-generated, needs human review. The score only advises; a person decides.

## Project structure

```
ai-recruitment-assistant/
├── README.md  .gitignore  .env.example  requirements.txt  pyproject.toml  pytest.ini
├── config/
│   ├── config.yaml              the ONE config file: sections phase1, phase2, scoring, phase3a, phase3b
│   └── skills_taxonomy.yaml     ONE taxonomy shared by all phases (data, not settings)
├── src/
│   ├── phase1/                  JD -> skills + HR questions
│   ├── resume_std/              resume -> layout -> PII masking -> keywords + evidence
│   ├── scoring/                 merge_score.py (weighted score), hr_screening.py
│   ├── evaluation/              Phase 3B: transcript + Phase 1 skills -> ratings -> recommendation (Groq over HTTP)
│   ├── interview/               Phase 3A: Phase 1 skills + Phase 2 keywords -> level -> plan -> interview questions (1 MAF agent, wording only)
│   └── maf/                     Microsoft Agent Framework workflow
│       ├── models.py            request / stage / result messages
│       ├── executors.py         JdExecutor, ResumeExecutor, ScoreExecutor, HrExecutor, InterviewExecutor
│       ├── workflow.py          JD -> resumes -> score -> HR -> interview (WorkflowBuilder)
│       ├── evaluation.py        Phase 3B workflow: prepare -> analyse -> merge -> recommend (second WorkflowBuilder)
│       └── run.py               command line
├── ui/
│   ├── phase_app.py             combined Streamlit app (runs the MAF workflow)
│   ├── merge_app.py             score uploaded JSON only (no LLM)
│   └── phase1_app.py            the original Phase 1 paste-box app
├── scripts/
│   ├── run_workflow.py          whole workflow from the command line (add --interview for Phase 3A)
│   ├── run_interview.py         Phase 3A from SAVED Phase 1 + Phase 2 files
│   ├── resume_cli.py            Phase 2 batch run          (was run.py)
│   ├── resume_console.py        Phase 2 interactive menu   (was app.py)
│   └── run_phase1_cells.py      Phase 1 step by step in VS Code
├── tools/                       make_samples.py (dummy resumes), evaluate.py
├── tests/                       pytest (no internet, no API key needed)
├── samples/                     one sample JD json + 2 synthetic candidates
├── docs/                        scoring_plan.md, phase2_resume_standardiser.md
└── input/                       your own jd.txt goes here (git-ignored)
```

## The MAF workflow

| Step (Executor) | Does | LLM? |
|---|---|---|
| `JdExecutor` | `phase1.run_phase1` or a ready Phase 1 json | yes (skipped with json) |
| `ResumeExecutor` | `resume_std.process_file` per resume, or ready keywords json | yes (skipped with json) |
| `ScoreExecutor` | `scoring.run_data` for every resume, ranked | no |
| `HrExecutor` | screening questions + a blank mark sheet per candidate | no |
| `phase3a_*` (prepare, load_evidence, verify_classify, plan, generate, validate, finalize) | Phase 3A: interview questions per candidate (only when `interview=True`); `generate` and `validate` loop to redo only the failed questions | yes (1 agent, only to word the questions) |
| `phase3b_*` (prepare, analyse, merge, recommend) | Phase 3B: interview transcript -> skill ratings -> recommendation (own entry, needs a transcript) | yes |

**There is exactly ONE workflow** (`src/maf/workflow.py`, one `WorkflowBuilder`). A `Router` start step sends the request to the right branch: a `PipelineRequest` runs Phase 1 -> 2 -> 2.5 -> HR (-> 3A), an `InterviewJob` runs Phase 3A alone for candidates already processed, an `EvaluationRequest` runs Phase 3B. All Phase 3A candidates travel through the steps together, so one failing candidate never stops the others. `build_workflow()` builds it. Phase 1 and Phase 2 code is unchanged; the executors only call it. Phase 3A reuses the skills Phase 1 found and the keywords, layout and text Phase 2 produced, so **nothing is entered or extracted twice**. Blocking LLM calls run in worker threads.
Scoring stays plain code on purpose: the same input always gives the same score.

```python
from maf import PipelineRequest, run_pipeline
result = run_pipeline(PipelineRequest(jd_text="...", resume_files=["a.pdf", "b.docx"]))
result.scores        # ranked, each with per-skill weight / credit / evidence
result.hr            # HR screening: questions + mark sheet per candidate
result.interviews    # Phase 3A: {resume name: interview scaffold}   (interview=True)
```

## Phase 3B in short

Runs after the interview, once a transcript exists, so it is a second MAF workflow (`maf.run_evaluation`) rather than one more step of the first. The JD text and the skills are taken from the Phase 1 result already in the app; the app has no second JD or skills box. Per candidate: upload a `.vtt` / `.docx` / `.txt` transcript, confirm who is Interviewer and who is Candidate, press **Evaluate interview**. The model rates each skill; code checks every quote against the real candidate lines, downgrades a rating whose quote is invented, and works out "not covered" itself. The manager then records Agree / Disagree and can download the report json. Sample transcripts: `samples/transcripts/`.

## HR screening questions vs interview questions (kept separate on purpose)

| | HR screening (Phase 1, `result.hr`) | Interview questions (Phase 3A, `result.interviews`) |
|---|---|---|
| Purpose | quick first call: does the person know the basics of the JD skills? | technical interview: probe what THIS person claims |
| Written from | the JD only | the Phase 1 skills **and** that candidate's Phase 2 keywords / resume evidence |
| Same for everyone? | yes, one set for every candidate | no, one set per candidate |
| Difficulty | one level | follows the candidate's years on each skill (fresher / mid / senior) |
| Answer side | keywords + model answer, HR marks Met / Partly met / Not met | good-answer keywords |
| Where | app section 5, config `config.yaml -> phase1.screening` | app section 6, config `config.yaml -> phase3a` |

Neither one reads or changes the other. With or without Phase 3A the HR step output is identical (a test checks this).

## Phase 3A in short

For every JD skill, code decides the bucket and difficulty (the model only writes the wording):

| Resume shows | Bucket | Question |
|---|---|---|
| the skill used in a project / job | `project_hands_on` | about THEIR project; **hard** if 3+ years on that skill (or overall, when the resume gives no skill years), else medium |
| the skill only listed | `listed_only` | medium concept question, never assumes real use |
| nothing | `gap` | easy fundamentals question (must-have gaps are always covered) |

**Phase 3A does no extraction of its own.** The JD skills are the Phase 1 skills; the bucket comes from the Phase 2 keywords (`hands_on` -> project, `theoretical` -> listed only, not found -> gap), so the interview plan can never disagree with the Phase 2.5 score. Years on a skill and overall years are date arithmetic on the dates Phase 2 parsed. The model is only asked to word the questions. Every resume quote is still checked against the real text and anything unverifiable is dropped. Details: `docs/phase3a_interview_questions.md`.

## Setup (Windows PowerShell)

```powershell
git clone <your-repo-url>
cd ai-recruitment-assistant
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
copy .env.example .env                # then open .env and paste your GROQ_API_KEY
```

Python 3.10+. Scanned PDFs also need the [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) program (optional).
Always run commands from the repo root.

## Run

```powershell
python -m streamlit run ui/phase_app.py            # everything in one app
python -m streamlit run ui/merge_app.py            # score uploaded JSON only
python -m streamlit run ui/phase1_app.py           # Phase 1 only

python scripts/run_workflow.py --jd input/jd.txt --resumes resumes/ --out results/
python scripts/run_workflow.py --jd-json samples/jd/sample_jd.json --keywords samples/keywords   # no LLM at all
python scripts/run_workflow.py --jd input/jd.txt --resumes resumes/ --interview --interview-top 3   # + Phase 3A for the 3 best
python scripts/run_interview.py --jd-json output/results/jd-1234abcd.json --layout out/workflow/layout   # Phase 3A from saved files (reads out/workflow/keywords too)

python scripts/resume_console.py                   # Phase 2 menu
python scripts/resume_cli.py resumes/ --out out    # Phase 2 batch
python tools/make_samples.py samples               # dummy resumes + gold labels
python tools/evaluate.py samples/gold out
```

## Tests

```powershell
python -m pytest -q
```

Tests are hermetic: the Groq call is replaced by a fake (Phase 3A is also tested through the real MAF client against a local fake Groq server), so they need no key, no internet and cost nothing.
To test against the real LLM: `$env:RUN_LIVE_LLM_TESTS = "1"; python -m pytest -q`.

## Privacy: what must never be pushed

`.gitignore` already blocks these. Check `git status` before every commit.

- `.env` (your API key). Commit `.env.example` only.
- Real resumes and anything made from them (`*.pdf`, `*.docx`, `out/`, `.cache/`, `output/`): they hold personal data.
- `samples/` is synthetic on purpose. Do not add real candidates there.
- `samples/jd/sample_jd.json` has its JD text removed.

## Push to GitHub

```powershell
git init
git add .
git status                      # must NOT list .env, resumes, out/, .cache/, output/
git commit -m "Initial commit: Phase 1 + Phase 2 + scoring as a MAF workflow"
git branch -M main
git remote add origin https://github.com/<username>/<repo>.git
git push -u origin main
```

If a secret was ever committed, rotate the key first; deleting the file later does not remove it from history.

## Phase 3A integration (what changed)

Phase 3A used to be a separate terminal app that asked you to type or upload a JD and a resume. It is now part of this repo:

| Before (`interview_3a`) | Now |
|---|---|
| asked for the JD and the resume again | uses the JD from Phase 1 and the resume from Phase 2 of the same run |
| `interview_scaffold/` package | `src/interview/` (same logic: evidence, classify, planner, validators, prompts, retry) |
| its own `GroqLLMClient` (raw `openai`) | `MafLLMClient`: two MAF `Agent`s, `evidence-extractor` and `question-writer` |
| its own small alias table | the one `config/skills_taxonomy.yaml`, so a skill has the same id in all phases |
| `config/settings.yaml` | `config/config.yaml` (section `phase3a`) |
| own MAF workflow | merged into the one project workflow (`src/maf/workflow.py`): the same steps and the same retry edge, as `phase3a_*` executors |
| `run.py` prompts, `filetext.py` upload reader | removed (no second input); `scripts/run_interview.py` reads saved Phase 1 + 2 files |
| optional FastAPI `api.py` | kept, optional (`pip install fastapi "uvicorn[standard]" httpx`) |

Phase 1, Phase 2 and scoring logic is unchanged; only where they read their settings from changed (one file, one section each).
Also fixed: the old 3A workflow used `WorkflowBuilder.with_name()`, which does not exist in MAF 1.x; it now passes `name=`.

## What changed from the three old folders

Nothing was removed. Logic in Phase 1 and Phase 2 is untouched; only paths moved.

| Old | New |
|---|---|
| `PROJECT PHASE 1/phase1/` | `src/phase1/` (`ROOT` now the repo root) |
| `ai_reruitment/resume_std/` | `src/resume_std/` (config and taxonomy paths follow) |
| `merge/merge_score.py` | `src/scoring/merge_score.py` |
| `PROJECT PHASE 1/config/*` , `ai_reruitment/config.yaml` , two taxonomy copies | `config/` (one taxonomy, they were identical) |
| `PROJECT PHASE 1/app.py` | `ui/phase1_app.py` |
| `ai_reruitment/app.py` , `run.py` | `scripts/resume_console.py` , `scripts/resume_cli.py` |
| `phase_app.py` , `merge/merge_app.py` | `ui/phase_app.py` (now runs the MAF workflow) , `ui/merge_app.py` |
| `tests/` of each phase | `tests/test_phase1_*`, `tests/test_phase2_*` |
| `output/results/jd-*.json` (Phase 1 results) | `output/results/` at the repo root (git-ignored) |

Also: `groq` added to `requirements.txt` (Phase 2 imports it but it was missing); tests no longer call the live LLM by default.
Old results you want to keep (for example `output/results/*.json`) can be copied into the same folders here.
