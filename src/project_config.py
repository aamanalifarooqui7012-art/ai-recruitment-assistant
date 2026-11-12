"""The ONE place that reads config/config.yaml. Every phase asks for its own section:

    phase1 | phase2 | scoring | phase3a | phase3b

Override the file with the env variable PROJECT_CONFIG (a path). Paths inside the file are relative to the project root.
API keys are not stored here: the file only names the .env variable (api_key_env).
"""
import os
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]                  # repo root (this file is src/project_config.py)
CONFIG_PATH = ROOT / "config" / "config.yaml"
TAXONOMY_PATH = ROOT / "config" / "skills_taxonomy.yaml"    # data (the skill list), shared by every phase


def config_path() -> Path:
    return Path(os.environ.get("PROJECT_CONFIG") or CONFIG_PATH)


@lru_cache(maxsize=4)
def _load(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_project_config(path=None) -> dict:
    """The whole file as a dict (cached per path)."""
    return _load(str(path or config_path()))


def section(name: str, path=None) -> dict:
    """One section, e.g. section('phase2'). A deep copy-free read: treat the result as read-only, copy it before changing it."""
    data = load_project_config(path)
    if name not in data:
        raise KeyError(f"Section '{name}' is missing in {path or config_path()}")
    return data[name]
