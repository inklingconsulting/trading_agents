from core.models import Direction, Order, OrderSide, OrderType, SignalStrength, TradeSignal


def test_trade_signal_defaults():
    signal = TradeSignal(
        ticker="AAPL",
        direction=Direction.LONG,
        strength=SignalStrength.STRONG,
        rationale="Breakout above resistance",
    )
    assert signal.ticker == "AAPL"
    assert signal.entry_price is None
    assert signal.timestamp is not None


def test_order_round_trip():
    order = Order(
        ticker="TSLA",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=10,
        limit_price=250.00,
    )
    dumped = order.model_dump()
    assert dumped["limit_price"] == 250.00
