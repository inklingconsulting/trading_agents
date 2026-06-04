"""Alpaca Markets data client for real-time pre-market gapper scanning.

Uses Alpaca's free paper-trading data API — no paid subscription needed.

Setup:
  1. Create a free account at alpaca.markets
  2. Go to paper.alpaca.markets → API Keys → Generate
  3. Add ALPACA_API_KEY and ALPACA_SECRET_KEY to .env

Endpoints used:
  /v1beta1/screener/stocks/movers  — top gainers/losers in real-time
  /v2/assets/{symbol}              — exchange + tradability info
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx

_DATA_BASE   = "https://data.alpaca.markets"
_BROKER_BASE = "https://paper-api.alpaca.markets"


@dataclass
class Gapper:
    ticker: str
    price: float
    prev_close: float
    gap_pct: float
    volume: int
    rel_vol: float = 0.0
    shares_m: Optional[float] = None
    market_cap_m: Optional[float] = None
    exchange: str = ""


class AlpacaScanner:
    def __init__(self, api_key: str, secret_key: str):
        self._headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
            "Accept": "application/json",
        }
        self._http = httpx.Client(timeout=30, headers=self._headers)

    def scan_gappers(
        self,
        min_gap_pct: float = 10.0,
        min_price: float = 1.0,
        max_price: float = 20.0,
        min_volume: int = 10_000,
        limit: int = 40,
    ) -> list[Gapper]:
        """Return top gainers matching day-trading universe criteria.

        Uses Alpaca's screener/movers endpoint which reflects real-time
        price changes from previous close, including pre-market moves.
        """
        resp = self._http.get(
            f"{_DATA_BASE}/v1beta1/screener/stocks/movers",
            params={"top": 50},
        )
        if resp.status_code == 403:
            raise PermissionError(
                "Alpaca API key rejected. Make sure you're using keys from "
                "paper.alpaca.markets (not a deleted/invalid key)."
            )
        if not resp.is_success:
            raise RuntimeError(
                f"Alpaca movers endpoint returned {resp.status_code}: {resp.text[:300]}"
            )
        resp.raise_for_status()

        data     = resp.json()
        gainers  = data.get("gainers", [])
        results: list[Gapper] = []

        print(
            f"[AlpacaScanner] Raw response: {len(gainers)} gainers, "
            f"{len(data.get('losers', []))} losers"
        )
        if gainers:
            top = gainers[0]
            print(
                f"[AlpacaScanner] Top raw gainer: {top.get('symbol')} "
                f"+{top.get('percent_change')}% @ ${top.get('price')} "
                f"vol {top.get('volume'):,}"
            )

        for item in gainers:
            try:
                ticker  = item.get("symbol", "")
                if not ticker or len(ticker) > 5:
                    continue

                price   = float(item.get("price", 0))
                gap_pct = float(item.get("percent_change", 0))
                volume  = int(item.get("volume", 0))

                if price <= 0 or not (min_price <= price <= max_price):
                    continue
                if gap_pct < min_gap_pct:
                    continue
                if volume < min_volume:
                    continue

                prev_close = round(price / (1 + gap_pct / 100), 2)
                results.append(Gapper(
                    ticker=ticker,
                    price=round(price, 2),
                    prev_close=prev_close,
                    gap_pct=round(gap_pct, 1),
                    volume=volume,
                ))
            except Exception:
                continue

        results.sort(key=lambda g: g.gap_pct, reverse=True)
        return results[:limit]

    def get_asset_details(self, ticker: str) -> dict:
        """Return exchange and tradability info for a ticker."""
        try:
            resp = self._http.get(f"{_BROKER_BASE}/v2/assets/{ticker}")
            resp.raise_for_status()
            data = resp.json()
            return {
                "exchange": data.get("exchange", ""),
                "tradable": data.get("tradable", True),
                "shortable": data.get("shortable", False),
                "type": data.get("class", ""),
            }
        except Exception:
            return {}

    def close(self) -> None:
        self._http.close()
