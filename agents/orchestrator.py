"""Orchestrator: boots all agents and routes inter-agent messages.

Message flow:
  NewsAgent  ──"news"──► Orchestrator ──► prints alert (+ future: chart agent enrichment)
  ChartAgent ──"chart"─► Orchestrator ──► prints alert (+ future: execution agent gating)

Execution is disabled by default (settings.execution_enabled = False).
When enabled in the future, the orchestrator will forward buy/sell ChartAlerts
to TradingAgent → WebullBroker.
"""
from __future__ import annotations

import asyncio

from agents.chart_agent import ChartAgent
from agents.news_agent import NewsAgent
from core.config import settings
from core.message_bus import MessageBus
from core.models import AgentMessage, ChartAction, ChartAlert, NewsAlert


class Orchestrator:
    def __init__(self, chart_poll_interval: int = 30):
        self._bus = MessageBus()
        self._news_agent = NewsAgent(bus=self._bus)
        self._chart_agent = ChartAgent(bus=self._bus, poll_interval=chart_poll_interval)
        self._setup_subscriptions()

    def _setup_subscriptions(self) -> None:
        self._bus.subscribe("news", self._on_news_alert)
        self._bus.subscribe("chart", self._on_chart_alert)

    async def _on_news_alert(self, msg: AgentMessage) -> None:
        alert = NewsAlert(**msg.payload)
        priority_emoji = {"high": "🔴", "medium": "🟡", "low": "⚪"}.get(alert.priority.value, "")
        print(
            f"\n{priority_emoji} [NEWS] {alert.ticker} ${alert.price or '?'} "
            f"| {alert.headline[:80]}"
        )
        if alert.premarket_change_pct is not None:
            print(f"   Pre-market: {alert.premarket_change_pct:+.1f}%")

    async def _on_chart_alert(self, msg: AgentMessage) -> None:
        alert = ChartAlert(**msg.payload)
        action_emoji = {"buy": "📈", "sell": "📉", "watch": "👀", "hold": "⏸"}.get(alert.action.value, "")
        print(
            f"\n{action_emoji} [CHART] {alert.ticker} → {alert.action.value.upper()} "
            f"({alert.strength.value}) | {alert.rationale[:80]}"
        )
        if alert.entry_price:
            print(f"   Entry: ${alert.entry_price}  Stop: ${alert.stop_loss}  Target: ${alert.take_profit}")
        if alert.rules_triggered:
            print(f"   Rules: {', '.join(alert.rules_triggered)}")

        if settings.execution_enabled and alert.action in (ChartAction.BUY, ChartAction.SELL):
            print("   [Orchestrator] Execution enabled — forwarding to trading agent (not yet wired)")
            # TODO: forward to TradingAgent when ready

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
