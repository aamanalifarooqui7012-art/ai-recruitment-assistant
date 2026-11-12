"""Scores pipeline output against gold labels.
Usage: python tools/evaluate.py samples/gold out [--verbose]
'status ok' only means no flags fired. This script tells you whether the labels were actually right."""
import json
import sys
from pathlib import Path


def norm(t):
    return " ".join(t.split())


def main():
    gold_dir, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    verbose = "--verbose" in sys.argv
    tot_ok = tot_gold = 0
    silent_wrong = 0
    print(f"{'file':28} {'status':7} {'correct':>9} {'wrong':>6} {'unmatched':>9}")
    for g in sorted(gold_dir.glob("*.json")):
        fname = g.name[:-5]
        stem = fname.replace(".", "_")
        lp = out_dir / "layout" / f"{stem}.layout.json"
        if not lp.exists():
            continue
        layout = json.loads(lp.read_text("utf-8"))
        pii = json.loads((out_dir / "pii" / f"{stem}.pii.json").read_text("utf-8"))
        gold = json.loads(g.read_text("utf-8"))
        got = {}
        for s in layout["sections"]:
            for e in s["entries"]:
                for b in e["blocks"]:
                    t = b["text"]
                    for p in pii:
                        t = t.replace(p["placeholder"], p["original"])
                    got.setdefault(norm(t), []).append(s["name"])
        ok = wrong = miss = 0
        notes = []
        for it in gold:
            secs = got.get(norm(it["text"]))
            if secs is None:
                miss += 1
                notes.append(f"  UNMATCHED [{it['section']}] {it['text'][:70]}")
            elif it["section"] in secs:
                ok += 1
            else:
                wrong += 1
                notes.append(f"  WRONG gold={it['section']} got={secs[0]} :: {it['text'][:60]}")
        tot_ok += ok
        tot_gold += len(gold)
        if layout["status"] == "ok" and (wrong or miss):
            silent_wrong += 1
        print(f"{fname:28} {layout['status']:7} {ok:>4}/{len(gold):<4} {wrong:>6} {miss:>9}")
        if verbose or wrong or miss:
            for n in notes[:8]:
                print(n)
    print(f"\nblock-level accuracy: {tot_ok}/{tot_gold} = {tot_ok / max(tot_gold, 1):.0%}")
    print(f"files marked ok but containing wrong labels (the dangerous kind): {silent_wrong}")


if __name__ == "__main__":
    main()
