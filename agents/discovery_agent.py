"""Discovery agent — finds pre-market day-trading candidates.

Flow:
  1. Polygon.io snapshot → real-time list of stocks gapping 10%+ pre-market
     with price/volume filters applied (fast, no Claude tokens spent here)
  2. Polygon reference API → enrich each candidate with shares outstanding,
     market cap, and exchange (filters out OTC / preferred shares)
  3. Claude + web search → find the news catalyst for each confirmed gapper,
     discard those with no catalyst, rank the remaining by setup quality
  4. Write daily_watchlist.json and send desktop/phone notification

Falls back to pure Claude web search if POLYGON_API_KEY is not set.

Run once each morning before market open:
    python main.py discover
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from agents.base_agent import BaseAgent, DEFAULT_MODEL
from core.config import settings
from core.models import AlertPriority, DailyWatchlist, WatchlistCandidate
from core.notifications import send_watchlist_ready

EST = ZoneInfo("America/New_York")
WATCHLIST_PATH = Path(__file__).parent.parent / "daily_watchlist.json"

_WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search"}

# ── Prompt: Claude finds catalysts for Polygon-identified gappers ─────────────
_CATALYST_SYSTEM = """\
You are a pre-market catalyst analyst for a professional day trading system.

You will receive a list of stocks that Polygon.io has confirmed are gapping up
significantly in pre-market trading right now. The price, gap %, and volume data
are real — your job is NOT to find these stocks, they have already been found.

