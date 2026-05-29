"""Chart agent: reads TradingView data via MCP and generates buy/sell alerts.

Trigger-driven: waits for "chart_trigger" messages published by the Orchestrator
when the NewsAgent finds a medium/high-priority catalyst. Falls back to a long
periodic poll (chart_fallback_poll_sec, default 5 min) as a heartbeat.
"""
from __future__ import annotations

import asyncio
import json

from agents.base_agent import BaseAgent
from core.config import settings
from core.message_bus import MessageBus
from core.models import AgentMessage, AlertPriority, ChartAction, ChartAlert, SignalStrength
from platforms.tradingview_mcp import check_mcp_server_available, run_with_tv_tools

_SYSTEM = """\
You are a technical analysis agent connected to a live TradingView chart via tools.

Your job:
1. If a specific ticker is provided, call chart_set_symbol to switch to it first
2. Call chart_get_state to identify the current symbol and indicators
3. Call data_get_study_values for indicator readings (RSI, MACD, EMA, etc.)
4. Call data_get_pine_lines and data_get_pine_labels for key price levels
5. Call quote_get for the current price
6. Evaluate the rules provided and decide: buy / sell / watch / hold

Rules to apply:
{rules}

After gathering data, produce a structured JSON response with these exact fields:
  ticker, action (buy/sell/watch/hold), strength (weak/moderate/strong),
  entry_price, stop_loss, take_profit, rationale, rules_triggered (list of rule names), priority (low/medium/high)

Return ONLY the JSON object, no prose.
"""


class ChartAgent(BaseAgent):
    """Analyzes TradingView charts via MCP tools and emits ChartAlerts.

    Waits for chart_trigger messages from the bus. Falls back to a periodic
    poll (fallback_poll_sec) as a heartbeat when no trigger arrives.
    """

    def __init__(self, bus: MessageBus, fallback_poll_sec: int | None = None, model: str | None = None):
        super().__init__(model=model or settings.chart_model)
        self._bus = bus
        self._fallback_poll_sec = fallback_poll_sec or settings.chart_fallback_poll_sec
        self._running = False
        self._rules = self._load_rules()
        self._trigger_queue: asyncio.Queue = asyncio.Queue()
        self._bus.subscribe("chart_trigger", self._on_trigger)

    def _load_rules(self) -> str:
        rules_path = settings.mcp_server_path() / "rules.json"
        if rules_path.exists():
            return rules_path.read_text(encoding="utf-8")
        return "No rules configured — use general technical analysis best practices."

    async def _on_trigger(self, msg: AgentMessage) -> None:
        await self._trigger_queue.put(msg.payload)

    async def run(self) -> None:
        self._running = True
        if not check_mcp_server_available():
            print("[ChartAgent] TradingView MCP server not available — stopping.")
            return

        print(
            f"[ChartAgent] Started - trigger-driven "
            f"(fallback poll every {self._fallback_poll_sec}s, model: {self.model})"
        )
        while self._running:
            ticker: str | None = None
            headline: str = ""
            try:
                payload = await asyncio.wait_for(
                    self._trigger_queue.get(),
                    timeout=self._fallback_poll_sec,
                )
                ticker = payload.get("ticker")
                headline = payload.get("headline", "")
                print(f"[ChartAgent] Triggered: {ticker} | {headline[:60]}")
            except asyncio.TimeoutError:
                print("[ChartAgent] Fallback poll - analyzing current chart")

            try:
                alert = await self._analyze_chart(ticker=ticker, headline=headline)
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

    def stop(self) -> None:
        self._running = False

    async def _analyze_chart(self, ticker: str | None = None, headline: str = "") -> ChartAlert | None:
        system = _SYSTEM.format(rules=self._rules)
        if ticker:
            user_content = (
                f"News catalyst detected for {ticker}. "
                + (f'Headline: "{headline}" ' if headline else "")
                + f"Switch the TradingView chart to {ticker} and produce a trading alert."
            )
        else:
            user_content = "Analyze the current TradingView chart and produce a trading alert."

        messages = [{"role": "user", "content": user_content}]
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
                ticker=data.get("ticker", ticker or "UNKNOWN"),
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
