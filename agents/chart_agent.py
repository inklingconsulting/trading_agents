"""Chart agent: reads TradingView data via MCP and generates buy/sell alerts.

Uses the mcp Python library to spawn the tradingview_mcp_jackson Node.js server
via stdio and run a Claude tool-use loop against its 68 TradingView tools.
Publishes ChartAlert messages to the bus on topic "chart".
"""
from __future__ import annotations

import asyncio
import json

from agents.base_agent import BaseAgent, DEFAULT_MODEL
from core.config import settings
from core.message_bus import MessageBus
from core.models import AgentMessage, AlertPriority, ChartAction, ChartAlert, SignalStrength
from platforms.tradingview_mcp import check_mcp_server_available, run_with_tv_tools

_SYSTEM = """\
You are a technical analysis agent connected to a live TradingView chart via tools.

Your job:
1. Call chart_get_state to identify the current symbol and indicators
2. Call data_get_study_values for indicator readings (RSI, MACD, EMA, etc.)
3. Call data_get_pine_lines and data_get_pine_labels for key price levels
4. Call quote_get for the current price
5. Evaluate the rules provided and decide: buy / sell / watch / hold

Rules to apply:
{rules}

After gathering data, produce a structured JSON response with these exact fields:
  ticker, action (buy/sell/watch/hold), strength (weak/moderate/strong),
  entry_price, stop_loss, take_profit, rationale, rules_triggered (list of rule names), priority (low/medium/high)

Return ONLY the JSON object, no prose.
"""


class ChartAgent(BaseAgent):
    """Analyzes TradingView charts via MCP tools and emits ChartAlerts."""

    def __init__(self, bus: MessageBus, poll_interval: int = 30, model: str = DEFAULT_MODEL):
        super().__init__(model=model)
        self._bus = bus
        self._poll_interval = poll_interval
        self._running = False
        self._rules = self._load_rules()

    def _load_rules(self) -> str:
        rules_path = settings.mcp_server_path() / "rules.json"
        if rules_path.exists():
            return rules_path.read_text(encoding="utf-8")
        return "No rules configured — use general technical analysis best practices."

    async def run(self) -> None:
        self._running = True
        if not check_mcp_server_available():
            print("[ChartAgent] TradingView MCP server not available — stopping.")
            return

        print(f"[ChartAgent] Started - polling chart every {self._poll_interval}s")
        while self._running:
            try:
                alert = await self._analyze_chart()
                if alert:
                    msg = AgentMessage(
                        topic="chart",
                        from_agent="chart_agent",
                        payload=alert.model_dump(),
                    )
                    await self._bus.publish(msg)
                    print(f"[ChartAgent] Alert: {alert.ticker} -> {alert.action.value} ({alert.strength.value})")
            except Exception as exc:
                print(f"[ChartAgent] Analysis error: {exc}")

            await asyncio.sleep(self._poll_interval)

    def stop(self) -> None:
        self._running = False

    async def _analyze_chart(self) -> ChartAlert | None:
        system = _SYSTEM.format(rules=self._rules)
        messages = [
            {"role": "user", "content": "Analyze the current TradingView chart and produce a trading alert."}
        ]

        raw = await run_with_tv_tools(self.client, self.model, system, messages)

        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            data = json.loads(raw[start:end]) if start >= 0 else {}
        except (json.JSONDecodeError, ValueError):
            return None

        if not data:
            return None

        try:
            return ChartAlert(
                ticker=data.get("ticker", "UNKNOWN"),
                action=ChartAction(data.get("action", "watch")),
                strength=SignalStrength(data.get("strength", "moderate")),
                entry_price=data.get("entry_price"),
                stop_loss=data.get("stop_loss"),
                take_profit=data.get("take_profit"),
                rationale=data.get("rationale", ""),
                rules_triggered=data.get("rules_triggered", []),
                priority=AlertPriority(data.get("priority", "medium")),
            )
        except Exception:
            return None
