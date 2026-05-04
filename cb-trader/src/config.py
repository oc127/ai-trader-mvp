from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


def _deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(config_dir: Path | None = None, environment: str | None = None) -> dict[str, Any]:
    load_dotenv()

    if config_dir is None:
        config_dir = Path(__file__).parent.parent / "config"

    default_path = config_dir / "default.yaml"
    with open(default_path) as f:
        config = yaml.safe_load(f)

    if environment:
        env_path = config_dir / f"{environment}.yaml"
        if env_path.exists():
            with open(env_path) as f:
                env_config = yaml.safe_load(f) or {}
            config = _deep_merge(config, env_config)

    return config
