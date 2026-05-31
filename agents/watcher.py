"""Standalone chart watcher — monitors whatever stock is open in TradingView.

Usage:
    python main.py watch                        # poll every 30s, notify on buy/sell
    python main.py watch --poll 60              # slower polling
    python main.py watch --actions buy,sell,watch
    python main.py watch --strength strong      # only strong signals

Turn it on when you want analysis. Press Ctrl+C to stop.
Notifications fire for any signal matching your --actions and --strength filters.
"""
from __future__ import annotations

import asyncio
import json

import anthropic

from core.config import settings
from core.models import AlertPriority, ChartAction, ChartAlert, SignalStrength
from core.notifications import notify, send_chart_alert
from platforms.tradingview_mcp import check_mcp_server_available, run_with_tv_tools

_STRENGTH_RANK = {"weak": 0, "moderate": 1, "strong": 2}

_SYSTEM = """\
You are a technical analysis agent monitoring a live TradingView chart for Ross Cameron-style day trading setups.

STEP 1 — Check for the RC Setup Scanner indicator (most efficient path):
  Call data_get_pine_tables with study_filter="RC Setup Scanner"
  If the table is present, it contains pre-computed levels (PDH, PDL, PMH, PML, ORH, ORL, VWAP),
  today's fired signals, and a SETUP recommendation. Use this as your primary data source.

STEP 2 — Get current price:
  Call quote_get — always do this for the latest price.

STEP 3 — If RC Setup Scanner table is NOT on the chart, fall back to manual TA:
  Call chart_get_state → data_get_study_values → data_get_pine_lines → data_get_pine_labels

STEP 4 — Apply trading rules and make a decision:
{rules}

Return ONLY a JSON object — no prose, no markdown:
  ticker, action (buy/sell/watch/hold), strength (weak/moderate/strong),
  entry_price, stop_loss, take_profit, rationale, rules_triggered (list of rule names), priority (low/medium/high)

For strength:
  strong  = multiple breakout signals fired + high relative volume + price above 3+ key levels
  moderate = single clean breakout + elevated volume
  weak    = marginal setup, low volume, or extended / overextended price
"""


class ChartWatcher:
    """Polls the currently open TradingView chart and notifies on matching signals."""

    def __init__(
        self,
        poll_interval: int = 30,
        min_strength: str = "moderate",
        notify_actions: set[str] | None = None,
    ):
        self.poll_interval = poll_interval
        self.min_strength = min_strength
        self.notify_actions = notify_actions or {"buy", "sell"}
        self._running = False
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._model = settings.chart_model
        self._rules = self._load_rules()

    def _load_rules(self) -> str:
        rules_path = settings.mcp_server_path() / "rules.json"
        if rules_path.exists():
            return rules_path.read_text(encoding="utf-8")
        return (
            "No rules.json found. Use general technical analysis:\n"
            "- RSI < 30 oversold (potential buy), RSI > 70 overbought (potential sell)\n"
            "- MACD bullish crossover = buy signal, bearish crossover = sell signal\n"
            "- Price at key support with volume = buy, at resistance = sell/watch\n"
            "- Require at least 2 confluent signals before calling buy or sell"
        )

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        if not check_mcp_server_available():
            print("[Watcher] Cannot reach TradingView.")
            print("[Watcher] TradingView must be running with CDP enabled on port 9222.")
            print("[Watcher] Close TradingView, then relaunch it from a terminal with:")
            print('[Watcher]   & "$env:LOCALAPPDATA\\Programs\\TradingView\\TradingView.exe" --remote-debugging-port=9222')
            return

        self._running = True
        actions_str = "/".join(sorted(self.notify_actions)).upper()
        print(f"[Watcher] Monitoring TradingView — poll every {self.poll_interval}s")
        print(f"[Watcher] Will notify on: {actions_str} | min strength: {self.min_strength}")
        print("[Watcher] Ctrl+C to stop\n")
        notify(
            title="Chart Watcher Active",
            message=f"Monitoring TradingView | alerts on {actions_str}",
            priority="low",
            tags=["eyes"],
        )

        while self._running:
            try:
                alert = await self._analyze()
                if alert:
                    self._print_and_notify(alert)
                else:
                    print("[Watcher] No signal parsed this cycle")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                print(f"[Watcher] Error: {exc}")

            try:
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break

        print("\n[Watcher] Stopped.")
        notify("Chart Watcher Stopped", "No longer monitoring TradingView", priority="low", tags=["stop_sign"])

    def _print_and_notify(self, alert: ChartAlert) -> None:
        action = alert.action.value
        strength = alert.strength.value
        tag = {"buy": "[BUY]", "sell": "[SELL]", "watch": "[WATCH]", "hold": "[hold]"}.get(action, "")

        print(f"{tag} {alert.ticker} | {strength} | {alert.rationale[:90]}")
        if alert.entry_price:
            print(f"       Entry ${alert.entry_price}  Stop ${alert.stop_loss}  Target ${alert.take_profit}")
        if alert.rules_triggered:
            print(f"       Rules: {', '.join(alert.rules_triggered)}")

        strength_ok = _STRENGTH_RANK.get(strength, 0) >= _STRENGTH_RANK.get(self.min_strength, 0)
        if action in self.notify_actions and strength_ok:
            send_chart_alert(alert.ticker, action, alert.rationale)

    async def _analyze(self) -> ChartAlert | None:
        system = _SYSTEM.format(rules=self._rules)
        messages = [{"role": "user", "content": "Analyze the current TradingView chart and return a trading signal."}]
        raw = await run_with_tv_tools(self._client, self._model, system, messages)

        try:
            start, end = raw.find("{"), raw.rfind("}") + 1
            data = json.loads(raw[start:end]) if start >= 0 else {}
        except (json.JSONDecodeError, ValueError):
            return None

        if not data:
            return None

        try:
            return ChartAlert(
                ticker=data.get("ticker", "UNKNOWN"),
                action=ChartAction(data.get("action", "hold")),
                strength=SignalStrength(data.get("strength", "weak")),
                entry_price=data.get("entry_price"),
                stop_loss=data.get("stop_loss"),
                take_profit=data.get("take_profit"),
                rationale=data.get("rationale", ""),
                rules_triggered=data.get("rules_triggered", []),
                priority=AlertPriority(data.get("priority", "low")),
            )
        except Exception:
            return None
