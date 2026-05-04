from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.data.store import DataStore


@pytest.fixture
def tmp_db() -> Path:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        return Path(f.name)


@pytest.fixture
def store(tmp_db: Path) -> DataStore:
    return DataStore(tmp_db)
