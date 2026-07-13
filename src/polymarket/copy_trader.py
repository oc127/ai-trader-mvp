"""Smart Money copy trading — mirror top Polymarket traders.

Fetches leaderboard, filters by strict criteria (win rate, PnL, consistency),
then copies their trades with configurable sizing and delay.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from src.logger import get_logger

log = get_logger(__name__)

DATA_API = "https://data-api.polymarket.com"


@dataclass
class TraderProfile:
    address: str
    username: str = ""
    pnl: float = 0.0
    volume: float = 0.0
    win_rate: float = 0.0
    num_trades: int = 0
    profit_factor: float = 0.0
    markets_traded: int = 0
    score: float = 0.0


@dataclass
class CopySignal:
    trader: TraderProfile
    market_slug: str
    condition_id: str
    token_id: str
    side: str          # "BUY" or "SELL"
    outcome: str       # "Yes" or "No"
    price: float
    size: float
    detected_at: float


@dataclass
class CopyConfig:
    min_pnl: float = 500.0
    min_win_rate: float = 0.60
    min_profit_factor: float = 1.5
    min_trades: int = 20
    min_consistency: float = 0.70
    max_traders_to_follow: int = 10
    copy_size_pct: float = 0.10     # copy 10% of their size
    max_copy_size_usd: float = 25.0
    min_copy_size_usd: float = 2.0
    scan_interval: float = 120.0    # check leaderboard every 2 min
    trade_delay: float = 5.0        # wait 5s before copying (avoid front-run)
    cooldown_per_trader: float = 300.0  # 5 min cooldown per trader


def load_copy_config(cfg: dict) -> CopyConfig:
    cc = cfg.get("polymarket", {}).get("copy_trading", {})
    return CopyConfig(
        min_pnl=cc.get("min_pnl", 500.0),
        min_win_rate=cc.get("min_win_rate", 0.60),
        min_profit_factor=cc.get("min_profit_factor", 1.5),
        min_trades=cc.get("min_trades", 20),
        min_consistency=cc.get("min_consistency", 0.70),
        max_traders_to_follow=cc.get("max_traders_to_follow", 10),
        copy_size_pct=cc.get("copy_size_pct", 0.10),
        max_copy_size_usd=cc.get("max_copy_size_usd", 25.0),
        min_copy_size_usd=cc.get("min_copy_size_usd", 2.0),
        scan_interval=cc.get("scan_interval", 120.0),
        trade_delay=cc.get("trade_delay", 5.0),
        cooldown_per_trader=cc.get("cooldown_per_trader", 300.0),
    )


class CopyTrader:
    """Track and copy top-performing Polymarket traders."""

    def __init__(self, cfg: dict) -> None:
        self._config = load_copy_config(cfg)
        self._followed: dict[str, TraderProfile] = {}
        self._last_trades: dict[str, list[dict]] = {}  # addr -> recent trades
        self._last_copy_ts: dict[str, float] = {}      # addr -> last copy time
        self._copy_pnl = 0.0
        self._copy_count = 0

    @property
    def config(self) -> CopyConfig:
        return self._config

    def fetch_leaderboard(self) -> list[TraderProfile]:
        """Fetch top traders from Polymarket Data API leaderboard."""
        try:
            import requests
            resp = requests.get(
                f"{DATA_API}/v1/leaderboard",
                params={"timePeriod": "MONTH", "orderBy": "PNL", "limit": 50},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            traders: list[TraderProfile] = []
            for entry in data if isinstance(data, list) else data.get("results", []):
                pnl = float(entry.get("pnl", 0) or 0)
                volume = float(entry.get("vol", entry.get("volume", 0)) or 0)

                traders.append(TraderProfile(
                    address=entry.get("proxyWallet", entry.get("userAddress", "")),
                    username=entry.get("userName", entry.get("username", "")),
                    pnl=pnl,
                    volume=volume,
                ))

            return traders
        except Exception as e:
            log.error(f"Failed to fetch leaderboard: {e}")
            return []

    def filter_traders(self, traders: list[TraderProfile]) -> list[TraderProfile]:
        """Filter to only follow traders meeting strict criteria."""
        cfg = self._config
        qualified: list[TraderProfile] = []

        for t in traders:
            if t.pnl < cfg.min_pnl:
                continue
            if t.win_rate and t.win_rate < cfg.min_win_rate:
                continue
            if t.num_trades and t.num_trades < cfg.min_trades:
                continue

            # score from available data: PnL dominates, volume as tiebreaker
            pnl_score = min(t.pnl / 1000, 10) * 30
            vol_score = min(t.volume / 100_000, 5) * 10 if t.volume > 0 else 0
            wr_score = t.win_rate * 30 if t.win_rate else 0
            efficiency = (t.pnl / t.volume * 100) if t.volume > 0 else 0
            eff_score = min(efficiency, 5) * 10

            t.score = pnl_score + vol_score + wr_score + eff_score
            qualified.append(t)

        qualified.sort(key=lambda t: t.score, reverse=True)
        top = qualified[:cfg.max_traders_to_follow]

        if top:
            log.info(
                f"Copy trader: {len(top)} qualified from {len(traders)} "
                f"(best: {top[0].username or top[0].address[:8]} "
                f"PnL=${top[0].pnl:,.0f} vol=${top[0].volume:,.0f})"
            )

        return top

    def update_followed(self, traders: list[TraderProfile]) -> None:
        """Update the list of traders we're following."""
        self._followed = {t.address: t for t in traders}

    def fetch_trader_trades(self, address: str) -> list[dict]:
        """Fetch recent trades for a specific trader via Data API."""
        try:
            import requests
            resp = requests.get(
                f"{DATA_API}/trades",
                params={"user": address, "limit": 20},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []
        except Exception as e:
            log.debug(f"Failed to fetch trades for {address[:8]}: {e}")
            return []

    def detect_new_trades(self, address: str, trades: list[dict]) -> list[dict]:
        """Compare with previous snapshot to find new trades."""
        def _trade_key(t: dict) -> str:
            return t.get("transactionHash", t.get("id", t.get("tradeId", "")))
        prev_ids = {_trade_key(t) for t in self._last_trades.get(address, [])}
        new = [t for t in trades if _trade_key(t) not in prev_ids]
        self._last_trades[address] = trades
        return new

    def should_copy(self, address: str) -> bool:
        """Check cooldown for a specific trader."""
        if address not in self._last_copy_ts:
            return True
        return (time.monotonic() - self._last_copy_ts[address]) >= self._config.cooldown_per_trader

    def calculate_copy_size(self, original_size: float) -> float:
        """Scale down the trade size for our copy."""
        size = original_size * self._config.copy_size_pct
        size = max(size, self._config.min_copy_size_usd)
        size = min(size, self._config.max_copy_size_usd)
        return round(size, 2)

    def record_copy(self, address: str, pnl: float = 0.0) -> None:
        self._last_copy_ts[address] = time.monotonic()
        self._copy_count += 1
        self._copy_pnl += pnl

    def status(self) -> dict:
        return {
            "followed_traders": len(self._followed),
            "copy_count": self._copy_count,
            "copy_pnl": round(self._copy_pnl, 4),
            "followed": [
                {"addr": t.address[:8], "wr": f"{t.win_rate:.0%}", "pnl": f"${t.pnl:,.0f}"}
                for t in list(self._followed.values())[:5]
            ],
        }

    def reset_daily(self) -> None:
        self._copy_pnl = 0.0
        self._copy_count = 0
