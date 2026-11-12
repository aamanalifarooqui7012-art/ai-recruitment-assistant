"""Step 5: label every block with a section, by rules only. Never drops or edits a block.

Order of evidence:
  1. Known headings (dictionary + fuzzy match), including inline "Skills: Python, Java".
  2. Unknown headings, only if they match the visual style of this file's known headings.
  3. Headless stretches (before the first heading, or after a column switch): classify by content.
Every label records how it was made (label_source) and a confidence."""
import re

from rapidfuzz import fuzz, process

from .mask_pii import PLACEHOLDER_RE
from .textutil import DATE_RANGE_RE, YEAR_RE, body_font_size, norm_phrase


class HeadingIndex:
    def __init__(self, cfg):
        self.threshold = cfg["label"]["heading_fuzzy_threshold"]
        self.phrases, self.sections = [], []
        for sec, plist in cfg["headings"].items():
            for p in plist:
                self.phrases.append(norm_phrase(p))
                self.sections.append(sec)

    def match(self, text: str):
        n = norm_phrase(text)
        if not n:
            return None
        if n in self.phrases:
            return self.sections[self.phrases.index(n)], 100.0
        hit = process.extractOne(n, self.phrases, scorer=fuzz.ratio, score_cutoff=self.threshold)
        return (self.sections[hit[2]], float(hit[1])) if hit else None


def _sig(b):
    heading_style = b.style.lower().startswith(("heading", "title"))
    return (round(b.font_size * 2) / 2, b.bold, b.text.isupper(), heading_style)


def _items(text):
    return [x.strip() for x in re.split(r"[,|;\u2022/]|\s\u00b7\s", text) if x.strip()]


def is_skill_list(text, vocab) -> bool:
    items = _items(text)
    if len(items) < 3:
        return False
    short = sum(1 for i in items if len(i.split()) <= 3)
    hits = sum(1 for i in items if i.lower().strip(".") in vocab)
    return short / len(items) >= 0.7 and hits >= 1 and hits / len(items) >= 0.3


def classify_content(b, cfg, vocab, seg_idx=99, prev_section=None):
    """Content-only guess for a block with no heading context. Returns (section, confidence)."""
    t, words = b.text, b.text.split()
    low = t.lower()
    if "name" in b.flags:
        return "contact", 0.95
    has_range = bool(DATE_RANGE_RE.search(t))
    has_year = bool(YEAR_RE.search(t))
    edu = any(re.search(rf"(?<![\w.]){re.escape(w)}(?![\w])", low) for w in cfg["education_words"])
    cert = any(w in low for w in cfg["cert_words"])
    if PLACEHOLDER_RE.search(t):
        return "contact", 0.95
    if edu and (has_year or has_range):
        return "education", 0.8
    if has_range:
        return "experience", 0.7
    if edu:
        return "education", 0.65
    if cert:
        return "certifications", 0.65
    if is_skill_list(t, vocab):
        return "skills", 0.7
    if len(words) >= 15 and re.search(r"[.!]", t) and not has_year and not b.is_bullet:
        return "summary", 0.6
    if seg_idx <= 3 and prev_section == "contact" and len(words) <= 14 and not has_year and not b.is_bullet \
            and not b.flags.count("headline_used"):
        return "contact", 0.6          # headline under the name, e.g. "Backend Developer"
    return "other", 0.3


