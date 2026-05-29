"""Browser automation layer for TradingView using Playwright."""
from __future__ import annotations

import asyncio
from typing import Optional

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from core.config import settings


TRADINGVIEW_URL = "https://www.tradingview.com"
CHART_URL = "https://www.tradingview.com/chart/"


class TradingViewClient:
    """Controls the TradingView Chrome session via Playwright."""

    def __init__(self, headless: bool = False):
        self._headless = headless
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    async def __aenter__(self) -> "TradingViewClient":
        await self.start()
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self._headless)
        self._context = await self._browser.new_context()
        self._page = await self._context.new_page()

        if settings.tradingview_username:
            await self._login()

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def _login(self) -> None:
        await self._page.goto(f"{TRADINGVIEW_URL}/accounts/signin/")
        await self._page.fill('[name="username"]', settings.tradingview_username)
        await self._page.fill('[name="password"]', settings.tradingview_password)
        await self._page.click('[type="submit"]')
        await self._page.wait_for_load_state("networkidle")

    async def navigate_to_chart(self, ticker: str) -> None:
        await self._page.goto(f"{CHART_URL}?symbol={ticker}")
        await self._page.wait_for_load_state("networkidle")

    async def get_chart_screenshot(self, ticker: str) -> bytes:
        """Return a PNG screenshot of the current chart for vision-based analysis."""
        await self.navigate_to_chart(ticker)
        await asyncio.sleep(2)  # let chart render fully
        return await self._page.screenshot(full_page=False)

    async def get_indicator_values(self) -> dict:
        """
        Extract indicator values from the chart data layer.
        Override or extend this for specific indicators (RSI, MACD, etc.).
        """
        raise NotImplementedError
