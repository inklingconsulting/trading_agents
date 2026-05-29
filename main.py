"""Entry point for the trading agent platform."""
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import asyncio
import argparse
import signal

from agents.analysis_agent import AnalysisAgent
from agents.discovery_agent import DiscoveryAgent
from agents.trading_agent import TradingAgent
from agents.orchestrator import Orchestrator
from brokers.webull import WebullBroker
from platforms.tradingview import TradingViewClient


async def run_orchestrator(chart_poll: int) -> None:
    orch = Orchestrator(chart_poll_interval=chart_poll)

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, orch.stop)
        except NotImplementedError:
            pass  # Windows doesn't support add_signal_handler for all signals

    try:
        await orch.run()
    except asyncio.CancelledError:
        orch.stop()


async def run_single(ticker: str, execute: bool, headless: bool) -> None:
    """Original single-ticker analysis + optional execution pipeline."""
    async with TradingViewClient(headless=headless) as tv:
        signal_obj = await AnalysisAgent().analyze(ticker, tv)

    print(f"Signal: {signal_obj.model_dump_json(indent=2)}")

    if execute:
        with WebullBroker() as broker:
            result = TradingAgent(broker).execute(signal_obj)
            if result:
                print(f"Order result: {result.model_dump_json(indent=2)}")
            else:
                print("Trading agent chose not to execute.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Trading agent platform")
    sub = parser.add_subparsers(dest="command", required=True)

    # Morning discovery scan
    sub.add_parser("discover", help="Find today's pre-market watchlist (run before 'run')")

    # Multi-agent orchestrator
    orch_p = sub.add_parser("run", help="Start full multi-agent orchestrator")
    orch_p.add_argument("--chart-poll", type=int, default=30, help="Chart polling interval in seconds")

    # Legacy single-ticker analysis
    single_p = sub.add_parser("analyze", help="Analyze a single ticker (legacy)")
    single_p.add_argument("ticker", help="Ticker symbol (e.g. AAPL)")
    single_p.add_argument("--execute", action="store_true", help="Execute trade via Webull")
    single_p.add_argument("--headless", action="store_true", help="Run browser headlessly")

    args = parser.parse_args()

    if args.command == "discover":
        asyncio.run(DiscoveryAgent().discover())
    elif args.command == "run":
        asyncio.run(run_orchestrator(args.chart_poll))
    elif args.command == "analyze":
        asyncio.run(run_single(args.ticker, args.execute, args.headless))
