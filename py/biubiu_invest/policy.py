from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .paths import repo_root


@dataclass(frozen=True)
class Policy:
    id: str
    name: str
    description: str
    mode: str  # e.g. research_only
    require_human_confirmation_for_live_trade: bool = True
    allow_live_trading: bool = False
    allow_broker_api_connection: bool = False
    allow_external_network_fetch: bool = True
    allow_browser_automation: bool = True


def _as_bool(d: dict[str, Any], k: str, default: bool) -> bool:
    v = d.get(k, default)
    return bool(v)


def load_policy(policy_path: Optional[str] = None) -> Policy:
    """
    Load a jurisdiction policy JSON.

    - Default: policies/default_strict.json
    - No third-party deps (keeps sample mode zero-deps).
    """
    if policy_path:
        p = Path(policy_path).expanduser().resolve()
    else:
        p = repo_root() / "policies" / "default_strict.json"

    data = json.loads(p.read_text(encoding="utf-8"))
    return Policy(
        id=str(data.get("id") or p.stem),
        name=str(data.get("name") or p.stem),
        description=str(data.get("description") or ""),
        mode=str(data.get("mode") or "research_only"),
        require_human_confirmation_for_live_trade=_as_bool(
            data, "require_human_confirmation_for_live_trade", True
        ),
        allow_live_trading=_as_bool(data, "allow_live_trading", False),
        allow_broker_api_connection=_as_bool(data, "allow_broker_api_connection", False),
        allow_external_network_fetch=_as_bool(data, "allow_external_network_fetch", True),
        allow_browser_automation=_as_bool(data, "allow_browser_automation", True),
    )


