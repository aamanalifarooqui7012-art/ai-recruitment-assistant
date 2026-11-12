"""Transcript reading. Deterministic code, no LLM. (Moved from the standalone Phase 3B app; logic unchanged.)"""
import io
import re

from .errors import EvalError

MAX_CHARS = 400_000
MAX_FILE_BYTES = 10 * 1024 * 1024
CHUNK_CHARS = 6000


def read_upload(name: str, data: bytes) -> str:
    if len(data) > MAX_FILE_BYTES:
        raise EvalError("The file is larger than 10 MB.")
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext == "docx":
        try:
            from docx import Document
            doc = Document(io.BytesIO(data))
        except Exception:
            raise EvalError("Could not read the DOCX. The file may be corrupt.")
        return "\n".join(p.text for p in doc.paragraphs)
    if ext in ("vtt", "txt"):
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return data.decode("cp1252", errors="replace")
    raise EvalError("Only .vtt, .docx or .txt files are allowed.")


def parse_vtt(text: str):
    lines = text.replace("\r", "").split("\n")
    out, i = [], 0
    while i < len(lines):
        if "-->" in lines[i] and re.search(r"\d:\d\d", lines[i]):
            ts = lines[i].split("-->")[0].strip()
            i += 1
            buf = []
            while i < len(lines) and lines[i].strip() != "":
                buf.append(lines[i].strip())
                i += 1
            s, sp = " ".join(buf), "Unknown"
            m = re.match(r"^<v\s+([^>]+)>(.*)$", s)
            if m:
                sp, s = m.group(1).strip(), m.group(2)
            else:
                m = re.match(r"^([^:]{1,40}):\s+(.*)$", s)
                if m:
                    sp, s = m.group(1).strip(), m.group(2)
            s = re.sub(r"<[^>]+>", "", s).strip()
            if s:
                out.append({"speaker": sp, "ts": ts, "text": s})
        else:
            i += 1
    return out


def parse_text(text: str):
    out, pend = [], None
    for raw in text.replace("\r", "").split("\n"):
        l = raw.strip()
        if not l or re.match(r"^WEBVTT", l, re.I):
            continue
        m = re.match(r"^\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?\s+([^:]{1,40}):\s*(.*)$", l)
        if m:
            out.append({"speaker": m.group(2).strip(), "ts": m.group(1), "text": m.group(3)})
            pend = None
            continue
        m = re.match(r"^([^:]{1,40}):\s+(.*)$", l)
        if m:
            out.append({"speaker": m.group(1).strip(), "ts": "", "text": m.group(2)})
            pend = None
            continue
        m = re.match(r"^(.{1,40}?)\s+\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?$", l)
        if m:
            pend = {"speaker": m.group(1).strip(), "ts": m.group(2)}
        elif pend:
            out.append({**pend, "text": l})
            pend = None
        elif out:
            out[-1]["text"] += " " + l
    return [t for t in out if t["text"]]


def unique_speakers(turns):
    return list(dict.fromkeys(t["speaker"] for t in turns))


def parse_transcript(text: str):
    if not text.strip():
        raise EvalError("The transcript is empty. Upload a file or paste the text.")
    if len(text) > MAX_CHARS:
        raise EvalError("The transcript is too large (over 400,000 characters).")
    turns = parse_vtt(text) if "-->" in text else parse_text(text)
    if not turns:
        raise EvalError("The transcript could not be parsed. Use 'Speaker: text' lines or a standard .vtt file.")
    names = unique_speakers(turns)
    if len(names) > 6:
        raise EvalError(f"{len(names)} speakers found; the format was probably parsed wrongly. "
                        "Try a transcript with fewer than 6 speakers.")
    return turns


def default_roles(names):
    guess = lambda n: bool(re.search(r"interview|hr|recruit|panel", n, re.I))
    any_guess = any(guess(n) for n in names)
    return {n: "Interviewer" if (guess(n) or (n == names[0] and not any_guess)) else "Candidate"
            for n in names}


def norm(s: str) -> str:
    s = str(s).lower()
    s = s.replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    s = re.sub(r"[^a-z0-9' ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def chunk_turns(turns):
    chunks, cur, n = [], [], 0
    for t in turns:
        ln = len(t["text"]) + len(t["speaker"]) + 12
        if cur and n + ln > CHUNK_CHARS:
            chunks.append(cur)
            cur, n = [], 0
        cur.append(t)
        n += ln
    if cur:
        chunks.append(cur)
    return chunks
