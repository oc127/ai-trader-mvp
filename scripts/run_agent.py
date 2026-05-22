"""Interactive AI Trading Agent — natural language trading on Hyperliquid.

Usage:
    python3 scripts/run_agent.py              # paper mode (default)
    python3 scripts/run_agent.py --live        # real trading
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent.core import TradingAgent
from src.agent.memory import Memory
from src.agent.tools import TradingTools
from src.hl_client.rest import HLRestClient


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Trading Agent")
    parser.add_argument("--live", action="store_true", help="Enable real trading")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Error: set ANTHROPIC_API_KEY environment variable")
        sys.exit(1)

    if not os.environ.get("HL_PRIVATE_KEY"):
        print("Error: set HL_PRIVATE_KEY environment variable")
        sys.exit(1)

    paper = not args.live
    mode = "PAPER" if paper else "LIVE"

    if args.live:
        print("\n  *** WARNING: LIVE TRADING MODE ***")
        confirm = input("  Type 'YES' to confirm: ")
        if confirm.strip() != "YES":
            print("  Aborted.")
            sys.exit(0)

    client = HLRestClient({"exchange": {"use_testnet": False}})
    tools = TradingTools(client, paper=paper)
    memory = Memory()
    agent = TradingAgent(tools, memory)

    print(f"\n  AI Trading Agent ({mode})")
    print(f"  连接到 Hyperliquid mainnet")
    print(f"  输入你的交易指令，用自然语言即可。")
    print(f"  输入 'quit' 退出，'reset' 清空对话。\n")

    while True:
        try:
            user_input = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("再见！")
            break
        if user_input.lower() == "reset":
            agent.reset()
            print("对话已重置。\n")
            continue

        try:
            reply = agent.chat(user_input)
            print(f"\nAgent: {reply}\n")
        except Exception as e:
            print(f"\n错误: {e}\n")


if __name__ == "__main__":
    main()
