"""Resume keyword columns, built from the labelled layout (stage 5.2 output). Reads the layout, never changes it.

  hands_on    = skills named inside experience / project text
  theoretical = skills stated anywhere else (skills list, summary, education, certificates, "learning" items)

Flow: load_resume -> split into units -> [LLM proposes] -> verify quotes -> code scans the same text (recall net)
      -> union -> normalise to taxonomy ids -> dedupe (hands-on wins) -> review lists."""
import re
from collections import OrderedDict

from .taxonomy import Taxonomy, get_taxonomy, norm_key
WS = lambda t: " ".join(t.split())


# ----------------------------------------------------------------------------- load + split
def load_resume(layout, kcfg):
    """Adapter: layout JSON -> list of chunks. The only place that knows the 5.2 output format."""
    chunks = []
    for sec in layout["sections"]:
        if sec["name"] in kcfg["skip_sections"]:
            continue
        for ei, e in enumerate(sec["entries"]):
            blocks = [{"id": b["id"], "text": b["text"]} for b in e["blocks"]
                      if not (b["is_heading"] and b["label_source"] == "heading")]
            if blocks:
                chunks.append({"section": sec["name"], "entry": ei, "dates": e["dates"], "blocks": blocks})
    return chunks


def build_units(chunks, kcfg):
    """Split chunks into (chunk, column) units. 'In progress' / 'learning' text goes to theory even in a project."""
    cue = re.compile(r"\b(?:" + "|".join(re.escape(c) for c in kcfg["learning_cues"]) + r")\b", re.I)
    units = []
    for ch in chunks:
        hands = ch["section"] in kcfg["hands_on_sections"]
        entry_learning = hands and bool(cue.search(ch["blocks"][0]["text"]))   # e.g. "Skill-Building (In Progress)"
        groups = {"hands_on": [], "theory": []}
        for b in ch["blocks"]:
            col = "hands_on" if hands and not entry_learning and not cue.search(b["text"]) else "theory"
            groups[col].append(b)
        for col, bs in groups.items():
            if bs:
                units.append({"kind": col, "section": ch["section"], "entry": ch["entry"], "dates": ch["dates"],
                              "blocks": bs, "text": "\n".join(b["text"] for b in bs)})
    return units


# ------------------------------------------------------------------------------ code scan
def sentence_around(text, start, end, cap=240):
    if len(text) <= cap:
        return text
    cuts = [0] + [m.end() for m in re.finditer(r"[.;|\u2022]\s", text)] + [len(text)]
    for a, b in zip(cuts, cuts[1:]):
        if a <= start < b:
            seg = text[a:b].strip()
            if len(seg) <= cap:
                return seg
            break
    return text[max(0, start - 100):end + 100].strip()


def scan_unit(unit, tax: Taxonomy):
    out = {}
    for b in unit["blocks"]:
        for m in tax.scan(b["text"]):
            out.setdefault(m.skill_id, {"quote": sentence_around(b["text"], m.start, m.end), "block_id": b["id"]})
    return out


# -------------------------------------------------------------------------------- verify
def verify(raw_items, unit, tax: Taxonomy):
    ok, bad = [], []
    blocks = [(b["id"], WS(b["text"])) for b in unit["blocks"]]
    for it in raw_items:
        kw, q = str(it.get("keyword", "")).strip(), WS(str(it.get("quote", "")))
        if not kw or not q:
            bad.append({"keyword": kw, "quote": q, "reason": "empty"})
            continue
        bid = next((i for i, t in blocks if q in t), None)
        if bid is None:
            bad.append({"keyword": kw, "quote": q, "reason": "quote_not_in_resume"})
            continue
        ids = {m.skill_id for m in tax.find(kw)} or ({tax.canonical(kw)} - {None})
        in_quote = norm_key(kw) in norm_key(q) or (ids and ids <= {m.skill_id for m in tax.find(q)})
        if not in_quote:
            bad.append({"keyword": kw, "quote": q, "reason": "keyword_not_in_quote"})
            continue
        ok.append({"keyword": kw, "quote": q, "block_id": bid, "ids": sorted(ids)})
    return ok, bad


# ------------------------------------------------------------------------ review candidates
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#]*(?:[.\-][A-Za-z0-9+#]+)*")


