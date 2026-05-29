from __future__ import annotations

import asyncio
import anthropic

from core.config import settings

DEFAULT_MODEL = "claude-sonnet-4-6"


class BaseAgent:
    """Shared Claude client and agentic loop utilities."""

    def __init__(self, model: str = DEFAULT_MODEL):
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.model = model

    def _run_loop(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_iterations: int = 10,
    ) -> str:
        for _ in range(max_iterations):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system,
                tools=tools,
                messages=messages,
            )

            if response.stop_reason == "end_turn":
                return self._extract_text(response)

            if response.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": response.content})
                tool_results = self._dispatch_tools(response.content, tools)
                messages.append({"role": "user", "content": tool_results})
            else:
                break

        return self._extract_text(response)

    async def _run_loop_async(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_iterations: int = 10,
    ) -> str:
        """Async wrapper — runs _run_loop in thread pool to avoid blocking."""
        return await asyncio.get_event_loop().run_in_executor(
            None, self._run_loop, system, messages, tools, max_iterations
        )

    def _dispatch_tools(self, content_blocks: list, tools: list[dict]) -> list[dict]:
        raise NotImplementedError

    @staticmethod
    def _extract_text(response) -> str:
        for block in response.content:
            if hasattr(block, "text"):
                return block.text
        return ""