def label_blocks(blocks, cfg):
    """blocks in reading order. Mutates block label fields. Returns stats dict."""
    lcfg = cfg["label"]
    hidx = HeadingIndex(cfg)
    vocab = {v.lower() for v in cfg["skills_vocab"]}
    body = body_font_size(blocks)

    known, inline = {}, {}
    for b in blocks:
        if b.is_bullet or "name" in b.flags or any(f == "hidden_text" for f in b.flags):
            continue
        words = b.text.split()
        if ":" in b.text:
            prefix, rest = b.text.split(":", 1)
            if rest.strip() and len(prefix.split()) <= 4:
                m = hidx.match(prefix)
                if m:
                    inline[b.id] = m
                    continue
        first_alpha = next((c for c in b.text if c.isalpha()), "a")
        if (len(words) <= lcfg["max_heading_words"] and len(b.text) <= 45 and not PLACEHOLDER_RE.search(b.text)
                and first_alpha.isupper()):     # a lowercase fragment like "project." is not a heading
            m = hidx.match(b.text)
            if m:
                known[b.id] = m

    # learn this file's heading style from the known headings, keep it only if it is distinctive
    sig_known, sig_all = {}, {}
    for b in blocks:
        s = _sig(b)
        sig_all[s] = sig_all.get(s, 0) + 1
        if b.id in known:
            sig_known[s] = sig_known.get(s, 0) + 1
    # A style only counts as "heading style" if it stands out from body text (bigger or ALL CAPS) and is
    # mostly used by known headings. Same-size bold is how entry titles look, so it is too ambiguous.
    heading_sigs = {s for s, n in sig_known.items()
                    if n / sig_all[s] >= lcfg["heading_style_exclusive_ratio"] and (s[0] > body * 1.1 or s[2])}

    unknown = set()
    for b in blocks:
        if (b.id not in known and b.id not in inline and not b.is_bullet and "name" not in b.flags
                and len(b.text.split()) <= lcfg["max_heading_words"] + 1 and len(b.text) <= 50
                and not YEAR_RE.search(b.text) and not PLACEHOLDER_RE.search(b.text)
                and _sig(b) in heading_sigs):
            unknown.add(b.id)

    def put(b, section, source, conf, heading=False):
        b.section, b.label_source, b.confidence, b.is_heading = section, source, conf, heading

    current, seg, prev_col, prev_page = None, [], -1, None
    pending = []   # headless run being classified by content

    def flush_pending():
        prev_sec = None
        for i, pb in enumerate(pending):
            sec, conf = classify_content(pb, cfg, vocab, i, prev_sec)
            if sec == "other" and prev_sec in {"experience", "education", "projects", "certifications"}:
                sec, conf = prev_sec, 0.55   # bullets and detail lines continue the entry above them
            put(pb, sec, "inferred", conf)
            if sec == "contact" and conf == 0.6 and not PLACEHOLDER_RE.search(pb.text):
                for nxt in pending[i + 1:i + 2]:
                    nxt.flags.append("headline_used")      # only one headline line per header
            prev_sec = sec
        pending.clear()

    for b in blocks:
        if (prev_page == b.page and b.column != prev_col and b.column != -1 and prev_col != -1):
            flush_pending()
            current = None              # new column on the same page: its first lines have no heading context
        prev_col, prev_page = b.column, b.page
        if b.id in known:
            flush_pending()
            current = known[b.id][0]
            put(b, current, "heading", known[b.id][1] / 100, True)
        elif b.id in inline:
            flush_pending()
            current = inline[b.id][0]
            put(b, current, "heading_inline", inline[b.id][1] / 100 * 0.95, True)
        elif b.id in unknown:
            flush_pending()
            current = None                  # unknown heading: classify what follows by content
            put(b, "other", "heading_style", 0.45, True)
            b.flags.append("unknown_heading")
        elif current is None:
            pending.append(b)
        else:
            put(b, current, "section_context", 0.9)
            sec, conf = classify_content(b, cfg, vocab)
            edu_bad = (sec == "education" and conf >= 0.8 and
                       current not in {"education", "certifications", "achievements", "projects", "references", "other", "interests"})
            prose_bad = sec == "summary" and current in {"skills", "contact", "languages", "interests"}
            if edu_bad or prose_bad:
                b.confidence = 0.4
                b.flags.append(f"content_conflict:{sec}")
    flush_pending()

    total = sum(len(b.text) for b in blocks) or 1
    return {
        "known_headings": len(known) + len(inline),
        "unknown_headings": len(unknown),
        "other_ratio": sum(len(b.text) for b in blocks if b.section == "other") / total,
        "low_conf_ratio": sum(len(b.text) for b in blocks if b.confidence < lcfg["min_confidence"]) / total,
    }
