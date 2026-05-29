"""Windows toast notifications for trading alerts.

Primary: winotify (Windows 10/11 native toasts — bottom-right corner pop-ups)
Fallback: winsound system beep + bold terminal print
"""
from __future__ import annotations

import sys


def send_watchlist_ready(tickers: list[str], date: str, candidate_count: int) -> None:
    """Fire a toast notification when DiscoveryAgent finishes."""
    top = ", ".join(tickers[:5])
    extra = f" +{candidate_count - 5} more" if candidate_count > 5 else ""
    title = f"Watchlist Ready — {date}"
    body = f"{top}{extra}"
    _notify(title, body)


def send_chart_alert(ticker: str, action: str, rationale: str) -> None:
    """Fire a toast for high-priority chart alerts."""
    title = f"{action.upper()} — {ticker}"
    body = rationale[:120]
    _notify(title, body)


def send_news_alert(ticker: str, headline: str) -> None:
    """Fire a toast for high-priority news hits."""
    title = f"News — {ticker}"
    body = headline[:120]
    _notify(title, body)


def _notify(title: str, body: str) -> None:
    if sys.platform != "win32":
        _fallback_print(title, body)
        return

    try:
        from winotify import Notification, audio
        toast = Notification(
            app_id="Trading Agents",
            title=title,
            msg=body,
            duration="long",
        )
        toast.set_audio(audio.Default, loop=False)
        toast.show()
        return
    except Exception:
        pass

    # Fallback: system sound + terminal
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception:
        pass
    _fallback_print(title, body)


def _fallback_print(title: str, body: str) -> None:
    bar = "=" * 60
    print(f"\n{bar}\n  {title}\n  {body}\n{bar}\n")
