"""Trading agent: receives a TradeSignal and executes it via Webull."""
from __future__ import annotations

from core.config import settings
from core.models import Direction, Order, OrderResult, OrderSide, OrderType, TradeSignal
from brokers.webull import WebullBroker
from agents.base_agent import BaseAgent


SYSTEM_PROMPT = """You are a disciplined trading execution agent. You receive a trade signal
and must decide whether to execute it, sizing the position within the configured risk limits.

Use calculate_position_size first, then place_order only if the signal passes your review.
Reject signals that violate risk controls."""

_TOOLS = [
    {
        "name": "calculate_position_size",
        "description": "Compute share quantity given a dollar risk budget and stop distance.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entry_price": {"type": "number"},
                "stop_loss": {"type": "number"},
                "risk_usd": {"type": "number", "description": "Max dollars to risk on the trade."},
            },
            "required": ["entry_price", "stop_loss", "risk_usd"],
        },
    },
    {
        "name": "place_order",
        "description": "Place a market or limit order via Webull.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "side": {"type": "string", "enum": ["buy", "sell"]},
                "order_type": {"type": "string", "enum": ["market", "limit"]},
                "quantity": {"type": "number"},
                "limit_price": {"type": "number"},
            },
            "required": ["ticker", "side", "order_type", "quantity"],
        },
    },
]


class TradingAgent(BaseAgent):
    """Executes trades on Webull based on incoming TradeSignals."""

    def __init__(self, broker: WebullBroker):
        super().__init__()
        self._broker = broker
        self._last_result: OrderResult | None = None

    def execute(self, signal: TradeSignal) -> OrderResult | None:
        messages = [
            {
                "role": "user",
                "content": (
                    f"Trade signal received:\n{signal.model_dump_json(indent=2)}\n\n"
                    f"Risk budget: ${settings.max_position_size_usd} max position, "
                    f"${settings.max_daily_loss_usd} max daily loss.\n"
                    "Review and execute if appropriate."
                ),
            }
        ]

        self._last_result = None
        self._run_loop(SYSTEM_PROMPT, messages, _TOOLS)
        return self._last_result

    def _dispatch_tools(self, content_blocks, tools) -> list[dict]:
        results = []
        for block in content_blocks:
            if block.type != "tool_use":
                continue

            if block.name == "calculate_position_size":
                qty = self._calc_position_size(**block.input)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": f"Recommended quantity: {qty} shares",
                })

            elif block.name == "place_order":
                inp = block.input
                order = Order(
                    ticker=inp["ticker"],
                    side=OrderSide(inp["side"]),
                    order_type=OrderType(inp["order_type"]),
                    quantity=inp["quantity"],
                    limit_price=inp.get("limit_price"),
                )
                self._last_result = self._broker.place_order(order)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": self._last_result.model_dump_json(),
                })

        return results

    @staticmethod
    def _calc_position_size(entry_price: float, stop_loss: float, risk_usd: float) -> float:
        stop_distance = abs(entry_price - stop_loss)
        if stop_distance == 0:
            return 0
        return round(risk_usd / stop_distance, 2)
