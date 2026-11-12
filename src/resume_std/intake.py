"""Step 1: deterministic intake checks. Reject per file with a clear reason."""
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pymupdf


@dataclass
class IntakeResult:
    ok: bool
    file_type: str = ""
    reason_code: str = ""
    reason: str = ""
    needs_ocr: bool = False


def _reject(code: str, msg: str) -> IntakeResult:
    return IntakeResult(False, "", code, msg)


def check_file(path, cfg) -> IntakeResult:
    p = Path(path)
    icfg = cfg["intake"]
    if not p.is_file():
        return _reject("missing", "File not found")
    size = p.stat().st_size
    if size == 0:
        return _reject("empty", "File is empty")
    if size > icfg["max_file_mb"] * 1024 * 1024:
        return _reject("too_large", f"File larger than {icfg['max_file_mb']} MB")
    with open(p, "rb") as f:
        head = f.read(1024)
    # Real type comes from the bytes, never the extension.
    if b"%PDF" in head:
        return _check_pdf(p, icfg, cfg)
    if head.startswith(b"PK\x03\x04"):
        return _check_docx(p, icfg)
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return _reject("legacy_or_encrypted_office",
                       "Old .doc file or password-protected Office file; save as .docx and retry")
    return _reject("unsupported_type", "Only PDF and DOCX are supported")


def _check_pdf(p: Path, icfg, cfg=None) -> IntakeResult:
    try:
        doc = pymupdf.open(p)
    except Exception as e:
        return _reject("corrupt", f"PDF cannot be opened ({type(e).__name__})")
    try:
        if doc.needs_pass:
            return _reject("password_protected", "PDF is password-protected")
        if doc.page_count == 0:
            return _reject("corrupt", "PDF has no pages")
        chars = sum(len(pg.get_text("text").strip()) for pg in doc)
        if chars / doc.page_count < icfg["min_chars_per_page"]:
            if (cfg or {}).get("ocr", {}).get("enabled"):
                from .ocr import tesseract_ready
                ready, msg = tesseract_ready(cfg)
                if ready:
                    return IntakeResult(True, "pdf", needs_ocr=True)
                return _reject("ocr_engine_missing", "No text layer (scan or 'Print to PDF'). " + msg)
            return _reject("scanned_or_image_only", "Almost no extractable text; likely a scan (OCR not enabled)")
    except Exception as e:
        return _reject("corrupt", f"PDF is damaged ({type(e).__name__})")
    finally:
        doc.close()
    return IntakeResult(True, "pdf")


def _check_docx(p: Path, icfg) -> IntakeResult:
    if not zipfile.is_zipfile(p):
        return _reject("corrupt", "File is not a valid DOCX")
    try:
        with zipfile.ZipFile(p) as z:
            if "word/document.xml" not in z.namelist():
                return _reject("unsupported_type", "ZIP file, but not a Word document")
            xml = z.read("word/document.xml").decode("utf8", "ignore")
    except Exception as e:
        return _reject("corrupt", f"DOCX is damaged ({type(e).__name__})")
    if xml.count("<w:t") == 0 or len(xml) < 200:
        return _reject("empty_or_image_only", "DOCX has no text")
    return IntakeResult(True, "docx")
