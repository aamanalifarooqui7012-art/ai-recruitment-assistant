"""OCR fallback for PDFs with no text layer (scans, 'Print to PDF' exports). Needs the Tesseract engine installed."""
import io
import os
import re
import shutil
from statistics import median

import pymupdf

from .textutil import clean_text

OCR_BULLET_MARKERS = {"e", "\u00a2", "\u00a9", "\u00ab", "*", "\u2022", "o", "\u00b0", "\u00bb", "+", "\u2014", "-"}
_WIN = [r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"]


def _configure(cfg):
    import pytesseract
    cmd = (cfg.get("ocr", {}).get("tesseract_cmd") or "").strip()
    if not cmd:
        cmd = shutil.which("tesseract") or next((c for c in _WIN + ["/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract"]
                                                 if os.path.exists(c)), "")
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    return pytesseract


def tesseract_ready(cfg):
    try:
        pt = _configure(cfg)
        import PIL  # noqa
    except ImportError:
        return False, "OCR needs two Python packages: pip install pytesseract pillow"
    try:
        pt.get_tesseract_version()
    except Exception:
        return False, ("OCR engine (Tesseract) not found. Install it (Windows: github.com/UB-Mannheim/tesseract/wiki), "
                       "or set ocr.tesseract_cmd in config.yaml. Or re-export the resume with 'Save as PDF' instead of 'Print to PDF'.")
    return True, ""


def ocr_lines(path, cfg):
    """Returns (lines, page_sizes). Lines use the same shape as the text-PDF reader."""
    from PIL import Image
    ocfg = cfg["ocr"]
    pt = _configure(cfg)
    scale = 72.0 / ocfg["dpi"]
    doc = pymupdf.open(path)
    lines, page_sizes, heights = [], {}, []
    try:
        for pno, page in enumerate(doc, start=1):
            page_sizes[pno] = (page.rect.width, page.rect.height)
            img = Image.open(io.BytesIO(page.get_pixmap(dpi=ocfg["dpi"]).tobytes("png")))
            d = pt.image_to_data(img, lang=ocfg["language"], output_type=pt.Output.DICT)
            groups = {}
            for i, txt in enumerate(d["text"]):
                if txt.strip() and float(d["conf"][i]) >= 0:
                    groups.setdefault((d["block_num"][i], d["par_num"][i], d["line_num"][i]), []).append(i)
            page_lines = []
            for idx in groups.values():
                idx.sort(key=lambda i: d["left"][i])
                words = [d["text"][i] for i in idx]
                h = median(d["height"][i] for i in idx) * scale
                page_lines.append(dict(
                    words=words, page=pno, conf=sum(float(d["conf"][i]) for i in idx) / len(idx), h=h,
                    x0=min(d["left"][i] for i in idx) * scale, y0=min(d["top"][i] for i in idx) * scale,
                    x1=max(d["left"][i] + d["width"][i] for i in idx) * scale,
                    y1=max(d["top"][i] + d["height"][i] for i in idx) * scale))
            if page_lines:
                margin = min(l["x0"] for l in page_lines)
                for l in page_lines:   # OCR often reads a bullet dot as "e", a cent sign, etc.
                    if len(l["words"]) > 1 and l["words"][0].lower() in OCR_BULLET_MARKERS and l["x0"] > margin + 4:
                        l["words"][0] = "\u2022"
                        l["marker_fixed"] = True
            lines += page_lines
            heights += [l["h"] for l in page_lines]
    finally:
        doc.close()
    body = median(heights) if heights else 10.0
    fixes = [(re.compile(f["pattern"]), f["replace"]) for f in ocfg.get("fixes", [])]
    out = []
    for l in lines:
        text = clean_text(" ".join(l["words"]))
        if not text:
            continue
        fixed = False
        for rx, rep in fixes:           # known OCR misreads only (e.g. "OpenAl" -> "OpenAI"); each fix is flagged
            new = rx.sub(rep, text)
            fixed, text = fixed or new != text, new
        # Box heights are too noisy for exact sizes: only clearly larger lines (the name) count as bigger.
        size = round(body * (1.5 if l["h"] >= 1.25 * body else 1.0), 1)
        out.append(dict(text=text, page=l["page"], x0=l["x0"], y0=l["y0"], x1=l["x1"], y1=l["y1"], size=size,
                        bold=False, hidden=False, bullet=text.startswith("\u2022"), conf=l["conf"], fixed=fixed,
                        lowconf=l["conf"] < ocfg["min_line_conf"]))
    return out, page_sizes
