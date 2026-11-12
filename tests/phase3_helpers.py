import asyncio
import json
from pathlib import Path

from interview.config import PROJECT_ROOT, load_settings
from interview.fakes import OfflineDemoLLM
from interview.schemas import ScaffoldRequest
from interview.services import build_services
from interview.store import JsonFileStore

DATA = PROJECT_ROOT / "samples" / "interview"


def load(name: str):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def sample_request() -> ScaffoldRequest:
    """JD text + Phase 1 skills, resume text + Phase 2 keywords and layout: exactly what the earlier phases hand over."""
    p1 = load("sample_phase1.json")
    return ScaffoldRequest(
        jd_text=p1["jd_text"], resume_text=(DATA / "sample_resume.txt").read_text(encoding="utf-8"),
        jd_skills=p1["skills"], resume_keywords=load("sample_phase2_keywords.json"),
        resume_layout=load("sample_phase2_layout.json"),
    )


def demo_llm() -> OfflineDemoLLM:
    return OfflineDemoLLM()


def make_svc(tmp_path: Path, llm=None, cfg=None):
    cfg = cfg or load_settings()
    return build_services(cfg=cfg, llm=llm or demo_llm(), store=JsonFileStore(tmp_path / "out"))


def run(coro):
    return asyncio.run(coro)
