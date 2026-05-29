# CLAUDE.md — trading_agents

Multi-agent trading platform. Python orchestrator that coordinates:
- **NewsAgent** — pre-market scanner (7 AM–9:30 AM EST), small-cap/low-float movers under $20
- **ChartAgent** — reads live TradingView charts via MCP, generates buy/sell alerts from rules
- **TradingAgent** — Webull execution (disabled by default — `EXECUTION_ENABLED=false`)
- **Orchestrator** — boots all agents, routes messages via async pub/sub

Connected to `C:\_repo\tradingview_mcp_jackson` (Node.js MCP server, 68 TradingView tools).

## Setup

```bash
pip install -e ".[dev]"
playwright install chromium
cp .env.example .env   # fill in real credentials — NEVER commit .env
```

## Commands

```bash
# Start full multi-agent orchestrator
python main.py run

# Adjust chart polling interval
python main.py run --chart-poll 60

# Legacy: analyze single ticker (screenshot → Claude vision → signal)
python main.py analyze AAPL

# With Webull execution (requires EXECUTION_ENABLED=true in .env)
python main.py analyze AAPL --execute

# Run tests
pytest
```

## Architecture

```
main.py run
  └─ Orchestrator              (agents/orchestrator.py)
       ├─ MessageBus           (core/message_bus.py)   — asyncio pub/sub
       ├─ NewsAgent            (agents/news_agent.py)
       │    ├─ yfinance        — price screening
       │    └─ Anthropic API   — web search beta for news catalysts
       │         → publishes NewsAlert on topic "news"
       └─ ChartAgent           (agents/chart_agent.py)
            ├─ Anthropic MCP beta
            └─ tradingview_mcp_jackson (node src/server.js)  — 68 TV tools
                 → publishes ChartAlert on topic "chart"

main.py analyze <TICKER>  (legacy path)
  └─ AnalysisAgent        (agents/analysis_agent.py)   — screenshot → vision
  └─ TradingAgent         (agents/trading_agent.py)    — Webull execution
```

## Key design decisions

- **`BaseAgent`** (`agents/base_agent.py`) — all agents inherit Claude client + tool-use loop. Default model: `claude-opus-4-7`.
- **`MessageBus`** — asyncio Queue-based pub/sub. Topics: `"news"`, `"chart"`, `"execution"`. Subscribe with a coroutine handler.
- **ChartAgent uses MCP beta** — calls `client.beta.messages.create` with `betas=["mcp-client-2025-04-04"]` and `mcp_servers=[mcp_server_config()]`. This starts the Node.js server as a subprocess and lets Claude call all 68 TradingView tools directly.
- **NewsAgent uses web search beta** — calls with `betas=["web-search-2025-03-05"]` to find pre-market news headlines.
- **Execution is disabled** — `EXECUTION_ENABLED=false` in `.env`. The orchestrator will forward alerts to TradingAgent only when this is true.
- **Rules from rules.json** — ChartAgent reads `tradingview_mcp_jackson/rules.json` for buy/sell criteria.

## Adding a new agent

1. Subclass `BaseAgent` in `agents/`
2. Define your tool list and `_dispatch_tools`
3. Inject `MessageBus` and call `await bus.publish(AgentMessage(...))`
4. Register in `Orchestrator.__init__` and `run()`

## Security

- **Never commit `.env`** — real credentials must stay local
- **Never commit `.env.example` with real values** — use placeholders only
- `EXECUTION_ENABLED=false` is the default safety guard — no real orders without explicit opt-in
