"""TradingView MCP client — spawns the Node.js server via stdio and runs the tool loop.

The Anthropic beta MCP API only supports type:"url" (remote HTTP servers).
We use the `mcp` Python library instead to communicate with the local stdio server,
list its tools, and dispatch tool calls — then feed results back to Claude manually.
"""
from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

import anthropic
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from core.config import settings


def _mcp_server_params() -> StdioServerParameters:
    path = settings.mcp_server_path()
    return StdioServerParameters(
        command="node",
        args=[str(path / "src" / "server.js")],
        cwd=str(path),
    )


def check_mcp_server_available() -> bool:
    server_js = settings.mcp_server_path() / "src" / "server.js"
    if not server_js.exists():
        print(f"[tradingview_mcp] server.js not found at {server_js}", file=sys.stderr)
        return False
    try:
        result = subprocess.run(["node", "--version"], capture_output=True, timeout=5)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print("[tradingview_mcp] Node.js not found in PATH", file=sys.stderr)
        return False


def _content_to_str(content: Any) -> str:
    """Convert MCP tool result content to a string for Claude."""
    if isinstance(content, list):
        parts = []
        for item in content:
            if hasattr(item, "text"):
                parts.append(item.text)
            elif hasattr(item, "model_dump"):
                parts.append(json.dumps(item.model_dump()))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    if hasattr(content, "text"):
        return content.text
    return str(content)


async def run_with_tv_tools(
    client: anthropic.Anthropic,
    model: str,
    system: str,
    messages: list[dict],
    max_iterations: int = 15,
) -> str:
    """Run a Claude agentic loop with all TradingView MCP tools available."""
    params = _mcp_server_params()

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            mcp_tools_resp = await session.list_tools()
            tools = [
                {
                    "name": t.name,
                    "description": t.description or "",
                    "input_schema": t.inputSchema,
                }
                for t in mcp_tools_resp.tools
            ]

            response = None
            for _ in range(max_iterations):
                response = client.messages.create(
                    model=model,
                    max_tokens=4096,
                    system=system,
                    tools=tools,
                    messages=messages,
                )

                if response.stop_reason == "end_turn":
                    break

                if response.stop_reason == "tool_use":
                    messages.append({"role": "assistant", "content": response.content})
                    tool_results = []
                    for block in response.content:
                        if block.type != "tool_use":
                            continue
                        try:
                            result = await session.call_tool(block.name, block.input)
                            content_str = _content_to_str(result.content)
                        except Exception as exc:
                            content_str = f"Tool error: {exc}"
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": content_str,
                        })
                    messages.append({"role": "user", "content": tool_results})
                else:
                    break

            if response is None:
                return ""
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
            return ""
