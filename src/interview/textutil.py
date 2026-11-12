"""Pure text helpers (no pydantic, no LLM). Quote verification lives here:
it is what turns 'the model says so' into 'the text really says so'."""
import re
import unicodedata

_BULLET_LINE = re.compile(r"(?m)^[ \t]*[-*\u2022\u25aa\u25cf\u25e6\u00b7\u25a0\u25c6\u25ba]+[ \t]+")
_WS = re.compile(r"\s+")
_ELLIPSIS = re.compile(r"\.\.\.|\u2026")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<![\w.])\+?\d[\d\s().-]{8,}\d(?![\w])")
_QUOTE_TABLE = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u00a0": " ",
})


def clean_text(text: str) -> str:
    """Normalise newlines and strip control noise. Content is not changed."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def mask_pii(text: str) -> str:
    """Mask emails and phone numbers before text goes to an LLM.
    (Names cannot be masked reliably with a regex; see README.)"""
    text = _EMAIL.sub("[EMAIL]", text)

    def _phone(m: re.Match) -> str:
        digits = sum(c.isdigit() for c in m.group(0))
        return "[PHONE]" if 10 <= digits <= 15 else m.group(0)

    return _PHONE.sub(_phone, text)


def normalise_for_match(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).translate(_QUOTE_TABLE)
    t = _BULLET_LINE.sub(" ", t)
    return _WS.sub(" ", t).strip().casefold()


_NON_ALNUM = re.compile(r"[\W_]+")
_BULLET_GLYPHS = "-*\u2022\u25aa\u25cf\u25e6\u00b7\u25a0\u25c6\u25ba"
_SEGMENT_SPLIT = re.compile(r"\n|[\u2022\u25aa\u25cf\u25e6\u25a0\u25c6\u25ba]")


def squash(text: str) -> str:
    """Only letters and digits, lowercase. Makes matching immune to the spacing/punctuation
    noise PDFs produce ('Neo4j ,  enabling' vs 'Neo4j, enabling'), while invented
    wording still cannot match because the letter sequence differs."""
    return _NON_ALNUM.sub("", unicodedata.normalize("NFKC", text).casefold())


def quote_in_text(quote: str, source: str, min_len: int = 8) -> bool:
    """True if `quote` really occurs in `source` (ignoring case, spacing, punctuation, bullets).
    A quote written with '...' is split and every part must occur, in order."""
    if not quote or not source:
        return False
    parts = [squash(p) for p in _ELLIPSIS.split(normalise_for_match(quote))]
    parts = [p for p in parts if len(p) >= 4]
    if not parts or max(len(p) for p in parts) < min_len:
        return False
    src = squash(source)
    pos = 0
    for part in parts:
        idx = src.find(part, pos)
        if idx < 0:
            return False
        pos = idx + len(part)
    return True


def word_set(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9+#.]+", normalise_for_match(text)))


def jaccard(a: str, b: str) -> float:
    wa, wb = word_set(a), word_set(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def segments(source: str) -> list[str]:
    """Bullets / lines of the source, used to snap a slightly-off quote to real text."""
    out = []
    for seg in _SEGMENT_SPLIT.split(source):
        seg = _WS.sub(" ", seg).strip()
        if len(seg) >= 8:
            out.append(seg)
    return out


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", normalise_for_match(text)))


def snap_quote(quote: str, source: str, min_containment: float = 0.7, max_chars: int = 300) -> str | None:
    """If the model's quote is slightly off (wording drifted), return the REAL source line it
    most likely came from. Invented quotes share too few words with any line and give None."""
    q = _tokens(quote)
    if len(q) < 3:
        return None
    best, best_score = None, 0.0
    for seg in segments(source):
        score = len(q & _tokens(seg)) / len(q)
        if score > best_score:
            best, best_score = seg, score
    if best is None or best_score < min_containment:
        return None
    return best[:max_chars]


def find_mention(terms: list[str], source: str, max_chars: int = 300) -> str | None:
    """First real source line that contains one of `terms` as a whole word (code-side search)."""
    pats = [
        re.compile(r"(?<![A-Za-z0-9])" + re.escape(t) + r"(?![A-Za-z0-9])", re.IGNORECASE)
        for t in terms
        if len(t.strip()) >= 3
    ]
    for seg in segments(source):
        if any(p.search(seg) for p in pats):
            return seg[:max_chars]
    return None
