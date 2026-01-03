from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class AuditEvent:
    event_type: str  # e.g. report_generated
    ts_utc: str
    policy_id: str
    provider: str
    watchlist_path: str
    db_path: str
    out_path: str
    params: dict[str, Any]
    notes: Optional[str] = None


def write_audit_event(audit_path: Path, evt: AuditEvent) -> None:
    """
    Append-only JSONL audit log for replay/traceability.
    Keep it simple and dependency-free.
    """
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(evt), ensure_ascii=False) + "\n")


def make_report_event(
    *,
    policy_id: str,
    provider: str,
    watchlist_path: str,
    db_path: str,
    out_path: str,
    params: dict[str, Any],
    notes: Optional[str] = None,
) -> AuditEvent:
    return AuditEvent(
        event_type="report_generated",
        ts_utc=_utc_now_iso(),
        policy_id=policy_id,
        provider=provider,
        watchlist_path=watchlist_path,
        db_path=db_path,
        out_path=out_path,
        params=params,
        notes=notes,
    )


