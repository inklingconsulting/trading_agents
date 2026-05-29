"""News agent: scans pre-market for small-cap, low-float movers under $20.

Runs on a loop between configured hours (default 7:00 AM – 9:30 AM EST).
Uses yfinance for price data and web search (Anthropic beta) for news headlines.
Publishes NewsAlert messages to the bus on topic "news".
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, time
from zoneinfo import ZoneInfo

from pathlib import Path

import httpx
import yfinance as yf

import anthropic

from agents.base_agent import BaseAgent, DEFAULT_MODEL
from agents.discovery_agent import WATCHLIST_PATH
from core.config import settings
from core.message_bus import MessageBus
from core.models import AgentMessage, AlertPriority, DailyWatchlist, NewsAlert

EST = ZoneInfo("America/New_York")

_WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
}

_SYSTEM = """\
You are a pre-market stock news scout. Your job is to find stocks that fit these criteria:
- Stock price under ${max_price}
- Small-cap or micro-cap (market cap under ${max_mcap}M)
- Low float (under {max_float}M shares) when data is available
- Significant pre-market news or catalyst (earnings, FDA, partnership, short squeeze, etc.)

When given candidate tickers or search results, return a JSON array of objects with:
  ticker, price, float_shares (or null), market_cap_usd (or null),
  premarket_change_pct (or null), headline, source, priority (low/medium/high)

Return ONLY the JSON array, no prose.
"""


class NewsAgent(BaseAgent):
    """Pre-market scanner that finds low-float small-cap movers with catalysts."""

    def __init__(self, bus: MessageBus, model: str = DEFAULT_MODEL):
        super().__init__(model=model)
        self._bus = bus
        self._running = False

    async def run(self) -> None:
        """Loop until stop() is called, scanning during configured market hours."""
        self._running = True
        print("[NewsAgent] Started — scanning pre-market movers")

        while self._running:
            now = datetime.now(tz=EST)
            scan_start = time(settings.news_scan_start_hour, 0)
            scan_end = time(settings.news_scan_end_hour, settings.news_scan_end_minute)

            if scan_start <= now.time() <= scan_end:
                try:
                    alerts = await self._scan()
                    for alert in alerts:
                        msg = AgentMessage(
                            topic="news",
                            from_agent="news_agent",
                            payload=alert.model_dump(),
                        )
                        await self._bus.publish(msg)
                        print(f"[NewsAgent] Alert: {alert.ticker} — {alert.headline[:60]}")
                except Exception as exc:
                    print(f"[NewsAgent] Scan error: {exc}")
            else:
                print(f"[NewsAgent] Outside scan window ({now.strftime('%H:%M')} EST), sleeping...")

            await asyncio.sleep(settings.news_poll_interval_sec)

    def stop(self) -> None:
        self._running = False

    async def _scan(self) -> list[NewsAlert]:
        candidates = await asyncio.get_event_loop().run_in_executor(None, self._fetch_premarket_candidates)
        if not candidates:
            return []
        return await asyncio.get_event_loop().run_in_executor(None, self._analyze_candidates, candidates)

    def _get_tickers(self) -> list[str]:
        """Prefer today's discovery watchlist; fall back to WATCHLIST env var."""
        today = datetime.now(tz=EST).strftime("%Y-%m-%d")
        if WATCHLIST_PATH.exists():
            try:
                data = DailyWatchlist.model_validate_json(WATCHLIST_PATH.read_text())
                if data.date == today and data.watchlist:
                    print(f"[NewsAgent] Using discovery watchlist ({len(data.watchlist)} tickers)")
                    return data.watchlist
            except Exception:
                pass
        return settings.watchlist_tickers()

    def _fetch_premarket_candidates(self) -> list[dict]:
        """Pull pre-market data for watchlist tickers."""
        tickers = self._get_tickers()
        if not tickers:
            return []

        results = []
        for sym in tickers:
            try:
                t = yf.Ticker(sym)
                info = t.fast_info
                price = getattr(info, "last_price", None) or getattr(info, "previous_close", None)
                if price is None or price > settings.news_max_price:
                    continue
                results.append({
                    "ticker": sym,
                    "price": round(float(price), 2),
                    "market_cap": getattr(info, "market_cap", None),
                    "shares_outstanding": getattr(info, "shares", None),
                })
            except Exception:
                pass
        return results

    def _analyze_candidates(self, candidates: list[dict]) -> list[NewsAlert]:
        """Ask Claude (with web search) to find news catalysts for each candidate."""
        if not candidates:
            return []

        system = _SYSTEM.format(
            max_price=settings.news_max_price,
            max_mcap=int(settings.news_max_market_cap_m),
            max_float=int(settings.news_max_float_m),
        )

        ticker_list = ", ".join(c["ticker"] for c in candidates)
        user_msg = (
            f"Current time: {datetime.now(tz=EST).strftime('%Y-%m-%d %H:%M EST')}\n"
            f"Candidate tickers from watchlist: {ticker_list}\n\n"
            f"Search for pre-market news and catalysts for each ticker. "
            f"Filter to only those with meaningful news today. Return JSON only."
        )

        # Use Anthropic's web search beta
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                betas=["web-search-2025-03-05"],
                system=system,
                tools=[_WEB_SEARCH_TOOL],
                messages=[{"role": "user", "content": user_msg}],
            )
        except anthropic.BadRequestError:
            # Fallback: no web search, just return raw candidates as low-priority alerts
            return [
                NewsAlert(
                    ticker=c["ticker"],
                    price=c.get("price"),
                    headline="Pre-market candidate (no news found)",
                    priority=AlertPriority.LOW,
                )
                for c in candidates
            ]

        raw = self._extract_text(response)
        try:
            start = raw.find("[")
            end = raw.rfind("]") + 1
            data = json.loads(raw[start:end]) if start >= 0 else []
        except (json.JSONDecodeError, ValueError):
            return []

        alerts = []
        for item in data:
            try:
                alerts.append(NewsAlert(
                    ticker=item.get("ticker", ""),
                    price=item.get("price"),
                    float_shares=item.get("float_shares"),
                    market_cap_usd=item.get("market_cap_usd"),
                    premarket_change_pct=item.get("premarket_change_pct"),
                    headline=item.get("headline", ""),
                    source=item.get("source", ""),
                    priority=AlertPriority(item.get("priority", "medium")),
                ))
            except Exception:
                pass
        return alerts
