"""Entry point for the trading agent platform.

Daily workflow
--------------
1. python main.py discover
      Morning scan: finds pre-market gappers with catalysts, saves watchlist,
      sends a notification, then exits. Run this before market open.

2. Open stocks manually in TradingView and review them yourself.

3. python main.py watch
      Turn on the chart watcher for whatever stock you have open in TradingView.
      It polls on a configurable interval and notifies you when buy/sell conditions
      are met. Press Ctrl+C to stop. Only runs when you want it.
"""
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import asyncio
import argparse
import signal

from agents.discovery_agent import DiscoveryAgent
from agents.watcher import ChartWatcher


async def run_watcher(poll: int, min_strength: str, actions: set[str]) -> None:
    watcher = ChartWatcher(
        poll_interval=poll,
        min_strength=min_strength,
        notify_actions=actions,
    )
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, watcher.stop)
        except NotImplementedError:
            pass  # Windows
    try:
        await watcher.run()
    except (asyncio.CancelledError, KeyboardInterrupt):
        watcher.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Trading agent platform",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main.py discover\n"
            "  python main.py watch\n"
            "  python main.py watch --poll 60 --strength strong\n"
            "  python main.py watch --actions buy,sell,watch\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- discover ---
    sub.add_parser(
        "discover",
        help="Morning scan: find pre-market gappers, save watchlist, notify, exit",
    )

    # --- watch ---
    watch_p = sub.add_parser(
        "watch",
        help="Watch the current TradingView chart and notify on signals (Ctrl+C to stop)",
    )
    watch_p.add_argument(
        "--poll", type=int, default=30,
        help="Seconds between chart reads (default: 30)",
    )
    watch_p.add_argument(
        "--strength", default="moderate",
        choices=["weak", "moderate", "strong"],
        help="Minimum signal strength to trigger a notification (default: moderate)",
    )
    watch_p.add_argument(
        "--actions", default="buy,sell",
        help="Comma-separated actions that trigger a notification (default: buy,sell)",
    )

    args = parser.parse_args()

    if args.command == "discover":
        asyncio.run(DiscoveryAgent().discover())

    elif args.command == "watch":
        actions = {a.strip().lower() for a in args.actions.split(",") if a.strip()}
        asyncio.run(run_watcher(args.poll, args.strength, actions))