def review_candidates(units, tax: Taxonomy, known_tokens, kcfg):
    """Tool-looking words that are in no taxonomy and that nobody extracted. For a human to check, never auto-counted."""
    stop = {s.lower() for s in kcfg["review_stoplist"]}
    seen, out = set(), []
    for u in units:
        for b in u["blocks"]:
            covered = [(m.start, m.end) for m in tax.find(b["text"])]
            for m in _WORD.finditer(b["text"]):
                tok = m.group(0)
                low = tok.lower()
                if (len(tok) < 3 or low in stop or low in seen or tax.canonical(tok) or norm_key(tok) in known_tokens
                        or any(a <= m.start() < z for a, z in covered)):
                    continue
                toolish = (re.search(r"[a-z][A-Z]", tok) or (re.search(r"[A-Za-z]", tok) and re.search(r"\d", tok))
                           or (tok.isupper() and len(tok) >= 3) or re.search(r"[A-Za-z]\.[A-Za-z]", tok))
                if toolish:
                    seen.add(low)
                    out.append({"token": tok, "block_id": b["id"], "column": u["kind"],
                                "quote": sentence_around(b["text"], m.start(), m.end())})
                    if len(out) >= kcfg["review_cap"]:
                        return out
    return out


# ---------------------------------------------------------------------------------- main
def extract_keywords(layout, cfg, llm=None, tax=None):
    kcfg = cfg["keywords"]
    tax = tax or get_taxonomy(cfg)
    units = build_units(load_resume(layout, kcfg), kcfg)
    cols = {"hands_on": OrderedDict(), "theoretical": OrderedDict()}
    rejected, code_added, llm_ids_total, llm_ids_agree = [], [], 0, 0
    verified_tokens, llm_items_n = set(), 0

    def add(col, key, info, ev):
        d = cols[col].setdefault(key, {**info, "evidence": [], "sources": set(), "also_in_theory": False})
        d["sources"].add(ev["source"])
        if len(d["evidence"]) < kcfg["evidence_per_skill"] and not any(e["quote"] == ev["quote"] for e in d["evidence"]):
            d["evidence"].append(ev)

    for u in units:
        col = "hands_on" if u["kind"] == "hands_on" else "theoretical"
        meta = {"section": u["section"], "entry": u["entry"], "dates": u["dates"]}
        code = scan_unit(u, tax)
        llm_found = set()
        if llm is not None:
            raw = llm.extract(u["kind"], u["text"])
            llm_items_n += len(raw)
            ok, bad = verify(raw, u, tax)
            rejected += [{**r, **meta} for r in bad]
            for it in ok:
                verified_tokens.add(norm_key(it["keyword"]))
                for sid in it["ids"] or [None]:
                    if sid is None:
                        add(col, "unmapped:" + norm_key(it["keyword"]),
                            {"skill_id": "Unmapped", "name": it["keyword"], "category": "Unmapped"},
                            {"quote": it["quote"], "block_id": it["block_id"], "source": "llm", **meta})
                    else:
                        llm_found.add(sid)
                        src = "both" if sid in code else "llm"
                        add(col, sid, tax.info(sid), {"quote": it["quote"], "block_id": it["block_id"], "source": src, **meta})
        for sid, c in code.items():
            if sid in llm_found:
                continue
            if llm is not None:
                code_added.append({"skill_id": sid, "section": u["section"], "entry": u["entry"], "quote": c["quote"]})
            add(col, sid, tax.info(sid), {"quote": c["quote"], "block_id": c["block_id"], "source": "code", **meta})
        if llm is not None:
            llm_ids_total += len(code)
            llm_ids_agree += len(set(code) & llm_found)

    for key in list(cols["theoretical"]):          # a skill used in work counts as hands-on only
        if key in cols["hands_on"]:
            cols["hands_on"][key]["also_in_theory"] = True
            del cols["theoretical"][key]

    def finish(d):
        return [{**v, "sources": sorted(v["sources"])} for v in d.values()]

    known = {norm_key(t) for t in verified_tokens}
    result = {
        "resume": layout.get("source_file", ""),
        "hands_on": finish(cols["hands_on"]),
        "theoretical": finish(cols["theoretical"]),
        "unmapped": [{"keyword": v["name"], "column": c} for c in cols for v in cols[c].values() if v["skill_id"] == "Unmapped"],
        "review_candidates": review_candidates(units, tax, known, kcfg),
        "rejected_llm_items": rejected,
        "code_added": code_added,
        "stats": {"llm_used": llm is not None, "units": len(units), "llm_items": llm_items_n,
                  "llm_rejected": len(rejected), "code_added": len(code_added),
                  "code_found_also_by_llm": (f"{llm_ids_agree}/{llm_ids_total}" if llm is not None else None)},
    }
    return result
