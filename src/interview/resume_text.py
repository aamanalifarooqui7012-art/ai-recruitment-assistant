"""Phase 2 layout json -> plain resume text for Phase 3A.

Phase 3A reads the resume exactly as Phase 2 standardised it (same text, PII already masked), so nobody has to
upload the resume a second time. Only real resume lines are written: no parsed-date lines, no link lines, so every
quote the model gives can be checked against this text.
"""
from resume_std.render import TITLES
from resume_std.textutil import BULLET_RE


def layout_to_text(layout: dict) -> str:
    out: list[str] = []
    for section in layout.get("sections", []):
        out.append(f"\n{TITLES.get(section['name'], section['name'].title()).upper()}")
        for entry in section.get("entries", []):
            for b in entry.get("blocks", []):
                if b.get("is_heading") and b.get("label_source") == "heading":
                    continue                                   # the original heading is replaced by the title above
                text = b["text"].strip()
                if text:
                    out.append(("- " if b.get("is_bullet") and not BULLET_RE.match(text) else "") + text)
    return "\n".join(out).strip()
