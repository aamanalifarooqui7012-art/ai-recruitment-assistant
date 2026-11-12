"""Step 6: build the standard layout. Blocks are copied by id into a fixed section order.
No text is created or edited here."""
from .textutil import DATE_RANGE_RE, parse_date_range

SECTION_ORDER = ["contact", "summary", "experience", "skills", "projects", "education",
                 "certifications", "achievements", "languages", "interests", "references", "other"]
ENTRY_SECTIONS = {"experience", "education", "projects"}


def _block_dict(b):
    return {"id": b.id, "text": b.text, "page": b.page, "is_bullet": b.is_bullet,
            "is_heading": b.is_heading, "label_source": b.label_source,
            "confidence": round(b.confidence, 2), "flags": list(b.flags), "links": list(b.links)}


def split_entries(blocks):
    """Start a new entry at each date-range block, pulling up to 2 short header lines above it."""
    starts = []
    for i, b in enumerate(blocks):
        if b.is_heading or not DATE_RANGE_RE.search(b.text):
            continue
        s = i
        floor = starts[-1] + 1 if starts else 0
        while s - 1 >= floor and i - s < 2:
            p = blocks[s - 1]
            if p.is_bullet or p.is_heading or DATE_RANGE_RE.search(p.text) or len(p.text.split()) > 12:
                break
            s -= 1
        starts.append(s)
    if not starts:
        return [{"dates": None, "blocks": blocks}]
    entries, first = [], starts[0]
    if first > 0:
        entries.append({"dates": None, "blocks": blocks[:first]})
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else len(blocks)
        chunk = blocks[s:e]
        dates = next((parse_date_range(c.text) for c in chunk if DATE_RANGE_RE.search(c.text)), None)
        entries.append({"dates": dates, "blocks": chunk})
    return entries


def build_layout(blocks, meta):
    by_sec = {}
    for b in sorted(blocks, key=lambda b: b.order):
        by_sec.setdefault(b.section or "other", []).append(b)
    sections = []
    for name in SECTION_ORDER + [s for s in by_sec if s not in SECTION_ORDER]:
        if name not in by_sec:
            continue
        bs = by_sec[name]
        entries = split_entries(bs) if name in ENTRY_SECTIONS else [{"dates": None, "blocks": bs}]
        sections.append({"name": name,
                         "entries": [{"dates": e["dates"], "blocks": [_block_dict(b) for b in e["blocks"]]}
                                     for e in entries]})
    return {**meta, "sections": sections}


def layout_blocks(layout):
    for s in layout["sections"]:
        for e in s["entries"]:
            for b in e["blocks"]:
                yield b
