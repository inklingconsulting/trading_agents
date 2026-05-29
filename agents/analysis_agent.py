"""Analysis agent: reads TradingView charts and produces trade signals."""
from __future__ import annotations

import base64
import json

from core.models import TradeSignal
from platforms.tradingview import TradingViewClient
from agents.base_agent import BaseAgent


SYSTEM_PROMPT = """You are a technical analysis expert. You are given a screenshot of a
TradingView chart and must produce a structured trade signal.

When calling produce_signal, populate every field you can determine from the chart.
If you cannot determine a value, omit it. Rationale should be concise (1-3 sentences)."""

_TOOLS = [
    {
        "name": "produce_signal",
        "description": "Emit a structured trade signal based on chart analysis.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "direction": {"type": "string", "enum": ["long", "short"]},
                "strength": {"type": "string", "enum": ["weak", "moderate", "strong"]},
                "entry_price": {"type": "number"},
                "stop_loss": {"type": "number"},
                "take_profit": {"type": "number"},
                "rationale": {"type": "string"},
            },
            "required": ["ticker", "direction", "strength", "rationale"],
        },
    }
]


class AnalysisAgent(BaseAgent):
    """Analyzes a TradingView chart screenshot and returns a TradeSignal."""

    def __init__(self):
        super().__init__()
        self._pending_signal: dict | None = None

    async def analyze(self, ticker: str, client: TradingViewClient) -> TradeSignal:
        screenshot = await client.get_chart_screenshot(ticker)
        image_b64 = base64.standard_b64encode(screenshot).decode()

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": f"Analyze this {ticker} chart and produce a trade signal."},
                ],
            }
        ]

        self._pending_signal = None
        self._run_loop(SYSTEM_PROMPT, messages, _TOOLS)

        if self._pending_signal is None:
            raise RuntimeError("Analysis agent did not produce a signal.")

        return TradeSignal(**self._pending_signal)

    def _dispatch_tools(self, content_blocks, tools) -> list[dict]:
        results = []
        for block in content_blocks:
            if block.type == "tool_use" and block.name == "produce_signal":
                self._pending_signal = block.input
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": "Signal recorded.",
                })
        return results
