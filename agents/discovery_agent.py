"""Discovery agent — finds pre-market day-trading candidates from scratch.

No watchlist required. Mimics Ross Cameron's morning scan:
  - Pre-market gappers 10%+ with a news catalyst
  - Low float (under 20M shares, ideally under 10M)
  - Price $1–$20
  - High relative volume vs average
  - NYSE/NASDAQ listed (not OTC)

Writes daily_watchlist.json and fires a Windows toast notification when done.
Run once each morning before starting the main orchestrator:
    python main.py discover
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yfinance as yf

from agents.base_agent import BaseAgent, DEFAULT_MODEL
from core.config import settings
from core.models import AlertPriority, DailyWatchlist, WatchlistCandidate
from core.notifications import send_watchlist_ready

EST = ZoneInfo("America/New_York")

WATCHLIST_PATH = Path(__file__).parent.parent / "daily_watchlist.json"

_WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
}

_SYSTEM = """\
You are acting as Ross Cameron from Warrior Trading — a professional small-cap day trader.
Every morning you run a pre-market scan to find the best day-trading setups.

Your criteria (strict order of importance):
1. NEWS CATALYST — must have a specific reason for the move today (earnings beat, FDA approval,
   contract win, short squeeze, press release, etc.). No catalyst = skip it.
2. GAP — gapping up 10%+ from prior close pre-market. Higher gap = more interesting.
3. FLOAT — under 20M shares outstanding (under 10M is ideal). Low float = big moves.
4. PRICE — $1 to $20 range. Sweet spot is $2–$10.
5. RELATIVE VOLUME — at least 2× normal pre-market volume.
6. MARKET CAP — micro to small cap, under $500M.
7. EXCHANGE — NYSE or NASDAQ preferred. Avoid OTC / pink sheets.

Your task:
  Search for today's pre-market gappers and movers.
  Find the news catalysts for each candidate.
  Filter to only stocks meeting the criteria above.
  Rank the top 5–10 picks for today's session.

Return ONLY a JSON array with this exact shape (no prose, no markdown fences):
[
  {{
    "ticker": "XYZ",
    "price": 5.20,
    "gap_pct": 45.2,
    "float_m": 3.5,
    "market_cap_m": 85,
    "relative_volume": 8.5,
    "catalyst": "One-sentence description of the specific news catalyst",
    "catalyst_source": "e.g. company press release, SEC filing, Benzinga",
    "priority": "high",
    "rank": 1,
    "rationale": "Why this is your top pick in 1-2 sentences."
  }}
]

Today's date: {date}
Current time: {time} EST
"""


class DiscoveryAgent(BaseAgent):
    """Proactively finds pre-market day-trading candidates each morning."""

    def __init__(self, model: str = DEFAULT_MODEL):
        super().__init__(model=model)

    async def discover(self) -> DailyWatchlist:
        now = datetime.now(tz=EST)
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M")

        print(f"[DiscoveryAgent] Scanning pre-market at {time_str} EST...")

        raw_candidates = await asyncio.get_event_loop().run_in_executor(
            None, self._run_discovery, date_str, time_str
        )

        if not raw_candidates:
            print("[DiscoveryAgent] No candidates found.")
            return DailyWatchlist(date=date_str)

        print(f"[DiscoveryAgent] Claude returned {len(raw_candidates)} candidates — enriching with fundamentals...")
        enriched = await asyncio.get_event_loop().run_in_executor(
            None, self._enrich, raw_candidates
        )

        candidates = [WatchlistCandidate(**c) for c in enriched]
        candidates.sort(key=lambda c: c.rank)
        watchlist = [c.ticker for c in candidates]

        result = DailyWatchlist(
            date=date_str,
            candidates=candidates,
            watchlist=watchlist,
        )

        self._write(result)
        self._print_summary(result)
        send_watchlist_ready(watchlist, date_str, len(candidates))

        return result

    def _run_discovery(self, date_str: str, time_str: str) -> list[dict]:
        """Ask Claude (with web search) to find and rank today's pre-market gappers."""
        system = _SYSTEM.format(date=date_str, time=time_str)
        messages = [
            {
                "role": "user",
                "content": (
                    f"Find today's best pre-market day-trading setups ({date_str}). "
                    "Search for pre-market gappers, check their catalysts, and return your ranked picks as JSON."
                ),
            }
        ]

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system,
                tools=[_WEB_SEARCH_TOOL],
                messages=messages,
                extra_headers={"anthropic-beta": "web-search-2025-03-05"},
            )
        except Exception as exc:
            print(f"[DiscoveryAgent] Claude search failed: {exc}")
            return []

        raw = self._extract_text(response)
        try:
            start = raw.find("[")
            end = raw.rfind("]") + 1
            return json.loads(raw[start:end]) if start >= 0 else []
        except (json.JSONDecodeError, ValueError):
            print(f"[DiscoveryAgent] Could not parse JSON response:\n{raw[:300]}")
            return []

    def _enrich(self, candidates: list[dict]) -> list[dict]:
        """Validate tickers and backfill float/market cap from yfinance where missing."""
        enriched = []
        for item in candidates:
            ticker = item.get("ticker", "").upper().strip()
            if not ticker:
                continue
            try:
                t = yf.Ticker(ticker)
                info = t.info  # slower but has float data
                fast = t.fast_info

                # Validate it's a real ticker
                if not info.get("symbol") and not info.get("shortName"):
                    print(f"[DiscoveryAgent] Skipping unrecognised ticker: {ticker}")
                    continue

                # Backfill missing data from yfinance
                if not item.get("price"):
                    item["price"] = round(float(fast.last_price or 0), 2) or None
                if not item.get("market_cap_m") and info.get("marketCap"):
                    item["market_cap_m"] = round(info["marketCap"] / 1_000_000, 1)
                if not item.get("float_m") and info.get("floatShares"):
                    item["float_m"] = round(info["floatShares"] / 1_000_000, 2)

                # Apply price filter (agent sometimes drifts)
                price = item.get("price") or 0
                if price > settings.news_max_price:
                    print(f"[DiscoveryAgent] {ticker} price ${price} > ${settings.news_max_price} limit — skipping")
                    continue

                item["ticker"] = ticker
                item.setdefault("priority", "medium")
                item.setdefault("rank", len(enriched) + 1)
                item.setdefault("rationale", "")
                item.setdefault("catalyst", "")
                item.setdefault("catalyst_source", "")

                enriched.append(item)
            except Exception as exc:
                print(f"[DiscoveryAgent] yfinance error for {ticker}: {exc}")
                item["ticker"] = ticker
                item.setdefault("priority", "medium")
                item.setdefault("rank", len(enriched) + 1)
                enriched.append(item)

        return enriched

    def _write(self, watchlist: DailyWatchlist) -> None:
        WATCHLIST_PATH.write_text(
            watchlist.model_dump_json(indent=2),
            encoding="utf-8",
        )
        print(f"[DiscoveryAgent] Watchlist saved to {WATCHLIST_PATH}")

    def _print_summary(self, watchlist: DailyWatchlist) -> None:
        print(f"\n{'='*60}")
        print(f"  TODAY'S WATCHLIST — {watchlist.date}")
        print(f"{'='*60}")
        for c in watchlist.candidates:
            print(f"  {c.summary_line()}")
        print(f"{'='*60}\n")
