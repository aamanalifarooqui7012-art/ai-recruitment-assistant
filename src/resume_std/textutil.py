"""Small shared helpers: text cleaning, regexes, date parsing, font stats."""
import re
from collections import Counter
from datetime import date

_LIGATURES = {"\ufb01": "fi", "\ufb02": "fl", "\ufb00": "ff", "\ufb03": "ffi", "\ufb04": "ffl",
              "\ufb05": "st", "\ufb06": "st"}
_INVISIBLE = dict.fromkeys(map(ord, "\u00ad\u200b\u200c\u200d\ufeff\x00"), None)


def clean_text(t: str) -> str:
    """The only normalisation applied to extracted text: ligatures, invisible chars, whitespace."""
    for k, v in _LIGATURES.items():
        t = t.replace(k, v)
    t = t.translate(_INVISIBLE)
    return re.sub(r"\s+", " ", t).strip()


def nonspace_len(t: str) -> int:
    return len(re.sub(r"\s", "", t))


def tokens(t: str):
    return t.split()


def token_counter(texts) -> Counter:
    c = Counter()
    for t in texts:
        c.update(t.split())
    return c


BULLET_RE = re.compile(
    r"^\s*(?:[\u2022\u25cf\u25e6\u25aa\u25a0\u25a1\u25ab\u27a2\u27a4\u2713\u2714\u25ba\u2023\u2219\u25cb\u00b7*"
    r"\uf000-\uf8ff]|[-\u2013\u2014](?=\s)|\(?\d{1,2}[.)](?=\s)|[a-z][.)](?=\s))")

MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
          "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12}
_MON = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE = rf"(?:(?:{_MON}\s+|\d{{1,2}}[/-])?(?:19|20)\d{{2}})"
_PRESENT = r"(?:present|current|now|ongoing|today|till date|to date)"
DATE_RANGE_RE = re.compile(
    rf"(?P<s>{_DATE})\s*(?:-|\u2013|\u2014|\u2212|to|until|till)\s*(?P<e>{_PRESENT}|{_DATE})", re.I)
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def parse_month_year(s: str):
    """'Jan 2020' / '03/2020' / '2020' -> 'YYYY-MM' (year only -> 'YYYY-01'), or None."""
    s = s.strip().lower()
    if re.fullmatch(_PRESENT, s):
        return "present"
    m = re.fullmatch(r"((?:19|20)\d{2})", s)
    if m:
        return f"{m.group(1)}-01"
    m = re.fullmatch(r"(\d{1,2})[/-]((?:19|20)\d{2})", s)
    if m and 1 <= int(m.group(1)) <= 12:
        return f"{m.group(2)}-{int(m.group(1)):02d}"
    m = re.fullmatch(rf"({_MON})\s+((?:19|20)\d{{2}})", s)
    if m:
        mon = MONTHS.get(re.sub(r"[^a-z]", "", m.group(1))[:4]) or MONTHS.get(re.sub(r"[^a-z]", "", m.group(1))[:3])
        if mon:
            return f"{m.group(2)}-{mon:02d}"
    return None


def parse_date_range(text: str):
    m = DATE_RANGE_RE.search(text)
    if not m:
        return None
    s, e = parse_month_year(m.group("s")), parse_month_year(m.group("e"))
    if s is None or e is None:
        return None
    return {"start": s, "end": e}


def body_font_size(blocks) -> float:
    """Most common font size weighted by characters = the body text size."""
    c = Counter()
    for b in blocks:
        if b.font_size:
            c[round(b.font_size * 2) / 2] += len(b.text)
    return c.most_common(1)[0][0] if c else 10.0


def norm_phrase(t: str) -> str:
    t = t.lower().replace("&", " and ")
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()
