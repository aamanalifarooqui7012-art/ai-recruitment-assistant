import os
import re
import tempfile
from pathlib import Path

from .config import resolve_path
from .schemas import InterviewScaffold

_ID = re.compile(r"^[0-9a-f]{32}$")  # ids are uuid4 hex; this also blocks path tricks


class JsonFileStore:
    """Plain JSON file per scaffold. Swap for the HRMS database later (same 3 methods)."""

    def __init__(self, output_dir: str | Path):
        self.dir = resolve_path(output_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def save(self, scaffold: InterviewScaffold) -> Path:
        if not _ID.match(scaffold.scaffold_id):
            raise ValueError("invalid scaffold_id")
        target = self.dir / f"{scaffold.scaffold_id}.json"
        fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(scaffold.model_dump_json(indent=2))
        os.replace(tmp, target)  # atomic: never a half-written file
        return target

    def get(self, scaffold_id: str) -> InterviewScaffold | None:
        if not _ID.match(scaffold_id):
            return None
        path = self.dir / f"{scaffold_id}.json"
        if not path.exists():
            return None
        return InterviewScaffold.model_validate_json(path.read_text(encoding="utf-8"))

    def list_ids(self) -> list[str]:
        return sorted(p.stem for p in self.dir.glob("*.json") if _ID.match(p.stem))


class NullStore:
    """Used when the workflow runs with save_outputs=False: nothing is written, the scaffold is only returned."""

    dir = None

    def save(self, scaffold: InterviewScaffold) -> None:
        return None

    def get(self, scaffold_id: str) -> InterviewScaffold | None:
        return None

    def list_ids(self) -> list[str]:
        return []
