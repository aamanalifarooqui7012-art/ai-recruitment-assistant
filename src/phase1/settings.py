"""Phase 1 settings = the `phase1` section of config/config.yaml. Paths are relative to the project root."""
from pathlib import Path

from project_config import ROOT, section

SETTINGS = section("phase1")


def path(key: str) -> Path:
    """Absolute path for an entry under `phase1.paths` in config/config.yaml."""
    return ROOT / SETTINGS["paths"][key]