Your job:
1. Search for today's ({date}) specific news catalyst for each ticker
2. Discard any ticker where you cannot find a clear, credible catalyst
   (no catalyst = no trade — this is Ross Cameron's first rule)
3. Rank the remaining tickers by day-trading setup quality:
   - Catalyst strength: FDA approval > earnings beat > major contract/partnership
     > short squeeze setup > analyst upgrade > vague rumor (avoid)
   - Float preference: lower is better (under 10M shares = A+, under 20M = A)
   - Gap size combined with volume: a 50% gap on 500K shares beats a 15% gap on 50K
   - Exchange: NYSE/NASDAQ strongly preferred over OTC/pink sheets

Return ONLY a JSON array (no prose, no markdown fences):
[
  {{
    "ticker": "XYZ",
    "price": 5.20,
    "gap_pct": 45.2,
    "float_m": null,
    "market_cap_m": null,
    "relative_volume": 8.5,
    "catalyst": "One-sentence description of the specific news catalyst",
    "catalyst_source": "e.g. company press release, SEC filing, Benzinga",
    "priority": "high",
    "rank": 1,
    "rationale": "Why this is a top pick in 1-2 sentences"
  }}
]

Today's date: {date}
Current time: {time} EST
"""

# ── Fallback prompt: used when Polygon key is not configured ──────────────────
_FALLBACK_SYSTEM = """\
You are acting as Ross Cameron from Warrior Trading — a professional small-cap day trader.
Every morning you run a pre-market scan to find the best day-trading setups.

Your criteria (strict order of importance):
1. NEWS CATALYST — must have a specific reason for the move today. No catalyst = skip.
2. GAP — gapping up 10%+ from prior close pre-market.
3. FLOAT — under 20M shares (under 10M is ideal).
4. PRICE — $1 to $20 range.
5. RELATIVE VOLUME — at least 2x normal pre-market volume.
6. MARKET CAP — micro to small cap, under $500M.
7. EXCHANGE — NYSE or NASDAQ preferred.

Search for today's pre-market gappers and movers. Find catalysts. Filter and rank.

Return ONLY a JSON array (no prose, no markdown fences):
[
  {{
    "ticker": "XYZ",
    "price": 5.20,
    "gap_pct": 45.2,
    "float_m": null,
    "market_cap_m": null,
    "relative_volume": 8.5,
    "catalyst": "Specific catalyst description",
    "catalyst_source": "source name",
    "priority": "high",
    "rank": 1,
    "rationale": "Why this is a top pick in 1-2 sentences"
  }}
]

Today's date: {date}
Current time: {time} EST
"""


class DiscoveryAgent(BaseAgent):
    """Finds pre-market day-trading candidates using Polygon data + Claude ranking."""

    def __init__(self, model: str = DEFAULT_MODEL):
        super().__init__(model=model)

    async def discover_raw(self) -> None:
        """Dump raw Alpaca data with no filters — for validating the connection."""
        from core.alpaca_client import AlpacaScanner
        if not (settings.alpaca_api_key and settings.alpaca_secret_key):
            print("[DiscoveryAgent] No Alpaca keys configured.")
            return
        scanner = AlpacaScanner(settings.alpaca_api_key, settings.alpaca_secret_key)
        try:
            # scan with no filters at all
            gappers = scanner.scan_gappers(
                min_gap_pct=0.0,
                min_price=0.0,
                max_price=999_999.0,
                min_volume=0,
                limit=50,
            )
            print(f"\n{'='*60}")
            print(f"  RAW ALPACA GAINERS — {len(gappers)} results (no filters)")
            print(f"{'='*60}")
            for g in gappers:
                print(f"  {g.ticker:<6} +{g.gap_pct:>6.1f}%  ${g.price:<8.2f}  vol {g.volume:>12,}")
            print(f"{'='*60}\n")
        finally:
            scanner.close()

    async def discover(self) -> DailyWatchlist:
        now = datetime.now(tz=EST)
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M")

        print(f"[DiscoveryAgent] Scanning pre-market at {time_str} EST...")

        if not (settings.alpaca_api_key and settings.alpaca_secret_key):
            print("[DiscoveryAgent] No ALPACA_API_KEY / ALPACA_SECRET_KEY in .env — stopping.")
            return DailyWatchlist(date=date_str)

        raw_candidates = await self._alpaca_flow(date_str, time_str)

        if raw_candidates is None:
            print("[DiscoveryAgent] Alpaca scan failed — check error above.")
            return DailyWatchlist(date=date_str)

        if not raw_candidates:
            print("[DiscoveryAgent] No candidates found.")
            return DailyWatchlist(date=date_str)

        candidates = []
        for c in raw_candidates:
            try:
                candidates.append(WatchlistCandidate(**c))
            except Exception:
                pass
        candidates.sort(key=lambda c: c.rank)
        watchlist = [c.ticker for c in candidates]

        result = DailyWatchlist(date=date_str, candidates=candidates, watchlist=watchlist)
        self._write(result)
        self._print_summary(result)
        send_watchlist_ready(watchlist, date_str, len(candidates))
        return result

    # ── Alpaca flow ──────────────────────────────────────────────────────────

    async def _alpaca_flow(self, date_str: str, time_str: str) -> list[dict] | None:
        """Returns None if Alpaca is unavailable (triggers next fallback)."""
        loop = asyncio.get_event_loop()

        gappers = await loop.run_in_executor(None, self._alpaca_scan)
        if gappers is None:
            return None
        if not gappers:
            print("[DiscoveryAgent] Alpaca returned no gappers matching criteria.")
            return []

        print(f"[DiscoveryAgent] Alpaca found {len(gappers)} gappers — filtering by exchange...")
        gappers = await loop.run_in_executor(None, self._alpaca_enrich, gappers)

        print(f"[DiscoveryAgent] {len(gappers)} candidates after filter — finding catalysts...")
        return await loop.run_in_executor(None, self._find_catalysts, gappers, date_str, time_str)

    def _alpaca_scan(self) -> list[dict] | None:
        from core.alpaca_client import AlpacaScanner
        scanner = AlpacaScanner(settings.alpaca_api_key, settings.alpaca_secret_key)
        try:
            gappers = scanner.scan_gappers(
                min_gap_pct=settings.discovery_min_gap_pct,
                min_price=1.0,
                max_price=settings.news_max_price,
                min_volume=settings.discovery_min_volume,
                limit=settings.discovery_max_candidates,
            )
            if gappers:
                print(
                    f"[DiscoveryAgent] Alpaca top gapper: {gappers[0].ticker} "
                    f"+{gappers[0].gap_pct}% @ ${gappers[0].price} | vol {gappers[0].volume:,}"
                )
            return [g.__dict__ for g in gappers]
        except PermissionError as exc:
            print(f"[DiscoveryAgent] Alpaca auth error: {exc}")
            return None
        except Exception as exc:
            print(f"[DiscoveryAgent] Alpaca scan error: {exc}")
            return None
        finally:
            scanner.close()

    def _alpaca_enrich(self, gappers: list[dict]) -> list[dict]:
        """Filter out non-US-exchange and untradable assets via Alpaca asset details."""
        from core.alpaca_client import AlpacaScanner
        scanner = AlpacaScanner(settings.alpaca_api_key, settings.alpaca_secret_key)
        kept = []
        try:
            for g in gappers:
                details = scanner.get_asset_details(g["ticker"])
                # Skip non-equity asset classes (crypto, ETF, etc.)
                if details.get("type") not in ("us_equity", ""):
                    continue
                # Skip if not tradable (halted, delisted, etc.)
                if details and not details.get("tradable", True):
                    continue
                g["exchange"] = details.get("exchange", "")
                kept.append(g)
        finally:
            scanner.close()

        dropped = len(gappers) - len(kept)
        if dropped:
            print(f"[DiscoveryAgent] Exchange filter: removed {dropped} non-equity/OTC tickers, {len(kept)} remain")
        return kept

    # ── Polygon flow ─────────────────────────────────────────────────────────

    async def _polygon_flow(self, date_str: str, time_str: str) -> list[dict] | None:
        """Returns None if Polygon is unavailable (triggers fallback)."""
        loop = asyncio.get_event_loop()

        # Step 1: scan for gappers
        gappers = await loop.run_in_executor(None, self._polygon_scan)
        if gappers is None:
            return None   # permission error — caller will fall back
        if not gappers:
            print("[DiscoveryAgent] Polygon returned no gappers matching criteria.")
            return []

        print(f"[DiscoveryAgent] Polygon found {len(gappers)} gappers — enriching details...")

        # Step 2: enrich with exchange / shares outstanding
        gappers = await loop.run_in_executor(None, self._polygon_enrich, gappers)

        # Step 3: Claude finds catalysts and ranks
        print(f"[DiscoveryAgent] Sending {len(gappers)} candidates to Claude for catalyst search...")
        return await loop.run_in_executor(None, self._find_catalysts, gappers, date_str, time_str)

    def _polygon_scan(self) -> list[dict]:
        from core.polygon_client import PolygonClient
        client = PolygonClient(settings.polygon_api_key)
        try:
            gappers = client.scan_gappers(
                min_gap_pct=settings.discovery_min_gap_pct,
                min_price=1.0,
                max_price=settings.news_max_price,
                min_volume=settings.discovery_min_volume,
                limit=settings.discovery_max_candidates,
            )
            if gappers:
                print(f"[DiscoveryAgent] Top gapper: {gappers[0].ticker} +{gappers[0].gap_pct}%"
                      f" @ ${gappers[0].price} | vol {gappers[0].volume:,}")
            return [g.__dict__ for g in gappers]
        except PermissionError as exc:
            print(f"[DiscoveryAgent] Polygon plan too low: {exc}")
            return None   # signal to caller to fall back
        except Exception as exc:
            print(f"[DiscoveryAgent] Polygon scan error: {exc}")
            return []
        finally:
            client.close()

    def _polygon_enrich(self, gappers: list[dict]) -> list[dict]:
        """Add shares outstanding, market cap, exchange via Polygon reference API."""
        from core.polygon_client import PolygonClient
        client = PolygonClient(settings.polygon_api_key)
        enriched = []
        try:
            for g in gappers:
                details = client.get_ticker_details(g["ticker"])
                # Filter out preferred shares, warrants, ETFs
                if details.get("type") not in ("", "CS", None):
                    continue
                # Filter out non-US exchanges (OTC pink sheets have long MIC codes)
                exchange = details.get("exchange", "")
                if exchange and exchange not in ("XNYS", "XNAS", "XASE", ""):
                    continue
                g.update({
                    "shares_m": details.get("shares_m"),
                    "market_cap_m": details.get("market_cap_m"),
                    "exchange": exchange,
                })
                # Apply market cap filter
                mcap = g.get("market_cap_m")
                if mcap and mcap > settings.news_max_market_cap_m:
                    continue
                enriched.append(g)
        finally:
            client.close()
        return enriched

    def _find_catalysts(self, gappers: list[dict], date_str: str, time_str: str) -> list[dict]:
        """Claude web-searches for catalysts on the Polygon-identified gappers."""
        ticker_lines = "\n".join(
            f"  {g['ticker']}: +{g['gap_pct']}% gap | ${g['price']} | "
            f"vol {g['volume']:,} | RV {g['rel_vol']}x"
            + (f" | shares {g['shares_m']}M" if g.get("shares_m") else "")
            + (f" | mcap ${g['market_cap_m']}M" if g.get("market_cap_m") else "")
            for g in gappers
        )

        system = _CATALYST_SYSTEM.format(date=date_str, time=time_str)
        messages = [{
            "role": "user",
            "content": (
                f"Find catalysts and rank these pre-market gappers ({date_str} {time_str} EST).\n"
                f"These are confirmed by Polygon.io real-time data:\n\n{ticker_lines}\n\n"
                f"Search for today's specific news catalyst for each. "
                f"Return only those with a real catalyst, ranked as JSON."
            ),
        }]

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
            print(f"[DiscoveryAgent] Catalyst search failed: {exc}")
            return []

        raw = self._extract_text(response)
        try:
            start, end = raw.find("["), raw.rfind("]") + 1
            candidates = json.loads(raw[start:end]) if start >= 0 else []
        except (json.JSONDecodeError, ValueError):
            print("[DiscoveryAgent] Could not parse Claude response")
            return []

        # Merge Polygon volume data back in (Claude may not preserve it)
        polygon_by_ticker = {g["ticker"]: g for g in gappers}
        for c in candidates:
            ticker = c.get("ticker", "")
            pg = polygon_by_ticker.get(ticker, {})
            c.setdefault("price", pg.get("price"))
            c.setdefault("gap_pct", pg.get("gap_pct"))
            c.setdefault("relative_volume", pg.get("rel_vol"))
            c.setdefault("float_m", pg.get("shares_m"))
            c.setdefault("market_cap_m", pg.get("market_cap_m"))
            c.setdefault("priority", "medium")
            c.setdefault("rank", 99)
            c.setdefault("rationale", "")
            c.setdefault("catalyst", "")
            c.setdefault("catalyst_source", "")

        return candidates

    # ── Fallback: pure Claude web search (no Polygon) ────────────────────────

    def _fallback_web_search(self, date_str: str, time_str: str) -> list[dict]:
        system = _FALLBACK_SYSTEM.format(date=date_str, time=time_str)
        messages = [{
            "role": "user",
            "content": (
                f"Find today's best pre-market day-trading setups ({date_str}). "
                "Search for pre-market gappers, check their catalysts, and return your ranked picks as JSON."
            ),
        }]
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
            print(f"[DiscoveryAgent] Web search failed: {exc}")
            return []

        raw = self._extract_text(response)
        try:
            start, end = raw.find("["), raw.rfind("]") + 1
            candidates = json.loads(raw[start:end]) if start >= 0 else []
        except (json.JSONDecodeError, ValueError):
            return []

        for i, c in enumerate(candidates):
            c.setdefault("priority", "medium")
            c.setdefault("rank", i + 1)
            c.setdefault("rationale", "")
            c.setdefault("catalyst", "")
            c.setdefault("catalyst_source", "")
        return candidates

    # ── Output helpers ───────────────────────────────────────────────────────

    def _write(self, watchlist: DailyWatchlist) -> None:
        WATCHLIST_PATH.write_text(watchlist.model_dump_json(indent=2), encoding="utf-8")
        print(f"[DiscoveryAgent] Watchlist saved -> {WATCHLIST_PATH}")

    def _print_summary(self, watchlist: DailyWatchlist) -> None:
        W   = 72
        bar = "=" * W
        div = "-" * W
        print(f"\n{bar}")
        print(f"  TODAY'S WATCHLIST  {watchlist.date}  ({len(watchlist.candidates)} candidates)")
        print(bar)

        priority_tag = {"high": "[!!!]", "medium": "[!] ", "low": "[ ] "}.get

        for c in watchlist.candidates:
            tag   = priority_tag(c.priority.value if c.priority else "medium", "[!] ")
            gap   = f"+{c.gap_pct:.1f}%" if c.gap_pct else ""
            price = f"${c.price:.2f}"    if c.price   else ""
            fl    = f"float {c.float_m:.1f}M" if c.float_m else ""
            mcap  = f"mcap ${c.market_cap_m:.0f}M" if c.market_cap_m else ""
            meta  = "  ".join(x for x in [gap, price, fl, mcap] if x)

            print(div)
            print(f"  {tag} #{c.rank}  {c.ticker}  |  {meta}")
            if c.catalyst:
                src = f" ({c.catalyst_source})" if c.catalyst_source else ""
                print(f"       Catalyst: {c.catalyst[:65]}{src}")
            if c.rationale:
                print(f"       Why:      {c.rationale[:65]}")

        print(bar)
        print(f"  Pull up in TradingView: {', '.join(watchlist.watchlist)}")
        print(f"{bar}\n")
