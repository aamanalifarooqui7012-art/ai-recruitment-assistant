# Scoring plan (Phase 2.5)

Implemented in `src/scoring/merge_score.py`. Numbers live in `config/config.yaml` (section `scoring`).

## Idea
Do not only count common skills. Ask how well the candidate satisfies the JD, using two facts per JD skill:

| Fact | Value |
|---|---|
| How important is the skill? | must-have = weight 3, good-to-have = weight 1 |
| What proof does the resume give? | hands-on = credit 1.0, theoretical = credit 0.4, not found = 0 |

A skill found in both lists gets the **highest** credit only (1.0, never 1.4).

## Formula
`score = 100 x sum(weight x credit) / sum(all weights)`

The denominator comes from the JD only, so listing 40 skills never helps a candidate.

**Must-have coverage** is a second number: `sum(3 x credit) / sum(3) x 100` over must-have skills only.
A missing must-have is **shown, never an automatic reject**; the recruiter decides. A hard gate is a business rule, not a scoring weight.

## Worked example
JD must-have: Python, SQL, RAG. Good-to-have: LangGraph, Docker.
Resume hands-on: Python, SQL, Docker. Theoretical: RAG, LangGraph.

| Skill | Weight | Evidence | Points |
|---|---|---|---|
| Python | 3 | Hands-on | 3.0 |
| SQL | 3 | Hands-on | 3.0 |
| RAG | 3 | Theoretical | 1.2 |
| LangGraph | 1 | Theoretical | 0.4 |
| Docker | 1 | Hands-on | 1.0 |
| **Total** | 11 | | **8.6** |

Score = 100 x 8.6 / 11 = **78.18**. Must-have coverage = (3 + 3 + 1.2) / 9 = **80%**.
(`tests/test_merge_score.py` checks this exact example.)

## Rules the code follows
- **Normalise first, then score.** Each skill is a canonical id (`rag`, not "Retrieval-augmented generation"). Matching is by id, or by exactly the same text. No guessing.
- **Related is not the same.** PostgreSQL does not match SQL; LangChain does not match LangGraph. Loose text overlaps are listed as "possible matches" and are **not scored**.
- **"Not found" is not "cannot do it".** The report says "No evidence found in resume" because extraction may have missed it.
- **Resume length does not change the score.** Order and duplicates do not change it either.
- **Explainable.** Every skill row keeps its weight, credit, points, the matched resume skill, how it matched, and the evidence sentence.
- A composite JD line such as "TensorFlow/Keras" is one skill; any one part satisfies it.

## Open question (answered)
The taxonomy (`config/skills_taxonomy.yaml`, 855 skills) holds only `name`, `category` and `aliases`: no parent/child links.
So a simple matcher is enough; no semantic layer is needed yet. Adding aliases (for example "MLOps practices" -> MLOps) is the cheapest way to raise match rates.

## Stages
1. **MVP** (done): weighted score, must-have coverage, per-skill explanation.
2. **Reliability** (done): strict taxonomy matching, input validation, tests.
3. **Calibration** (next): tune `config/config.yaml` (section `scoring`) from recruiters' labelled examples and check the ranking.

## HR screening
Phase 1's screening questions are marked per candidate (Met = 1, Partly met = 0.5, Not met = 0, see `config/config.yaml` (section `scoring`)).
The HR score is kept **separate** from the 100-point resume score.
