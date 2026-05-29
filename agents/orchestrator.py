"""Orchestrator: boots all agents and routes inter-agent messages.

Message flow:
  NewsAgent  --"news"--> Orchestrator --> prints alert
                                      --> publishes "chart_trigger" if priority >= medium
                                          (with per-ticker cooldown)
  ChartAgent --"chart"--> Orchestrator --> prints alert
                                       --> execution agent (not yet wired)

Execution is disabled by default (settings.execution_enabled = False).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from agents.chart_agent import ChartAgent
from agents.news_agent import NewsAgent
from core.config import settings
from core.message_bus import MessageBus
from core.models import AgentMessage, AlertPriority, ChartAction, ChartAlert, NewsAlert


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

    async def _on_news_alert(self, msg: AgentMessage) -> None:
        alert = NewsAlert(**msg.payload)
        priority_tag = {"high": "[!!!]", "medium": "[!]", "low": "[ ]"}.get(alert.priority.value, "")
        print(
            f"\n{priority_tag} [NEWS] {alert.ticker} ${alert.price or '?'} "
            f"| {alert.headline[:80]}"
        )
        if alert.premarket_change_pct is not None:
            print(f"   Pre-market: {alert.premarket_change_pct:+.1f}%")

        if alert.priority in (AlertPriority.HIGH, AlertPriority.MEDIUM):
            await self._maybe_trigger_chart(alert.ticker, alert.headline)

    async def _maybe_trigger_chart(self, ticker: str, headline: str) -> None:
        now = datetime.utcnow()
        last = self._last_chart_trigger.get(ticker)
        cooldown = timedelta(seconds=settings.chart_trigger_cooldown_sec)
        if last is not None and (now - last) < cooldown:
            remaining = int((cooldown - (now - last)).total_seconds())
            print(f"   [Orchestrator] Chart trigger skipped for {ticker} (cooldown: {remaining}s remaining)")
            return

        self._last_chart_trigger[ticker] = now
        trigger = AgentMessage(
            topic="chart_trigger",
            from_agent="orchestrator",
            payload={"ticker": ticker, "headline": headline},
        )
        await self._bus.publish(trigger)
        print(f"   [Orchestrator] Chart analysis triggered for {ticker}")

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

        if settings.execution_enabled and alert.action in (ChartAction.BUY, ChartAction.SELL):
            print("   [Orchestrator] Execution enabled — forwarding to trading agent (not yet wired)")

    async def run(self) -> None:
        print("[Orchestrator] Starting all agents...")
        print(f"[Orchestrator] Execution mode: {'ENABLED' if settings.execution_enabled else 'DISABLED (safe mode)'}")

        await asyncio.gather(
            self._bus.run(),
            self._news_agent.run(),
            self._chart_agent.run(),
        )

    def stop(self) -> None:
        self._bus.stop()
        self._news_agent.stop()
        self._chart_agent.stop()
