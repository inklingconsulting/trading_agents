from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    anthropic_api_key: str

    # Webull
    webull_username: str = ""
    webull_password: str = ""
    webull_trading_pin: str = ""
    webull_device_id: str = ""

    # TradingView (legacy Playwright login)
    tradingview_username: str = ""
    tradingview_password: str = ""

    # Risk controls
    max_position_size_usd: float = 1000.0
    max_daily_loss_usd: float = 500.0

    # TradingView MCP server (Node.js)
    tradingview_mcp_path: str = r"C:\_repo\tradingview_mcp_jackson"

    # Notifications
    ntfy_topic: str = ""   # set to enable phone push via ntfy.sh (e.g. "my-trading-alerts")

    # News agent watchlist criteria
    news_max_price: float = 20.0          # max stock price to consider
    news_max_float_m: float = 20.0        # max float in millions (0 = no limit)
    news_max_market_cap_m: float = 500.0  # max market cap in millions (0 = no limit)
    news_scan_start_hour: int = 7         # 24h EST (7 = 7:00 AM)
    news_scan_end_hour: int = 9           # 9 = up to 9:30 AM (30 min buffer in agent)
    news_scan_end_minute: int = 30
    news_poll_interval_sec: int = 300     # how often to scan (5 min default)

    # Watchlist: comma-separated tickers for the chart agent to monitor
    watchlist: str = ""

    # Chart agent
    chart_model: str = "claude-sonnet-4-6"
    chart_fallback_poll_sec: int = 300      # poll interval when no news trigger fires
    chart_trigger_cooldown_sec: int = 300   # min seconds between triggers for the same ticker

    # Agent execution guard (False = no real orders placed)
    execution_enabled: bool = False

    def watchlist_tickers(self) -> list[str]:
        return [t.strip().upper() for t in self.watchlist.split(",") if t.strip()]

    def mcp_server_path(self) -> Path:
        return Path(self.tradingview_mcp_path)


settings = Settings()
