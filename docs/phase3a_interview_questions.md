# Phase 3A: interview questions (per candidate)

Runs after the HR step, on the **same JD and resumes** the workflow already processed.
It is optional (`PipelineRequest(interview=True)`, `--interview`, or the buttons in app section 6).

```
Phase 1 skills + JD text  |  Phase 2 keywords + layout + resume text (standardised, e-mail/phone masked)
  -> code: reshape them into evidence (NO extraction, NO model call): JD skills, resume quotes, project names,
           years per skill and overall years from the dates Phase 2 already parsed
  -> code: check every quote against the texts (drop unverifiable ones), classify, plan the slots
  -> [agent question-writer]     wording for each planned slot
  -> code: validate each question; only failed slots are regenerated (max 2 extra rounds)
  -> save output/scaffolds/<id>.json    (status pending_human_review)
```

## What code decides, what the model decides

| Code (`classify.py`, `planner.py`, `validators.py`, `evidence.py`) | Model |
|---|---|
| bucket (project_hands_on / listed_only / gap), experience level, difficulty | nothing about evidence: skills, quotes and dates come from Phase 1 / Phase 2 |
| which skills get questions, how many, technical / scenario / behavioural mix | the wording of each question |
| every must-have gap gets a question | the question text and answer keywords (then checked) |

Level = years on THAT skill; if the resume gives none, overall experience; if neither, unknown (medium).
Thresholds, question count and mix, and the difficulty matrix are in `config/config.yaml` (section `phase3a`).

## Inputs, and why a candidate can be skipped

| Situation | Result |
|---|---|
| resume came from a file (PDF/DOCX) | normal |
| only a Phase 2 keywords json was given | no resume text or layout, so no interview questions; reported in `interview_errors` |
| Phase 1 json has no skills | reported, no LLM call is made |
| Phase 1 json has no usable `jd_text` (missing, or only a placeholder like `samples/jd/sample_jd.json`) | reported, no LLM call is made |
| Phase 2 marked the resume for review (for example OCR) | questions are written, with a warning in the scaffold |
| one candidate fails (rate limit, bad output) | recorded for that candidate only; the others continue |

## Safety nets (unchanged from 3A)

- Every output says "AI-generated, needs human review" and is saved as `pending_human_review`.
- A skill Phase 2 did not find is a `gap`; the resume text is not searched a second time (it would contradict the score).
- Unverifiable quotes never raise difficulty; unverified evidence is dropped and the skill falls back to `gap`.
- Years on a skill come only from dated Phase 2 evidence (overlaps counted once); undated evidence gives no years, so the level falls back to overall experience, then to medium. Phase 2 keeps at most `evidence_per_skill` (3) quotes per skill, so years can be under-counted, never over-counted.
- Overall experience = the larger of the "N years of experience" statement in the summary and the work-experience dates.
- Skill years larger than the candidate's overall years are ignored.
- Names are not masked (Phase 2 already masked e-mail, phone and links).
- Too-long input is rejected, never silently cut.

## Settings and limits

- `llm.max_output_tokens` (8000): raise it if you see "output was cut off".
- `workflow.max_parallel_candidates` (1): Groq free-tier limits are tight for this model. Raise only if yours allow it.
- Groq key: the same `GROQ_API_KEY` in `.env`.

## The 4-section interview paper

Each candidate now gets one paper of 20 questions in 4 sections (sizes in `config/config.yaml` (section `phase3a`)):

| Section | Count setting | Built from | Question type | Evidence quote |
|---|---|---|---|---|
| `basic` | `questions.extra_sections.basic` (5) | JD skills only, difficulty easy | technical | JD |
| `resume_based` | `questions.total` (5) | resume evidence (project_hands_on / listed_only / gap, the old behaviour) | technical / scenario / behavioural | resume, or JD for a gap |
| `scenario_case` | `questions.extra_sections.scenario_case` (5) | JD skills only; situational cases that are hard to guess from the resume | scenario | JD |
| `coding` | `questions.extra_sections.coding` (5) | JD skills only; short coding / logic problems | technical | JD |

* `basic`, `scenario_case` and `coding` slots carry no resume data to the model, so those questions never depend on the candidate's projects.
* Generation runs one LLM call per section (4 calls); failed slots are regenerated section by section.
* Set a section's count to `0` to switch it off. Slot ids run 1..N in the order basic, resume_based, scenario_case, coding.
