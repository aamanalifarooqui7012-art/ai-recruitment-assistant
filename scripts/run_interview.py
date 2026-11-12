"""Phase 3A (interview questions) from SAVED Phase 1 + Phase 2 outputs. Nothing is typed in or uploaded again.

  python scripts/run_interview.py --jd-json output/results/jd-1234abcd.json --layout out/workflow/layout
  python scripts/run_interview.py --jd-json output/results/jd-1234abcd.json --layout out/workflow/layout/Rahul.layout.json --out interviews/

  --jd-json   the Phase 1 json (it must still contain `jd_text`; it does unless you removed it)
  --layout    Phase 2 *.layout.json files, or the folder that holds them (written by scripts/run_workflow.py
              and scripts/resume_cli.py)
  --keywords  Phase 2 *.keywords.json files or their folder. Default: the `keywords` folder next to each layout folder
              (Phase 3A reuses the saved Phase 2 keywords; it never extracts them again)
For the whole flow in one go use:  python scripts/run_workflow.py ... --interview
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # repo/src on the import path

from interview.render import render  # noqa: E402
from interview.resume_text import layout_to_text  # noqa: E402
from interview.runner import usable_jd_text  # noqa: E402
from interview.schemas import InterviewScaffold  # noqa: E402
from maf import run_interview  # noqa: E402


def _layouts(paths):
    out = []
    for p in map(Path, paths):
        if p.is_dir():
            out += sorted(p.glob("*.layout.json"))
        elif p.is_file():
            out.append(p)
        else:
            raise SystemExit(f"Not found: {p}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jd-json", required=True, help="Phase 1 json of the JD")
    ap.add_argument("--layout", nargs="+", required=True, help="Phase 2 *.layout.json files or folders")
    ap.add_argument("--keywords", nargs="*", default=None, help="Phase 2 *.keywords.json files or folders")
    ap.add_argument("--out", default=None, help="folder to save <resume>.interview.json files")
    ap.add_argument("--no-save", action="store_true", help="do not write to output/scaffolds")
    a = ap.parse_args(argv)

    jd = json.loads(Path(a.jd_json).read_text(encoding="utf-8"))
    if not usable_jd_text(jd):
        raise SystemExit("This Phase 1 json has no usable `jd_text` (missing or only a placeholder); Phase 3A needs the JD text.")
    files = _layouts(a.layout)
    if not files:
        raise SystemExit("No *.layout.json files found.")
    kw_files = {}
    for p in map(Path, a.keywords or []):
        for k in (sorted(p.glob("*.keywords.json")) if p.is_dir() else [p]):
            kw_files[k.name.replace(".keywords.json", "")] = k
    resumes, layouts, keywords, review = {}, {}, {}, {}
    for f in files:
        layout = json.loads(f.read_text(encoding="utf-8"))
        name = f.name.replace(".layout.json", "")
        resumes[name], layouts[name] = layout_to_text(layout), layout
        kw = kw_files.get(name) or f.parent.parent / "keywords" / f"{name}.keywords.json"
        if kw.is_file():
            keywords[name] = json.loads(kw.read_text(encoding="utf-8"))
        if layout.get("status") != "ok" and layout.get("review_reasons"):
            review[name] = list(layout["review_reasons"])

    scaffolds, errors = run_interview(jd, resumes, keywords, layouts, review=review, save=not a.no_save,
                                      progress=lambda m: print(f"  - {m}", file=sys.stderr))
    for name, sc in scaffolds.items():
        print(f"\n#### {name}")
        print(render(InterviewScaffold.model_validate(sc)))
        if a.out:
            out = Path(a.out)
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{name}.interview.json").write_text(json.dumps(sc, indent=2, ensure_ascii=False), encoding="utf-8")
    for e in errors:
        print(f"no interview questions: {e['candidate']} - {e['error']}")
    raise SystemExit(0 if scaffolds else 1)


if __name__ == "__main__":
    main()
