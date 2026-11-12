"""Runs steps 1-7 on one file, then a folder in parallel. One bad file never stops the batch."""
import csv
import json
import traceback
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path

from .config import load_config
from .extract import extract
from .fidelity import check_fidelity
from .intake import check_file
from .label import label_blocks
from .layout import build_layout
from .mask_pii import mask_pii
from .reading_order import order_blocks
from .render import to_docx, to_markdown
from .keywords import extract_keywords
from .llm import make_llm

SUPPORTED = {".pdf", ".docx", ".doc", ".txt"}   # anything is sent to intake; the bytes decide


def process_file(path, cfg) -> dict:
    name = Path(path).name
    res = {"file": name, "status": "error", "reasons": [], "notes": [], "layout": None,
           "pii": [], "label_debug": [], "fidelity": None, "keywords": None}
    try:
        intake = check_file(path, cfg)
        if not intake.ok:
            res.update(status="rejected", reasons=[f"{intake.reason_code}: {intake.reason}"])
            return res
        ex = extract(path, intake.file_type, cfg, intake.needs_ocr)
        blocks, notes = order_blocks(ex.blocks, ex.page_sizes, ex.file_type, cfg["reading_order"])
        pii, pnotes = mask_pii(blocks, cfg)
        stats = label_blocks(blocks, cfg)
        reasons = []
        if ex.ocr:
            fixed = sum(1 for b in blocks if "ocr_fixed" in b.flags)
            reasons.append("ocr_used" + (f"(auto-corrected {fixed} lines)" if fixed else ""))
            low = sum(1 for b in blocks if "ocr_low_conf" in b.flags)
            if low:
                reasons.append(f"ocr_low_confidence_lines({low})")
        if stats["known_headings"] == 0:
            reasons.append("no_headings_found")
        if stats["other_ratio"] > cfg["review"]["max_other_ratio"]:
            reasons.append(f"too_much_unclassified_text({stats['other_ratio']:.0%})")
        if stats["low_conf_ratio"] > cfg["review"]["max_low_conf_ratio"]:
            reasons.append(f"low_confidence_labels({stats['low_conf_ratio']:.0%})")
        if stats["unknown_headings"]:
            reasons.append(f"unknown_headings({stats['unknown_headings']})")
        if any("hidden_text" in b.flags for b in blocks):
            reasons.append("hidden_text_detected")
        if any(f.startswith("content_conflict") for b in blocks for f in b.flags):
            reasons.append("content_conflicts_heading")
        ambiguous = [b for b in blocks if b.section == "other" and b.label_source == "inferred"
                     and len(b.text.split()) >= 4]
        if ambiguous:
            reasons.append(f"ambiguous_blocks({len(ambiguous)})")
        secs = {b.section for b in blocks}
        if not ({"experience", "education"} & secs):
            reasons.append("no_experience_or_education")
        if "name_not_found" in pnotes:
            reasons.append("name_not_found")
        meta = {"source_file": name, "file_type": ex.file_type, "text_source": "ocr" if ex.ocr else "embedded", "notes": notes + pnotes,
                "links_found": sum(len(b.links) for b in blocks),
                "label_stats": {k: round(v, 3) if isinstance(v, float) else v for k, v in stats.items()}}
        layout = build_layout(blocks, {**meta, "status": "pending", "review_reasons": []})
        fid = check_fidelity(blocks, layout, pii, ex.reference_chars)
        if not fid["ok"]:
            reasons += [f"fidelity_failed: {p}" for p in fid["problems"]]
        layout["status"] = "review" if reasons else "ok"
        layout["review_reasons"] = reasons
        keywords = extract_keywords(layout, cfg, make_llm(cfg)) if fid["ok"] else None
        res.update(status=layout["status"], reasons=reasons, notes=notes + pnotes, layout=layout,
                   pii=[asdict(p) for p in pii], fidelity=fid, keywords=keywords,
                   label_debug=[(b.order, b.page, b.section, b.label_source, round(b.confidence, 2),
                                 ",".join(b.flags), b.text[:90]) for b in blocks])
    except Exception as e:  # per-file failure, batch continues
        res.update(status="error", reasons=[f"{type(e).__name__}: {e}"],
                   notes=[traceback.format_exc(limit=3)])
    return res


def _worker(args):
    path, cfg = args
    return process_file(path, cfg)


def _safe(name: str) -> str:
    return name.replace(".", "_")


def write_outputs(results, out_dir):
    out = Path(out_dir)
    for d in ("layout", "debug", "pii", "keywords"):
        (out / d).mkdir(parents=True, exist_ok=True)
    rows = []
    for r in results:
        s = _safe(r["file"])
        if r["layout"]:
            (out / "layout" / f"{s}.layout.json").write_text(json.dumps(r["layout"], indent=2, ensure_ascii=False), "utf-8")
            (out / "layout" / f"{s}.md").write_text(to_markdown(r["layout"]), "utf-8")
            to_docx(r["layout"], out / "layout" / f"{s}.docx")
            (out / "pii" / f"{s}.pii.json").write_text(json.dumps(r["pii"], indent=2, ensure_ascii=False), "utf-8")
            lines = ["order | page | section | source | conf | flags | text"] + [" | ".join(map(str, row)) for row in r["label_debug"]]
            (out / "debug" / f"{s}.labels.txt").write_text("\n".join(lines), "utf-8")
        rows.append({"file": r["file"], "status": r["status"], "reasons": "; ".join(r["reasons"]),
                     "fidelity_ok": (r["fidelity"] or {}).get("ok", ""),
                     "reviewer_labels_correct (Y/N)": "", "reviewer_notes": ""})
        if r.get("keywords"):
            (out / "keywords" / f"{s}.keywords.json").write_text(
                json.dumps(r["keywords"], indent=2, ensure_ascii=False), "utf-8")
    with open(out / "review_log.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def process_folder(folder, out_dir, cfg=None, workers=4):
    cfg = cfg or load_config()
    p = Path(folder)
    files = sorted(x for x in ([p] if p.is_file() else p.iterdir()) if x.is_file() and not x.name.startswith("."))
    if workers > 1 and len(files) > 1:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(_worker, [(str(f), cfg) for f in files]))
    else:
        results = [process_file(str(f), cfg) for f in files]
    if results:
        write_outputs(results, out_dir)
    return results
