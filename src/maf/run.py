"""Command line for the whole workflow.

  python scripts/run_workflow.py --jd input/jd.txt --resumes resumes/            # Phase 1 + 2 + 2.5 (needs the LLM key)
  python scripts/run_workflow.py --jd-json samples/jd/sample_jd.json --keywords samples/keywords    # no LLM at all
  add  --out results/  to save ranking.csv, workflow_result.json and one <jd>__<resume>.score.json per resume
  add  --interview     to also write Phase 3A interview questions per candidate (from the SAME JD and resumes; needs the
                       LLM key and the JD text, so use --jd or a Phase 1 json that still has its jd_text)
       --interview-top 3   only for the 3 best-ranked candidates
HR screening questions (Phase 1) and interview questions (Phase 3A) are printed and saved separately.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

from .models import PipelineRequest
from .workflow import run_pipeline

RESUME_SUFFIXES = {".pdf", ".docx", ".doc", ".txt"}


def _files(paths, suffixes):
    out = []
    for p in map(Path, paths):
        if p.is_dir():
            out += sorted(x for x in p.iterdir() if x.is_file() and x.suffix.lower() in suffixes and not x.name.startswith("."))
        elif p.is_file():
            out.append(p)
        else:
            raise SystemExit(f"Not found: {p}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    jd = ap.add_mutually_exclusive_group(required=True)
    jd.add_argument("--jd", help="job description as a text file (runs Phase 1)")
    jd.add_argument("--jd-json", help="an existing Phase 1 json (skips the LLM)")
    ap.add_argument("--resumes", nargs="*", default=[], help="resume files or folders: pdf / docx / doc / txt (runs Phase 2)")
    ap.add_argument("--keywords", nargs="*", default=[], help="existing Phase 2 *.keywords.json files or folders (skips the LLM)")
    ap.add_argument("--out", default=None, help="folder for the saved results")
    ap.add_argument("--no-save", action="store_true", help="do not write Phase 1 / Phase 2 output files")
    ap.add_argument("--must-weight", type=float, default=None)
    ap.add_argument("--nice-weight", type=float, default=None)
    ap.add_argument("--interview", action="store_true", help="also run Phase 3A: interview questions per candidate")
    ap.add_argument("--interview-top", type=int, default=None, metavar="N", help="Phase 3A only for the N best-ranked candidates")
    ap.add_argument("--hands-on-credit", type=float, default=None)
    ap.add_argument("--theory-credit", type=float, default=None)
    a = ap.parse_args(argv)

    weights = {k: v for k, v in (("must_have", a.must_weight), ("nice_to_have", a.nice_weight)) if v is not None}
    credits = {k: v for k, v in (("hands_on", a.hands_on_credit), ("theoretical", a.theory_credit)) if v is not None}
    keywords = {}
    for f in _files(a.keywords, {".json"}):
        keywords[f.name.replace(".keywords.json", "")] = json.loads(f.read_text(encoding="utf-8"))

    request = PipelineRequest(
        jd_text=Path(a.jd).read_text(encoding="utf-8") if a.jd else None,
        jd_json=json.loads(Path(a.jd_json).read_text(encoding="utf-8")) if a.jd_json else None,
        resume_files=[str(f) for f in _files(a.resumes, RESUME_SUFFIXES)],
        keywords_json=keywords, weights=weights or None, credits=credits or None,
        save_outputs=not a.no_save,
        interview=a.interview or a.interview_top is not None, interview_top_n=a.interview_top, progress=lambda m: print(f"  - {m}", file=sys.stderr),
    )
    result = run_pipeline(request)

    print(f"\n{result.jd.get('role_title')}  ({result.jd.get('jd_id')})")
    for i, s in enumerate(result.scores, start=1):
        mh = s["must_have"]
        cov = "n/a" if mh["coverage_pct"] is None else f"{mh['coverage_pct']}%"
        print(f"{i:>2}. {str(s['resume']):<42} score {s['score']:>6}/100  must-have coverage {cov:>7} "
              f"({mh['matched']}/{mh['count']})  missing must-have: {len(mh['missing'])}")
    for f in result.failures:
        print(f"    not scored: {f['file']} [{f['status']}] {'; '.join(f['reasons'])[:120]}")
    for e in result.score_errors:
        print(f"    not scored: {e['resume']} - {e['error']}")
    for n in result.notes:
        print(f"    note: {n}")
    print(f"HR screening: {len(result.hr['questions'])} questions ready (mark them in the app, per candidate)")
    if request.interview:
        from interview.render import render
        from interview.schemas import InterviewScaffold
        for name, sc in result.interviews.items():
            print(f"\n#### Interview questions (Phase 3A) - {name}")
            print(render(InterviewScaffold.model_validate(sc)))
        for e in result.interview_errors:
            print(f"    no interview questions: {e['candidate']} - {e['error']}")

    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "workflow_result.json").write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        for s in result.scores:
            name = f"{s['jd_id']}__{Path(str(s['resume'])).stem}.score.json"
            (out / name).write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
        if result.interviews:
            (out / "interview").mkdir(exist_ok=True)
            for name, sc in result.interviews.items():
                (out / "interview" / f"{result.jd.get('jd_id')}__{Path(name).stem}.interview.json").write_text(
                    json.dumps(sc, indent=2, ensure_ascii=False), encoding="utf-8")
        with open(out / "ranking.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["rank", "resume", "score", "must_have_coverage_pct", "must_matched", "must_total", "missing_must_have"])
            for i, s in enumerate(result.scores, start=1):
                mh = s["must_have"]
                w.writerow([i, s["resume"], s["score"], mh["coverage_pct"], mh["matched"], mh["count"], "; ".join(mh["missing"])])
        print(f"saved to {out}/")


if __name__ == "__main__":
    main()
