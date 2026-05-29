"""Asyncio-based pub/sub message bus for inter-agent communication."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Callable, Awaitable

from core.models import AgentMessage


class MessageBus:
    """Simple topic-based pub/sub. Agents publish messages; subscribers receive them."""

    def __init__(self):
        self._subscribers: dict[str, list[Callable[[AgentMessage], Awaitable[None]]]] = defaultdict(list)
        self._queue: asyncio.Queue[AgentMessage] = asyncio.Queue()
        self._running = False

    def subscribe(self, topic: str, handler: Callable[[AgentMessage], Awaitable[None]]) -> None:
        self._subscribers[topic].append(handler)

    async def publish(self, message: AgentMessage) -> None:
        await self._queue.put(message)

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                msg = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            handlers = self._subscribers.get(msg.topic, [])
            if handlers:
                await asyncio.gather(*[h(msg) for h in handlers], return_exceptions=True)
            self._queue.task_done()

    def stop(self) -> None:
        self._running = False
