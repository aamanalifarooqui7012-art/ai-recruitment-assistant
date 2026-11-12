"""Step 4: mask personal details with regex. Originals go to a separate store, never into the layout."""
import re
from urllib.parse import urlparse

from .config import load_config
from .models import PIIItem
from .textutil import body_font_size

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s,;|)\]]+|(?<![\w@.-])(?:linkedin\.com|github\.com|gitlab\.com|bitbucket\.org|behance\.net|"
    r"kaggle\.com|colab\.research\.google\.com|drive\.google\.com|huggingface\.co|medium\.com|facebook\.com|"
    r"instagram\.com|twitter\.com)/[^\s,;|)\]]+", re.I)
PHONE_RE = re.compile(r"(?<![\w.])\+?\d[\d\s().-]{8,16}\d(?!\w)")
PIN_RE = re.compile(r"\b[1-9]\d{5}\b")
PLACEHOLDER_RE = re.compile(r"\[(?:EMAIL|PHONE|URL|PINCODE|NAME)(?:_\d+)?\]")
JOB_WORDS = {"engineer", "developer", "manager", "analyst", "designer", "consultant", "student", "intern",
             "resume", "curriculum", "vitae", "architect", "lead", "scientist", "administrator", "specialist"}


def normalise_url(u: str) -> str:
    u = u.strip().rstrip(".,;:)")
    return u if re.match(r"https?://", u, re.I) else "https://" + u


def _url_key(u: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", normalise_url(u).lower()).rstrip("/")


def is_personal_url(u: str, priv: dict) -> bool:
    """Profile pages identify the person and are masked; project links (repo, notebook, article) are shown."""
    if u.lower().startswith(("mailto:", "tel:")):
        return True
    p = urlparse(normalise_url(u))
    host = p.netloc.lower().removeprefix("www.")
    segs = [x for x in p.path.split("/") if x]
    on = lambda domains: any(host == d or host.endswith("." + d) for d in domains)
    return on(priv["mask_domains"]) or (on(priv["profile_hosts"]) and len(segs) <= 1)


def _add_link(b, url: str):
    url = normalise_url(url)
    if _url_key(url) not in {_url_key(x) for x in b.links}:
        b.links.append(url)


def _is_phone(s: str) -> bool:
    digits = re.sub(r"\D", "", s)
    if not 10 <= len(digits) <= 13:
        return False
    groups = re.findall(r"\d+", s)
    return not (len(groups) >= 2 and all(len(g) == 4 and g[:2] in ("19", "20") for g in groups))


def _looks_like_name(text: str) -> bool:
    words = text.replace(",", " ").split()
    if not 2 <= len(words) <= 4:
        return False
    if any(not re.fullmatch(r"[A-Za-z][A-Za-z.'\-]*", w) for w in words):
        return False
    return not any(w.lower().strip(".") in JOB_WORDS for w in words)


def mask_pii(blocks, cfg=None):
    """blocks must be in reading order. Mutates block.text; returns (items, notes)."""
    cfg = cfg or load_config()
    priv = cfg["privacy"]
    items, counts, notes = [], {}, []

    def new_item(kind, original, b):
        counts[kind] = counts.get(kind, 0) + 1
        ph = f"[{kind}_{counts[kind]}]"
        items.append(PIIItem(ph, kind, original, b.id))
        return ph

    def url_sub(b):
        def repl(m):
            raw, trail = m.group(0), ""
            while raw and raw[-1] in ".,;:)":
                trail, raw = raw[-1] + trail, raw[:-1]
            if is_personal_url(raw, priv):
                return new_item("URL", raw, b) + trail
            _add_link(b, raw)            # project/work link: stays visible in the text and is listed as a link
            return m.group(0)
        b.text = URL_RE.sub(repl, b.text)

    def sub(kind, regex, b, check=None):
        def repl(m):
            if check and not check(m.group(0)):
                return m.group(0)
            counts[kind] = counts.get(kind, 0) + 1
            ph = f"[{kind}_{counts[kind]}]"
            items.append(PIIItem(ph, kind, m.group(0), b.id))
            return ph
        b.text = regex.sub(repl, b.text)

    for b in blocks:
        for uri in list(b.links):        # links hidden behind clickable text (e.g. "Colab notebook")
            if is_personal_url(uri, priv):
                b.links.remove(uri)
                new_item("EMAIL" if uri.lower().startswith("mailto:") else "URL", uri, b)
        sub("EMAIL", EMAIL_RE, b)
        url_sub(b)
        sub("PHONE", PHONE_RE, b, _is_phone)
        if len(b.text.split()) <= 15 and ("," in b.text or "india" in b.text.lower()):
            sub("PINCODE", PIN_RE, b)

    # Name: biggest first-page block near the top, else a title-case first line (flagged as a guess).
    body = body_font_size(blocks)
    first = [b for b in blocks if b.page == 1 and not PLACEHOLDER_RE.search(b.text)]
    cand = None
    for b in sorted(first[:15], key=lambda b: -b.font_size):   # biggest text near the top, any column
        if (b.font_size >= 1.15 * body or b.style.lower() == "title") and _looks_like_name(b.text):
            cand = b
            break
    guess = False
    if cand is None and first and _looks_like_name(first[0].text) and (first[0].bold or first[0].text.isupper() or first[0].text.istitle()):
        cand, guess = first[0], True
    if cand is None:
        notes.append("name_not_found")
    else:
        cand.flags.append("name")
        if not priv.get("show_name", True):
            counts["NAME"] = 1
            items.append(PIIItem("[NAME]", "NAME", cand.text, cand.id))
            cand.text = "[NAME]"
        if guess:
            cand.flags.append("name_guess")
            notes.append("name_guessed")
    return items, notes


def unmask(text: str, items, block_id: int) -> str:
    for it in items:
        if it.block_id == block_id:
            text = text.replace(it.placeholder, it.original)
    return text
