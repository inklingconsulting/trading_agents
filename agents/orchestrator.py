"""Orchestrator: boots all agents and routes inter-agent messages.

Startup sequence:
  1. Auto-discover today's watchlist (DiscoveryAgent) if none exists yet today
  2. Display the watchlist so you can pull tickers up in TradingView
  3. Start NewsAgent + ChartAgent

Message flow:
  NewsAgent  --"news"--> Orchestrator --> notify + trigger chart if priority >= medium
  ChartAgent --"chart"--> Orchestrator --> notify + print alert

Execution is disabled by default (settings.execution_enabled = False).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from agents.chart_agent import ChartAgent
from agents.news_agent import NewsAgent
from core.config import settings
from core.message_bus import MessageBus
from core.models import AgentMessage, AlertPriority, ChartAction, ChartAlert, DailyWatchlist, NewsAlert
from core.notifications import notify, send_chart_alert, send_news_alert

EST = ZoneInfo("America/New_York")


class Orchestrator:
    def __init__(self, chart_fallback_poll: int | None = None):
        self._bus = MessageBus()
        self._news_agent = NewsAgent(bus=self._bus)
        self._chart_agent = ChartAgent(
            bus=self._bus,
            fallback_poll_sec=chart_fallback_poll,
        )
        self._last_chart_trigger: dict[str, datetime] = {}
        self._setup_subscriptions()

    def _setup_subscriptions(self) -> None:
        self._bus.subscribe("news", self._on_news_alert)
        self._bus.subscribe("chart", self._on_chart_alert)

    # --- auto-discovery ---

    async def _auto_discover(self) -> None:
        """Run DiscoveryAgent if no fresh watchlist exists for today."""
        from agents.discovery_agent import DiscoveryAgent, WATCHLIST_PATH

        now = datetime.now(tz=EST)
        today = now.strftime("%Y-%m-%d")
        market_open = dtime(9, 30)

        # Use existing watchlist if it's fresh
        if WATCHLIST_PATH.exists():
            try:
                data = DailyWatchlist.model_validate_json(WATCHLIST_PATH.read_text(encoding="utf-8"))
                if data.date == today and data.watchlist:
                    self._print_watchlist(data)
                    return
            except Exception:
                pass

        # No fresh watchlist
        if now.time() >= market_open:
            print(
                "[Orchestrator] No watchlist for today. Market is already open — "
                "run 'python main.py discover' before 9:30 AM EST next time."
            )
            notify(
                title="No Watchlist Today",
                message="Run 'python main.py discover' before 9:30 AM tomorrow",
                priority="low",
            )
            return

        # Pre-market: run discovery automatically
        print("[Orchestrator] No watchlist for today — running pre-market discovery...")
        agent = DiscoveryAgent()
        watchlist = await agent.discover()
        if watchlist.watchlist:
            self._print_watchlist(watchlist)

    def _print_watchlist(self, watchlist: DailyWatchlist) -> None:
        tickers = watchlist.watchlist
        bar = "=" * 60
        print(f"\n{bar}")
        print(f"  TODAY'S WATCHLIST  {watchlist.date}")
        for c in watchlist.candidates:
            print(f"  {c.summary_line()}")
        print(f"\n  Pull these up in TradingView: {', '.join(tickers)}")
        print(f"{bar}\n")
        notify(
            title=f"Watchlist: {', '.join(tickers[:4])}{'...' if len(tickers) > 4 else ''}",
            message=f"Pull up in TradingView ({len(tickers)} tickers for {watchlist.date})",
            priority="default",
            tags=["clipboard"],
        )

    # --- news handler ---

    async def _on_news_alert(self, msg: AgentMessage) -> None:
        alert = NewsAlert(**msg.payload)
        priority_tag = {"high": "[!!!]", "medium": "[!]", "low": "[ ]"}.get(alert.priority.value, "")
        pct_str = f" {alert.premarket_change_pct:+.1f}%" if alert.premarket_change_pct is not None else ""
        print(
            f"\n{priority_tag} [NEWS] {alert.ticker} ${alert.price or '?'}{pct_str} "
            f"| {alert.headline[:80]}"
        )

        send_news_alert(alert.ticker, f"${alert.price or '?'}{pct_str} | {alert.headline}")

        if alert.priority in (AlertPriority.HIGH, AlertPriority.MEDIUM):
            await self._maybe_trigger_chart(alert.ticker, alert.headline)

    async def _maybe_trigger_chart(self, ticker: str, headline: str) -> None:
        now = datetime.utcnow()
        last = self._last_chart_trigger.get(ticker)
        cooldown = timedelta(seconds=settings.chart_trigger_cooldown_sec)
        if last is not None and (now - last) < cooldown:
            remaining = int((cooldown - (now - last)).total_seconds())
            print(f"   [Orchestrator] Chart trigger skipped for {ticker} (cooldown: {remaining}s)")
            return

        self._last_chart_trigger[ticker] = now
        trigger = AgentMessage(
            topic="chart_trigger",
            from_agent="orchestrator",
            payload={"ticker": ticker, "headline": headline},
        )
        await self._bus.publish(trigger)
        print(f"   [Orchestrator] Chart analysis triggered for {ticker}")

    # --- chart handler ---

    async def _on_chart_alert(self, msg: AgentMessage) -> None:
        alert = ChartAlert(**msg.payload)
        action_tag = {"buy": "[BUY]", "sell": "[SELL]", "watch": "[WATCH]", "hold": "[HOLD]"}.get(alert.action.value, "")
        print(
            f"\n{action_tag} [CHART] {alert.ticker} -> {alert.action.value.upper()} "
            f"({alert.strength.value}) | {alert.rationale[:80]}"
        )
        if alert.entry_price:
            print(f"   Entry: ${alert.entry_price}  Stop: ${alert.stop_loss}  Target: ${alert.take_profit}")
        if alert.rules_triggered:
            print(f"   Rules: {', '.join(alert.rules_triggered)}")

        send_chart_alert(alert.ticker, alert.action.value, alert.rationale)

        if settings.execution_enabled and alert.action in (ChartAction.BUY, ChartAction.SELL):
            print("   [Orchestrator] Execution enabled — forwarding to trading agent (not yet wired)")

    # --- lifecycle ---

    async def run(self) -> None:
        print("[Orchestrator] Starting all agents...")
        print(f"[Orchestrator] Execution mode: {'ENABLED' if settings.execution_enabled else 'DISABLED (safe mode)'}")
        notify(
            title="Trading Agents Started",
            message=f"Execution: {'ENABLED' if settings.execution_enabled else 'disabled (safe mode)'}",
            priority="low",
            tags=["robot"],
        )

        await self._auto_discover()

        await asyncio.gather(
            self._bus.run(),
            self._news_agent.run(),
            self._chart_agent.run(),
        )

    def stop(self) -> None:
        self._bus.stop()
        self._news_agent.stop()
        self._chart_agent.stop()
