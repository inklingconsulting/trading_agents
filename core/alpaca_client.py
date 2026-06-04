"""Alpaca Markets data client for real-time pre-market gapper scanning.

Uses Alpaca's free paper-trading data API — no paid subscription needed.

Setup:
  1. Create a free account at alpaca.markets
  2. Go to paper.alpaca.markets -> API Keys -> Generate
  3. Add ALPACA_API_KEY and ALPACA_SECRET_KEY to .env

Endpoints used:
  /v1beta1/screener/stocks/movers  -- top gainers/losers in real-time
  /v2/assets/{symbol}              -- exchange + tradability info
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

import httpx

_EST         = ZoneInfo("America/New_York")
_MARKET_OPEN = time(9, 30)
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


def _divider(width: int = 62) -> str:
    return "-" * width


def print_raw_table(gainers: list[dict]) -> None:
    """Print every gapper Alpaca returned before any filtering."""
    now_str = datetime.now(tz=_EST).strftime("%H:%M EST")
    is_pm   = datetime.now(tz=_EST).time() < _MARKET_OPEN
    session = "PRE-MARKET" if is_pm else "REGULAR SESSION"

    print(f"\n{_divider()}")
    print(f"  ALPACA RAW GAINERS -- {len(gainers)} results ({session} {now_str})")
    print(f"  {'#':<4} {'TICKER':<7} {'GAP %':>8}  {'PRICE':>8}  {'PREV CLOSE':>10}  {'VOLUME':>12}")
    print(_divider())
    for i, g in enumerate(gainers, 1):
        price   = float(g.get("price") or 0)
        gap_pct = float(g.get("percent_change") or 0)
        vol     = int(g.get("volume") or 0)
        prev    = round(price / (1 + gap_pct / 100), 2) if gap_pct != -100 else 0
        vol_str = f"{vol:,}" if vol else ("pre-mkt" if is_pm else "0")
        print(
            f"  {i:<4} {g.get('symbol',''):<7} {gap_pct:>+7.1f}%  "
            f"${price:>7.2f}  ${prev:>9.2f}  {vol_str:>12}"
        )
    print(_divider())


def print_filtered_table(gappers: list[Gapper], min_gap: float, max_price: float) -> None:
    """Print candidates that passed the price and gap % filter."""
    print(f"\n{_divider()}")
    print(
        f"  AFTER FILTER (gap >={min_gap:.0f}%, price $1-${max_price:.0f})"
        f" -- {len(gappers)} candidates"
    )
    if not gappers:
        print("  (none passed -- consider lowering DISCOVERY_MIN_GAP_PCT)")
        print(_divider())
        return
    print(f"  {'#':<4} {'TICKER':<7} {'GAP %':>8}  {'PRICE':>8}  {'PREV CLOSE':>10}")
    print(_divider())
    for i, g in enumerate(gappers, 1):
        print(
            f"  {i:<4} {g.ticker:<7} {g.gap_pct:>+7.1f}%  "
            f"${g.price:>7.2f}  ${g.prev_close:>9.2f}"
        )
    print(_divider())


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
        """Return top gainers matching day-trading universe criteria."""
        resp = self._http.get(
            f"{_DATA_BASE}/v1beta1/screener/stocks/movers",
            params={"top": 50},
        )
        if resp.status_code == 403:
            raise PermissionError(
                "Alpaca API key rejected. Use keys from paper.alpaca.markets."
            )
        if not resp.is_success:
            raise RuntimeError(
                f"Alpaca movers returned {resp.status_code}: {resp.text[:300]}"
            )

        data    = resp.json()
        gainers = data.get("gainers", [])

        # Always print the full raw table so you can see the landscape
        print_raw_table(gainers)

        is_premarket = datetime.now(tz=_EST).time() < _MARKET_OPEN
        results: list[Gapper] = []

        for item in gainers:
            try:
                ticker  = item.get("symbol", "")
                if not ticker or len(ticker) > 5:
                    continue

                price   = float(item.get("price") or 0)
                gap_pct = float(item.get("percent_change") or 0)
                volume  = int(item.get("volume") or 0)

                if price <= 0 or not (min_price <= price <= max_price):
                    continue
                if gap_pct < min_gap_pct:
                    continue
                # Volume is 0 pre-market — only apply this filter once open
                if not is_premarket and volume < min_volume:
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
        results = results[:limit]

        # Print filtered table
        print_filtered_table(results, min_gap_pct, max_price)

        return results

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
                "type":     data.get("class", ""),
            }
        except Exception:
            return {}

    def close(self) -> None:
        self._http.close()
