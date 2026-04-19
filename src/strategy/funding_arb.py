from __future__ import annotations

from dataclasses import dataclass

from src.data.store import DataStore
from src.hl_client.rest import HLRestClient
from src.hl_client.types import OrderRequest, OrderType, Side
from src.logger import get_logger
from src.strategy.base import Signal, Strategy

log = get_logger(__name__)


@dataclass
class ArbPosition:
    coin: str
    spot_size: float = 0.0
    perp_size: float = 0.0
    spot_entry: float = 0.0
    perp_entry: float = 0.0
    cumulative_funding: float = 0.0


class FundingArbStrategy(Strategy):
    def __init__(self, client: HLRestClient, store: DataStore, cfg: dict) -> None:
        self._client = client
        self._store = store

        arb_cfg = cfg.get("strategy", {}).get("funding_arb", {})
        self._entry_threshold = arb_cfg.get("entry_rate_threshold", 0.0001)
        self._exit_threshold = arb_cfg.get("exit_rate_threshold", 0.00003)
        self._max_pairs = arb_cfg.get("max_pairs", 5)
        self._per_pair_max_pct = arb_cfg.get("per_pair_max_pct", 0.20)
        self._margin_buffer_pct = arb_cfg.get("margin_buffer_pct", 0.20)
        self._basis_exit_threshold = arb_cfg.get("basis_exit_threshold", 0.03)
        self._min_liquidity = arb_cfg.get("min_liquidity_usd", 50000)

        self._positions: dict[str, ArbPosition] = {}
        self._candidate_coins: list[str] = []
        self._paper_equity: float | None = None

    def name(self) -> str:
        return "funding_arb"

    def set_paper_equity(self, equity: float) -> None:
        self._paper_equity = equity

    def set_candidate_coins(self, coins: list[str]) -> None:
        self._candidate_coins = coins

    def evaluate(self) -> list[Signal]:
        signals: list[Signal] = []
        signals.extend(self._check_exits())
        signals.extend(self._check_entries())
        return signals

    def _check_exits(self) -> list[Signal]:
        signals = []
        for coin, pos in list(self._positions.items()):
            rate = self._store.get_latest_funding_rate(coin)
            if rate is None:
                continue

            reason = ""
            if rate.rate < self._exit_threshold:
                reason = f"funding rate {rate.rate:.6f} below exit threshold {self._exit_threshold}"

            mids = self._client.get_all_mids()
            perp_mid = mids.get(coin)
            spot_mid = mids.get(f"@{coin}")
            if perp_mid and spot_mid and spot_mid > 0:
                basis = abs(perp_mid - spot_mid) / spot_mid
                if basis > self._basis_exit_threshold:
                    reason = f"basis {basis:.4f} exceeds threshold {self._basis_exit_threshold}"

            if reason:
                orders = self._build_close_orders(pos)
                signals.append(Signal(coin=coin, action="close", orders=orders, reason=reason))

        return signals

    def _check_entries(self) -> list[Signal]:
        if len(self._positions) >= self._max_pairs:
            return []

        signals = []
        if self._paper_equity is not None:
            equity = self._paper_equity
        else:
            equity = self._client.get_account_state().equity
        per_pair_capital = equity * self._per_pair_max_pct

        ranked = self._rank_candidates()
        slots = self._max_pairs - len(self._positions)

        for coin, rate_val in ranked[:slots]:
            if coin in self._positions:
                continue
            if per_pair_capital < 100:
                continue

            mids = self._client.get_all_mids()
            mid_price = mids.get(coin)
            if mid_price is None or mid_price == 0:
                continue

            if not self._check_liquidity(coin):
                continue

            size = (per_pair_capital / 2) / mid_price
            orders = self._build_open_orders(coin, size, mid_price)
            signals.append(
                Signal(
                    coin=coin,
                    action="open",
                    orders=orders,
                    reason=f"funding rate {rate_val:.6f} above entry threshold",
                    score=rate_val,
                )
            )

        return signals

    def _rank_candidates(self) -> list[tuple[str, float]]:
        rated = []
        for coin in self._candidate_coins:
            rate = self._store.get_latest_funding_rate(coin)
            if rate and rate.rate >= self._entry_threshold:
                rated.append((coin, rate.rate))
        rated.sort(key=lambda x: x[1], reverse=True)
        return rated

    def _check_liquidity(self, coin: str) -> bool:
        try:
            book = self._client.get_l2_snapshot(coin)
            levels = book.get("levels", [[], []])
            if len(levels) < 2:
                return False
            bid_depth = sum(float(lv["sz"]) * float(lv["px"]) for lv in levels[0][:5])
            ask_depth = sum(float(lv["sz"]) * float(lv["px"]) for lv in levels[1][:5])
            return min(bid_depth, ask_depth) >= self._min_liquidity
        except Exception:
            return False

    def _build_open_orders(self, coin: str, size: float, price: float) -> list[OrderRequest]:
        return [
            OrderRequest(coin=coin, side=Side.BUY, size=size, order_type=OrderType.MARKET, is_spot=True),
            OrderRequest(coin=coin, side=Side.SELL, size=size, order_type=OrderType.MARKET, is_spot=False),
        ]

    def _build_close_orders(self, pos: ArbPosition) -> list[OrderRequest]:
        orders = []
        if pos.spot_size > 0:
            orders.append(
                OrderRequest(
                    coin=pos.coin,
                    side=Side.SELL,
                    size=abs(pos.spot_size),
                    order_type=OrderType.MARKET,
                    is_spot=True,
                )
            )
        if pos.perp_size != 0:
            side = Side.BUY if pos.perp_size < 0 else Side.SELL
            orders.append(
                OrderRequest(
                    coin=pos.coin,
                    side=side,
                    size=abs(pos.perp_size),
                    order_type=OrderType.MARKET,
                    is_spot=False,
                    reduce_only=True,
                )
            )
        return orders

    def on_fill(self, coin: str, side: str, size: float, price: float, is_spot: bool = True) -> None:
        if coin not in self._positions:
            self._positions[coin] = ArbPosition(coin=coin)

        pos = self._positions[coin]
        if is_spot:
            if side == "buy":
                if pos.spot_size == 0:
                    pos.spot_entry = price
                else:
                    pos.spot_entry = (pos.spot_entry * pos.spot_size + price * size) / (pos.spot_size + size)
                pos.spot_size += size
            else:
                pos.spot_size -= size
        else:
            if side == "sell":
                if pos.perp_size == 0:
                    pos.perp_entry = price
                else:
                    pos.perp_entry = (pos.perp_entry * abs(pos.perp_size) + price * size) / (abs(pos.perp_size) + size)
                pos.perp_size -= size
            else:
                pos.perp_size += size

        if abs(pos.spot_size) < 1e-8 and abs(pos.perp_size) < 1e-8:
            del self._positions[coin]

        log.info("Fill recorded", extra={"coin": coin, "side": side, "size": size, "price": price, "is_spot": is_spot})

    def get_positions(self) -> dict[str, ArbPosition]:
        return self._positions.copy()
