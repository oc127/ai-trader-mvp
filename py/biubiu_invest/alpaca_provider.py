"""Alpaca Markets data provider for US stocks."""
from __future__ import annotations

import os
from typing import Optional

from .data_providers import FetchRequest, Provider
from .storage import DailyBar


class AlpacaProvider(Provider):
    """
    US stock daily bars via Alpaca Markets API.

    Requires: pip install alpaca-py
    Environment variables: ALPACA_API_KEY, ALPACA_SECRET_KEY
    """

    name = "alpaca"

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
    ):
        self.api_key = api_key or os.environ.get("ALPACA_API_KEY", "")
        self.secret_key = secret_key or os.environ.get("ALPACA_SECRET_KEY", "")
        if not self.api_key or not self.secret_key:
            raise RuntimeError(
                "Alpaca credentials required. Set ALPACA_API_KEY and ALPACA_SECRET_KEY "
                "environment variables, or pass api_key/secret_key to AlpacaProvider."
            )
        try:
            from alpaca.data.historical import StockHistoricalDataClient  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "alpaca-py is not installed. Install it first: pip install alpaca-py"
            ) from e
        self._client = StockHistoricalDataClient(self.api_key, self.secret_key)

    def fetch_daily_bars(self, req: FetchRequest) -> list[DailyBar]:
        from datetime import datetime

        from alpaca.data.requests import StockBarsRequest  # type: ignore
        from alpaca.data.timeframe import TimeFrame  # type: ignore

        symbols = req.ts_codes
        if not symbols:
            return []

        kwargs: dict = {
            "symbol_or_symbols": symbols,
            "timeframe": TimeFrame.Day,
        }
        if req.start_date:
            kwargs["start"] = datetime.strptime(req.start_date, "%Y-%m-%d")
        if req.end_date:
            kwargs["end"] = datetime.strptime(req.end_date, "%Y-%m-%d")

        request = StockBarsRequest(**kwargs)
        bars_set = self._client.get_stock_bars(request)

        out: list[DailyBar] = []
        for symbol in symbols:
            try:
                symbol_bars = bars_set[symbol]
            except (KeyError, TypeError):
                continue
            if not symbol_bars:
                continue
            for bar in symbol_bars:
                out.append(
                    DailyBar(
                        ts_code=symbol,
                        trade_date=bar.timestamp.strftime("%Y-%m-%d"),
                        open=float(bar.open),
                        high=float(bar.high),
                        low=float(bar.low),
                        close=float(bar.close),
                        volume=float(bar.volume),
                    )
                )
        return out
