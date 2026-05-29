"""Push notification sender.

Desktop: winotify (Windows toast, no config needed).
Phone:   ntfy.sh — set NTFY_TOPIC in .env, then subscribe to that topic
         in the ntfy app (iOS / Android / Windows / Mac / browser).
         Free tier: https://ntfy.sh  — no account needed.

Never raises: failures are logged but never crash the trading loop.
"""
from __future__ import annotations

import threading

import httpx

from core.config import settings

_NTFY_BASE = "https://ntfy.sh"

_PRIORITY_MAP = {
    "low": "low",
    "medium": "default",
    "high": "high",
    "urgent": "urgent",
}


def _toast(title: str, message: str) -> None:
    try:
        from winotify import Notification
        n = Notification(
            app_id="Trading Agents",
            title=title,
            msg=message[:200],
            duration="short",
        )
        n.show()
    except Exception:
        pass


def _ntfy(title: str, message: str, priority: str, tags: list[str]) -> None:
    if not settings.ntfy_topic:
        return
    try:
        headers: dict[str, str] = {
            "Title": title,
            "Priority": _PRIORITY_MAP.get(priority, "default"),
        }
        if tags:
            headers["Tags"] = ",".join(tags)
        httpx.post(
            f"{_NTFY_BASE}/{settings.ntfy_topic}",
            content=message.encode("utf-8"),
            headers=headers,
            timeout=5,
        )
    except Exception:
        pass


def notify(
    title: str,
    message: str,
    priority: str = "default",
    tags: list[str] | None = None,
) -> None:
    """Fire-and-forget: desktop toast + optional ntfy.sh push."""
    _tags = tags or []
    threading.Thread(
        target=lambda: (_toast(title, message), _ntfy(title, message, priority, _tags)),
        daemon=True,
    ).start()
