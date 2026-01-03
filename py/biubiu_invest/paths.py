from __future__ import annotations

from pathlib import Path


def repo_root() -> Path:
    # repo_root/py/biubiu_invest/paths.py -> repo_root
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    return repo_root() / "data"


def reports_dir() -> Path:
    return repo_root() / "reports"


def default_db_path() -> Path:
    return data_dir() / "biubiu.db"


def audit_log_path() -> Path:
    return repo_root() / "data" / "audit.jsonl"


