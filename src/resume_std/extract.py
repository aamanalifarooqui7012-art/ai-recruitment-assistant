"""Step 2: cut the file into blocks (paragraph-sized pieces with position and style).
Nothing here interprets meaning. Text is copied verbatim apart from clean_text()."""
import re
import zipfile
from collections import defaultdict

import pymupdf

from .models import Block, ExtractResult
from .textutil import BULLET_RE, clean_text, nonspace_len


# ----------------------------------------------------------------------------- PDF
def _span_is_bold(s) -> bool:
    name = s.get("font", "").lower()
    return bool(s.get("flags", 0) & 16) or "bold" in name or "black" in name or "heavy" in name


def _read_lines(page, pno, cfg):
    lines = []
    hid_max = cfg["extract"]["hidden_font_size_max"]
    for blk in page.get_text("dict")["blocks"]:
        if blk.get("type") != 0:
            continue
        for ln in blk["lines"]:
            text = clean_text("".join(s["text"] for s in ln["spans"]))
            if not text:
                continue
            spans = [s for s in ln["spans"] if s["text"].strip()]
            total = sum(len(s["text"].strip()) for s in spans) or 1
            dom = max(spans, key=lambda s: len(s["text"].strip()))
            bold = sum(len(s["text"].strip()) for s in spans if _span_is_bold(s)) / total >= 0.6
            hidden = sum(len(s["text"].strip()) for s in spans
                         if s.get("color") == 0xFFFFFF or s["size"] < hid_max) / total >= 0.5
            x0, y0, x1, y1 = ln["bbox"]
            lines.append(dict(text=text, page=pno, x0=x0, y0=y0, x1=x1, y1=y1,
                              size=round(dom["size"], 1), bold=bold, hidden=hidden,
                              bullet=bool(BULLET_RE.match(text))))
    return lines


LABEL_START_RE = re.compile(r"^[A-Z][\w &/+.\-]{1,30}:\s")


def _merge_lines(lines, cfg, gap_ratio=None):
    """Join wrapped lines of one paragraph. Same column, small gap, same style, previous line 'full'."""
    ecfg = dict(cfg["extract"])
    if gap_ratio is not None:
        ecfg["line_merge_gap_ratio"] = gap_ratio
    lines.sort(key=lambda l: (l["page"], l["y0"], l["x0"]))
    for l in lines:  # widest line in the same column = what a "full" line looks like
        same = [m["x1"] - m["x0"] for m in lines if m["page"] == l["page"] and abs(m["x0"] - l["x0"]) <= 8]
        l["colw"] = max(same)
    paras = []
    for l in lines:
        target = None
        for p in reversed([q for q in paras if q["page"] == l["page"]][-14:]):
            last = p["lines"][-1]
            gap = l["y0"] - last["y1"]
            if not (-2 <= gap <= ecfg["line_merge_gap_ratio"] * l["size"]):
                continue
            bold_ok = l["bold"] == last["bold"] or l["text"][:1].islower()  # lowercase start = sentence continuation
            if abs(l["size"] - last["size"]) > 0.6 or not bold_ok or l["bullet"]:
                continue
            if LABEL_START_RE.match(l["text"]) and not last["text"].rstrip().endswith(("|", ",", ";", "(", "/", "&", "-")):
                continue      # "Data Analysis: ..." starts a new item, unless the line above clearly broke mid-list
            flush = abs(l["x0"] - p["x0"]) <= 6
            hanging = p["lines"][0]["bullet"] and p["x0"] < l["x0"] <= p["x0"] + 30
            if not (flush or hanging):
                continue
            if (last["x1"] - last["x0"]) < ecfg["full_line_ratio"] * l["colw"]:
                continue  # previous line ended early, so it ended the paragraph
            target = p
            break
        if target:
            target["lines"].append(l)
        else:
            paras.append(dict(page=l["page"], x0=l["x0"], lines=[l]))
    return paras


def _pdf_links(doc):
    out = []
    for pno, page in enumerate(doc, start=1):
        for l in page.get_links():
            if l.get("kind") == pymupdf.LINK_URI and l.get("uri"):
                out.append((pno, l["from"], l["uri"]))
    return out


