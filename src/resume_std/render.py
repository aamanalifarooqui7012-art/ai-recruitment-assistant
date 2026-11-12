"""Human-readable renderings of the layout JSON. Headings are replaced by fixed section titles
(a template, not resume text); every other block is printed as is."""
from .textutil import BULLET_RE

TITLES = {"contact": "Contact", "summary": "Summary", "experience": "Experience", "skills": "Skills",
          "projects": "Projects", "education": "Education", "certifications": "Certifications",
          "achievements": "Achievements", "languages": "Languages", "interests": "Interests",
          "references": "References", "other": "Other"}


def _visible(block):
    return not (block["is_heading"] and block["label_source"] == "heading")


def to_markdown(layout):
    out = [f"<!-- AI-generated layout, needs human review. status={layout['status']} -->"]
    for s in layout["sections"]:
        out.append(f"\n## {TITLES.get(s['name'], s['name'].title())}")
        for e in s["entries"]:
            if e["dates"]:
                out.append(f"\n*{e['dates']['start']} to {e['dates']['end']}*")
            for b in e["blocks"]:
                if _visible(b):
                    out.append(("- " if b["is_bullet"] and not BULLET_RE.match(b["text"]) else "") + b["text"])
                    out += [f"  Link: {u}" for u in b.get("links", [])]
    return "\n".join(out) + "\n"


def to_docx(layout, path):
    from docx import Document
    d = Document()
    d.add_paragraph(f"AI-generated layout, needs human review. Status: {layout['status']}").runs[0].italic = True
    for s in layout["sections"]:
        d.add_heading(TITLES.get(s["name"], s["name"].title()), level=1)
        for e in s["entries"]:
            for b in e["blocks"]:
                if _visible(b):
                    for u in b.get("links", []):
                        d.add_paragraph(f"Link: {u}")
                    d.add_paragraph(b["text"], style="List Bullet" if b["is_bullet"] and not BULLET_RE.match(b["text"]) else None)
    d.save(path)
