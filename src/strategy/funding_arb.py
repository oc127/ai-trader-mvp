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

    def name(self) -> str:
        return "funding_arb"

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
            if coin in mids and pos.spot_entry > 0 and pos.perp_entry > 0:
                mid = mids[coin]
                basis = abs(mid - pos.perp_entry) / pos.perp_entry
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
        account = self._client.get_account_state()
        per_pair_capital = account.equity * self._per_pair_max_pct

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
            OrderRequest(
                coin=coin, side=Side.BUY, size=size, order_type=OrderType.MARKET, is_spot=True
            ),
            OrderRequest(
                coin=coin, side=Side.SELL, size=size, order_type=OrderType.MARKET, is_spot=False
            ),
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

    def on_fill(self, coin: str, side: str, size: float, price: float) -> None:
        if coin not in self._positions:
            self._positions[coin] = ArbPosition(coin=coin)
        log.info("Fill recorded", extra={"coin": coin, "side": side, "size": size, "price": price})

    def get_positions(self) -> dict[str, ArbPosition]:
        return self._positions.copy()
