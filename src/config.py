from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_DIR = _ROOT / "config"


def load_config(env: str | None = None) -> dict[str, Any]:
    load_dotenv(_ROOT / ".env")
    env = env or os.getenv("ENVIRONMENT", "testnet")

    with open(_CONFIG_DIR / "default.yaml") as f:
        cfg = yaml.safe_load(f)

    override_path = _CONFIG_DIR / f"{env}.yaml"
    if override_path.exists():
        with open(override_path) as f:
            overrides = yaml.safe_load(f) or {}
        cfg = _deep_merge(cfg, overrides)

    return cfg


def _deep_merge(base: dict, override: dict) -> dict:
    merged = base.copy()
    for k, v in override.items():
        if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged
