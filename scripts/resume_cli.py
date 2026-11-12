"""Usage: python scripts/resume_cli.py <file-or-folder> [--out out] [--workers 4] [--config config/config.yaml]"""
import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # repo/src on the import path

from resume_std.config import load_config
from resume_std.pipeline import process_folder


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--out", default="out")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--config", default=None)
    a = ap.parse_args()
    results = process_folder(a.path, a.out, load_config(a.config), a.workers)
    for r in results:
        print(f"{r['status']:9} {r['file']:34} {'; '.join(r['reasons'])[:110]}")
    n = len(results) or 1
    c = Counter(r["status"] for r in results)
    readable = [r for r in results if r["status"] in ("ok", "review")]
    fid = sum(1 for r in readable if r["fidelity"]["ok"])
    print(f"\n{len(results)} files: ok={c['ok']} review={c['review']} rejected={c['rejected']} error={c['error']}")
    print(f"clean without human help: {c['ok']/n:.0%} | fidelity passed: {fid}/{len(readable)}")
    print(f"outputs in {a.out}/ (layout, debug, pii, review_log.csv)")


if __name__ == "__main__":
    main()
