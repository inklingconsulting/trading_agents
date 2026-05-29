"""Notification sender: Windows toast + optional ntfy.sh phone push.

Set NTFY_TOPIC in .env to enable phone notifications.
Subscribe in the ntfy app (iOS/Android/Windows/Mac): https://ntfy.sh
No account required for basic use.
"""
from __future__ import annotations

import sys
import threading

import httpx

_NTFY_BASE = "https://ntfy.sh"

_NTFY_PRIORITY = {
    "low": "low",
    "default": "default",
    "high": "high",
    "urgent": "urgent",
}


def notify(
    title: str,
    message: str,
    priority: str = "default",
    tags: list[str] | None = None,
) -> None:
    """Fire-and-forget desktop toast + optional ntfy.sh push notification."""
    threading.Thread(
        target=lambda: (_toast(title, message), _ntfy(title, message, priority, tags or [])),
        daemon=True,
    ).start()


# --- convenience wrappers used by existing agents ---

def send_watchlist_ready(tickers: list[str], date: str, candidate_count: int) -> None:
    top = ", ".join(tickers[:5])
    extra = f" +{candidate_count - 5} more" if candidate_count > 5 else ""
    notify(
        title=f"Watchlist Ready - {date}",
        message=f"{top}{extra}",
        priority="default",
        tags=["clipboard"],
    )


def send_chart_alert(ticker: str, action: str, rationale: str) -> None:
    tags = {"buy": ["chart_increasing"], "sell": ["chart_decreasing"]}.get(action.lower(), ["eyes"])
    priority = "high" if action.lower() in ("buy", "sell") else "default"
    notify(
        title=f"{action.upper()} - {ticker}",
        message=rationale[:200],
        priority=priority,
        tags=tags,
    )


def send_news_alert(ticker: str, headline: str) -> None:
    notify(
        title=f"News - {ticker}",
        message=headline[:200],
        priority="default",
        tags=["newspaper"],
    )


# --- internals ---

def _toast(title: str, body: str) -> None:
    if sys.platform != "win32":
        _fallback_print(title, body)
        return
    try:
        from winotify import Notification, audio
        toast = Notification(
            app_id="Trading Agents",
            title=title,
            msg=body[:200],
            duration="long",
        )
        toast.set_audio(audio.Default, loop=False)
        toast.show()
        return
    except Exception:
        pass
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception:
        pass
    _fallback_print(title, body)


def _ntfy(title: str, message: str, priority: str, tags: list[str]) -> None:
    try:
        from core.config import settings
        topic = settings.ntfy_topic
    except Exception:
        return
    if not topic:
        return
    try:
        headers: dict[str, str] = {
            "Title": title,
            "Priority": _NTFY_PRIORITY.get(priority, "default"),
        }
        if tags:
            headers["Tags"] = ",".join(tags)
        httpx.post(
            f"{_NTFY_BASE}/{topic}",
            content=message.encode("utf-8"),
            headers=headers,
            timeout=5,
        )
    except Exception:
        pass


def _fallback_print(title: str, body: str) -> None:
    bar = "=" * 60
    print(f"\n{bar}\n  {title}\n  {body}\n{bar}\n")
