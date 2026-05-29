"""Webull broker integration wrapping the unofficial webull Python SDK."""
from __future__ import annotations

from typing import Optional

from webull import webull as WebullAPI

from core.config import settings
from core.models import Order, OrderResult, OrderType


class WebullBroker:
    """Thin wrapper around the webull SDK with login and order management."""

    def __init__(self):
        self._api = WebullAPI()
        self._logged_in = False

    def login(self) -> None:
        self._api.login(
            username=settings.webull_username,
            password=settings.webull_password,
            device_id=settings.webull_device_id or None,
        )
        self._api.get_trade_token(settings.webull_trading_pin)
        self._logged_in = True

    def logout(self) -> None:
        self._api.logout()
        self._logged_in = False

    def __enter__(self) -> "WebullBroker":
        self.login()
        return self

    def __exit__(self, *_) -> None:
        self.logout()

    def get_account(self) -> dict:
        self._require_login()
        return self._api.get_account()

    def get_positions(self) -> list[dict]:
        self._require_login()
        return self._api.get_positions()

    def place_order(self, order: Order) -> OrderResult:
        self._require_login()

        kwargs = dict(
            stock=order.ticker,
            action=order.side.value.upper(),
            orderType=order.order_type.value.upper(),
            enforce="GTC",
            quant=order.quantity,
        )
        if order.order_type == OrderType.LIMIT:
            kwargs["price"] = order.limit_price
        if order.order_type == OrderType.STOP:
            kwargs["price"] = order.stop_price

        result = self._api.place_order(**kwargs)
        return OrderResult(
            order_id=str(result.get("orderId", "")),
            status=result.get("status", "unknown"),
            message=str(result),
        )

    def cancel_order(self, order_id: str) -> dict:
        self._require_login()
        return self._api.cancel_order(order_id)

    def _require_login(self) -> None:
        if not self._logged_in:
            raise RuntimeError("Not logged in to Webull. Call login() first.")
