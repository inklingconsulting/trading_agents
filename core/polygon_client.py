"""Polygon.io market data client for real-time pre-market scanning.

Used by DiscoveryAgent to find gappers directly from exchange feed data
instead of relying on Claude web search to discover stocks.

Requires POLYGON_API_KEY in .env — https://polygon.io (Stocks Starter ~$29/mo)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx

_BASE = "https://api.polygon.io"


@dataclass
class Gapper:
    ticker: str
    price: float
    prev_close: float
    gap_pct: float
    volume: int
    rel_vol: float
    shares_m: Optional[float] = None
    market_cap_m: Optional[float] = None
    exchange: str = ""


class PolygonClient:
    def __init__(self, api_key: str):
        self._key = api_key
        self._http = httpx.Client(timeout=60)

    def _get(self, path: str, **params) -> dict:
        resp = self._http.get(
            f"{_BASE}{path}",
            params={"apiKey": self._key, **params},
        )
        if resp.status_code == 403:
            raise PermissionError(
                "Polygon API key is valid but this endpoint requires a paid plan. "
                "Upgrade to Stocks Starter ($29/mo) at polygon.io/dashboard."
            )
        resp.raise_for_status()
        return resp.json()

    def scan_gappers(
        self,
        min_gap_pct: float = 10.0,
        min_price: float = 1.0,
        max_price: float = 20.0,
        min_volume: int = 10_000,
        limit: int = 40,
    ) -> list[Gapper]:
        """Scan all US stocks for pre-market gappers matching the given criteria.

        Returns up to `limit` results sorted by gap % descending.
        """
        data = self._get(
            "/v2/snapshot/locale/us/markets/stocks/tickers",
            include_otc="false",
        )

        results: list[Gapper] = []
        for item in data.get("tickers", []):
            try:
                ticker = item.get("ticker", "")
                if not ticker or len(ticker) > 5:
                    continue

                prev_close = item.get("prevDay", {}).get("c") or 0
                if prev_close <= 0:
                    continue

                # lastTrade is the most current price including extended hours
                price = (
                    item.get("lastTrade", {}).get("p")
                    or item.get("min", {}).get("c")
                    or item.get("day", {}).get("c")
                    or 0
                )
                if price <= 0:
                    continue

                gap_pct = (price - prev_close) / prev_close * 100
                if gap_pct < min_gap_pct:
                    continue
                if not (min_price <= price <= max_price):
                    continue

                volume = int(item.get("day", {}).get("v") or 0)
                if volume < min_volume:
                    continue

                prev_vol = item.get("prevDay", {}).get("v") or 0
                rel_vol = round(volume / prev_vol, 1) if prev_vol > 0 else 0.0

                results.append(Gapper(
                    ticker=ticker,
                    price=round(price, 2),
                    prev_close=round(prev_close, 2),
                    gap_pct=round(gap_pct, 1),
                    volume=volume,
                    rel_vol=rel_vol,
                ))
            except Exception:
                continue

        results.sort(key=lambda g: g.gap_pct, reverse=True)
        return results[:limit]

    def get_ticker_details(self, ticker: str) -> dict:
        """Fetch shares outstanding, market cap, and exchange for one ticker."""
        try:
            data = self._get(f"/v3/reference/tickers/{ticker}")
            r = data.get("results", {})
            shares = r.get("weighted_shares_outstanding") or r.get("share_class_shares_outstanding")
            mcap = r.get("market_cap")
            return {
                "shares_m": round(shares / 1_000_000, 2) if shares else None,
                "market_cap_m": round(mcap / 1_000_000, 1) if mcap else None,
                "exchange": r.get("primary_exchange", ""),
                "type": r.get("type", ""),
            }
        except Exception:
            return {}

    def close(self) -> None:
        self._http.close()
