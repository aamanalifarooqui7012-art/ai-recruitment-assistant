"""Phase 2 settings = the `phase2` section of config/config.yaml (or a yaml file you pass in, same shape as that section)."""
import copy
from pathlib import Path

import yaml

from project_config import CONFIG_PATH as DEFAULT_PATH, section


def load_config(path=None) -> dict:
    if path and Path(path).resolve() != Path(DEFAULT_PATH).resolve():
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("phase2", data)          # a full project config file or a file with just the phase2 settings
    return copy.deepcopy(section("phase2"))          # a copy: callers (the workflow) edit paths in it
