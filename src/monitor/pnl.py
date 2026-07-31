from __future__ import annotations

from datetime import datetime, timezone

from src.data.store import DataStore
from src.hl_client.rest import HLRestClient
from src.logger import get_logger

log = get_logger(__name__)


class PnLTracker:
    def __init__(self, client: HLRestClient, store: DataStore) -> None:
        self._client = client
        self._store = store
        self._initial_equity: float | None = None
        self._last_daily_summary: str = ""
        self._paper_executor = None

    def set_paper_executor(self, executor) -> None:
        self._paper_executor = executor
        self._initial_equity = executor.balance

    def _get_equity(self) -> float:
        if self._paper_executor is not None:
            return self._paper_executor.get_equity()
        return self._client.get_account_state().equity

    def snapshot(self) -> dict:
        equity = self._get_equity()

        if self._initial_equity is None:
            self._initial_equity = equity

        if self._paper_executor is not None:
            positions = self._paper_executor.positions
            total_unrealized = 0.0
            available = self._paper_executor.balance
            margin_used = 0.0
            margin_util = 0.0
            num_positions = len(positions)
        else:
            account = self._client.get_account_state()
            total_unrealized = sum(p.unrealized_pnl for p in account.positions)
            available = account.available_balance
            margin_used = account.margin_used
            margin_util = account.margin_utilization
            num_positions = len(account.positions)

        snapshot = {
            "equity": equity,
            "available_balance": available,
            "unrealized_pnl": total_unrealized,
            "realized_pnl": equity - self._initial_equity - total_unrealized,
            "margin_used": margin_used,
            "margin_utilization": margin_util,
            "num_positions": num_positions,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        self._store.save_pnl_snapshot(
            equity=snapshot["equity"],
            available_balance=snapshot["available_balance"],
            unrealized_pnl=snapshot["unrealized_pnl"],
            realized_pnl=snapshot["realized_pnl"],
        )

        log.info("PnL snapshot", extra=snapshot)
        return snapshot

    def should_send_daily_summary(self) -> bool:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        hour = datetime.now(timezone.utc).hour
        if hour == 0 and today != self._last_daily_summary:
            return True
        return False

    def build_daily_summary(self) -> str:
        equity = self._get_equity()

        if self._initial_equity is None:
            self._initial_equity = equity

        total_return = equity - self._initial_equity
        return_pct = (total_return / self._initial_equity * 100) if self._initial_equity > 0 else 0

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._last_daily_summary = today

        lines = [
            f"*Hourly Report — {today}*",
            "",
            f"Equity: ${equity:,.2f}",
            f"Return: ${total_return:+,.2f} ({return_pct:+.2f}%)",
        ]

        if self._paper_executor is not None:
            lines.append(f"Cash: ${self._paper_executor.balance:,.2f}")
            positions = self._paper_executor.positions
            lines.append(f"Positions: {len(positions)}")
            for coin, pos in positions.items():
                spot = pos.get("spot", 0)
                perp = pos.get("perp", 0)
                if abs(spot) > 1e-8 or abs(perp) > 1e-8:
                    lines.append(f"  {coin}: spot={spot:.4f}, perp={perp:.4f}")
        else:
            account = self._client.get_account_state()
            lines.append(f"Available: ${account.available_balance:,.2f}")
            lines.append(f"Margin: {account.margin_utilization:.1%}")
            lines.append(f"Positions: {len(account.positions)}")
            for p in account.positions:
                lines.append(f"  {p.coin}: size={p.size:.4f}, PnL=${p.unrealized_pnl:+,.2f}")

        lines.append(f"\n_Mode: {'PAPER' if self._paper_executor else 'LIVE'}_")

        # Append top funding rates scan
        try:
            rates = self._scan_funding_rates()
            if rates:
                lines.append("")
                lines.append("*Top Funding Rates:*")
                for r in rates[:8]:
                    sign = "+" if r["rate_annual"] > 0 else ""
                    lines.append(f"  {r['coin']}: {sign}{r['rate_annual']:.1f}%/yr ({r['rate_8h']*100:.4f}%/8h)")
        except Exception:
            pass

        return "\n".join(lines)

    def _scan_funding_rates(self) -> list[dict]:
        raw = self._client._info.meta_and_asset_ctxs()
        universe = raw[0]["universe"]
        ctxs = raw[1]
        rates = []
        for i, asset in enumerate(universe):
            if i >= len(ctxs):
                break
            ctx = ctxs[i]
            if ctx and ctx.get("funding"):
                rate_8h = float(ctx["funding"])
                rates.append({
                    "coin": asset["name"],
                    "rate_8h": rate_8h,
                    "rate_annual": rate_8h * 3 * 365 * 100,
                })
        rates.sort(key=lambda x: abs(x["rate_annual"]), reverse=True)
        return rates