def _attach_links(blocks, links, notes):
    for pno, rect, uri in links:
        cx, cy = (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2
        hit = next((b for b in blocks if b.page == pno and b.x0 - 2 <= cx <= b.x1 + 2 and b.y0 - 2 <= cy <= b.y1 + 2), None)
        if hit is None:
            notes.append("link_not_attached")
        elif uri not in hit.links:
            hit.links.append(uri)


def _blocks_from_lines(lines, cfg, gap_ratio=None):
    blocks = []
    for i, p in enumerate(_merge_lines(lines, cfg, gap_ratio)):
        ls = p["lines"]
        text = clean_text(" ".join(l["text"] for l in ls))
        b = Block(id=i, text=text, raw_text=text, page=p["page"],
                  x0=min(l["x0"] for l in ls), y0=min(l["y0"] for l in ls),
                  x1=max(l["x1"] for l in ls), y1=max(l["y1"] for l in ls),
                  font_size=ls[0]["size"], bold=ls[0]["bold"], is_bullet=ls[0]["bullet"])
        if sum(l["hidden"] for l in ls) * 2 >= len(ls):
            b.flags.append("hidden_text")
        if any(l.get("lowconf") for l in ls):
            b.flags.append("ocr_low_conf")
        if any(l.get("fixed") for l in ls):
            b.flags.append("ocr_fixed")
        blocks.append(b)
    return blocks


def extract_pdf_ocr(path, cfg) -> ExtractResult:
    from .ocr import ocr_lines
    lines, page_sizes = ocr_lines(path, cfg)
    blocks = _blocks_from_lines(lines, cfg, cfg["ocr"]["gap_ratio"])
    notes = ["ocr"]
    doc = pymupdf.open(path)
    try:
        _attach_links(blocks, _pdf_links(doc), notes)      # a clickable layer can survive without a text layer
    finally:
        doc.close()
    ref = sum(nonspace_len(b.raw_text) for b in blocks)   # no independent source exists for OCR text
    return ExtractResult(blocks, "pdf", page_sizes, ref, notes, ocr=True)


def extract_pdf(path, cfg) -> ExtractResult:
    doc = pymupdf.open(path)
    lines, ref_chars, page_sizes, notes = [], 0, {}, []
    links = []
    try:
        links = _pdf_links(doc)
        for pno, page in enumerate(doc, start=1):
            page_sizes[pno] = (page.rect.width, page.rect.height)
            ref_chars += nonspace_len(clean_text(page.get_text("text")))
            lines += _read_lines(page, pno, cfg)
    finally:
        doc.close()
    blocks = _blocks_from_lines(lines, cfg)
    _attach_links(blocks, links, notes)
    return ExtractResult(blocks, "pdf", page_sizes, ref_chars, notes)


# ---------------------------------------------------------------------------- DOCX
def extract_docx(path, cfg) -> ExtractResult:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph
    from docx.text.run import Run

    doc = Document(path)
    W_P, W_TBL, W_TR, W_TC, W_SDT = qn("w:p"), qn("w:tbl"), qn("w:tr"), qn("w:tc"), qn("w:sdt")
    MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"

    default_size = 11.0
    try:
        sz = doc.styles.element.xpath("w:docDefaults/w:rPrDefault/w:rPr/w:sz/@w:val")
        if sz:
            default_size = int(sz[0]) / 2
    except Exception:
        pass

    def style_attr(style, attr):
        while style is not None:
            v = getattr(style.font, attr)
            if v is not None:
                return v
            style = style.base_style
        return None

    def own_runs(p):
        for r in p.iter(qn("w:r")):
            a = r.getparent()
            while a is not None and a.tag != W_P:
                a = a.getparent()
            if a is p:
                yield r

    def run_text(r):
        out = []
        for ch in r:
            if ch.tag == qn("w:t"):
                out.append(ch.text or "")
            elif ch.tag in (qn("w:tab"), qn("w:br"), qn("w:cr")):
                out.append(" ")
            elif ch.tag == qn("w:noBreakHyphen"):
                out.append("-")
        return "".join(out)

    items = []  # (paragraph_element, in_table, column)

    def walk(elem, in_table, col=-1):
        for ch in elem.iterchildren():
            if ch.tag == W_P:
                items.append((ch, in_table, col))
            elif ch.tag == W_TBL:
                rows = list(ch.iterchildren(W_TR))
                for tr in rows:
                    cells = list(tr.iterchildren(W_TC))
                    # a one-row, multi-cell table is a column layout (sidebar); other tables are just tables
                    layout_cols = len(rows) == 1 and len(cells) > 1
                    for idx, tc in enumerate(cells):
                        walk(tc, True, min(idx, 1) if layout_cols else -1)
            elif ch.tag == W_SDT:
                for c in ch.iterchildren(qn("w:sdtContent")):
                    walk(c, in_table, col)

    walk(doc.element.body, False)

    blocks = []
    for p_el, in_table, col in items:
        par = Paragraph(p_el, doc._body)
        runs = [(Run(r, par), run_text(r)) for r in own_runs(p_el)]
        text = clean_text("".join(t for _, t in runs))
        if not text:
            continue
        weighted = [(r, len(t.strip())) for r, t in runs if t.strip()]
        total = sum(n for _, n in weighted) or 1
        sizes, bold_chars = [], 0
        for r, n in weighted:
            s = r.font.size.pt if r.font.size else style_attr(par.style, "size")
            s = s.pt if hasattr(s, "pt") else s
            sizes.append(s or default_size)
            b = r.font.bold if r.font.bold is not None else style_attr(par.style, "bold")
            bold_chars += n if b else 0
        try:
            style_name = par.style.name or ""
        except Exception:
            style_name = ""
        num_pr = p_el.find(qn("w:pPr")) is not None and p_el.find(qn("w:pPr")).find(qn("w:numPr")) is not None
        is_bullet = num_pr or "list" in style_name.lower() or bool(BULLET_RE.match(text))
        i = len(blocks)
        links = []
        for h in p_el.iter(qn("w:hyperlink")):
            rid = h.get(qn("r:id"))
            rel = doc.part.rels.get(rid) if rid else None
            if rel is not None and rel.is_external and rel.target_ref not in links:
                links.append(rel.target_ref)
        blocks.append(Block(id=i, text=text, raw_text=text, page=1, y0=float(i), y1=float(i) + 1, links=links,
                            font_size=max(sizes) if sizes else default_size,
                            bold=bold_chars / total >= 0.6, is_bullet=is_bullet,
                            style=style_name, in_table=in_table, column=col))
    # independent reference count: every w:t in the body except legacy fallback copies
    ref = 0
    body = doc.element.body
    for t in body.iter(qn("w:t")):
        a, skip = t.getparent(), False
        while a is not None:
            if a.tag == MC_FALLBACK:
                skip = True
                break
            a = a.getparent()
        if not skip:
            ref += nonspace_len(clean_text(t.text or ""))
    return ExtractResult(blocks, "docx", {}, ref, [])


def extract(path, file_type, cfg, needs_ocr=False) -> ExtractResult:
    if file_type == "pdf":
        return extract_pdf_ocr(path, cfg) if needs_ocr else extract_pdf(path, cfg)
    return extract_docx(path, cfg)
