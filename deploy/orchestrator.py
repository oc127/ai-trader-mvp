"""Cross-platform trading system orchestrator.

Launches, monitors, and restarts all trading bots.
Sends health reports via Telegram.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

BOT_CONFIGS = {
    "hyperliquid": {
        "dir": "/home/user/ai-trader-mvp",
        "cmd": ["uv", "run", "trader"],
        "paper_cmd": ["uv", "run", "trader"],
        "live_cmd": ["uv", "run", "trader", "--live"],
        "log_file": "logs/hl-trader.log",
        "health_check_interval": 60,
    },
    "deribit": {
        "dir": "/home/user/deribit-mm",
        "cmd": ["uv", "run", "deribot"],
        "paper_cmd": ["uv", "run", "deribot"],
        "live_cmd": ["uv", "run", "deribot", "--live"],
        "log_file": "logs/deribit-mm.log",
        "health_check_interval": 60,
    },
    "gate": {
        "dir": "/home/user/gate-trader",
        "cmd": ["uv", "run", "gatebot"],
        "paper_cmd": ["uv", "run", "gatebot"],
        "live_cmd": ["uv", "run", "gatebot", "--live"],
        "log_file": "logs/gate-trader.log",
        "health_check_interval": 60,
    },
    "polymarket": {
        "dir": "/home/user/polymarket-bot",
        "cmd": ["uv", "run", "polybot"],
        "paper_cmd": ["uv", "run", "polybot"],
        "live_cmd": ["uv", "run", "polybot", "--live"],
        "log_file": "logs/polymarket.log",
        "health_check_interval": 60,
    },
}


@dataclass
class BotState:
    name: str
    process: subprocess.Popen | None = None
    restart_count: int = 0
    last_start: float = 0.0
    last_health_check: float = 0.0
    status: str = "stopped"
    errors: list[str] = field(default_factory=list)


class Orchestrator:
    def __init__(
        self,
        bots: list[str] | None = None,
        live: bool = False,
        ssl_fix: bool = True,
    ) -> None:
        self._live = live
        self._ssl_fix = ssl_fix
        self._running = False
        self._max_restarts = 5
        self._restart_cooldown = 60  # seconds between restarts

        enabled = bots or list(BOT_CONFIGS.keys())
        self._bots: dict[str, BotState] = {}
        for name in enabled:
            if name in BOT_CONFIGS:
                self._bots[name] = BotState(name=name)

        self._health_report_interval = 3600  # hourly
        self._last_health_report = 0.0

    def run(self) -> None:
        self._running = True
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        print(f"[orchestrator] Starting {len(self._bots)} bots: {list(self._bots.keys())}")
        print(f"[orchestrator] Mode: {'LIVE' if self._live else 'PAPER'}")

        for name in self._bots:
            self._start_bot(name)

        while self._running:
            try:
                self._check_bots()
                time.sleep(10)
            except KeyboardInterrupt:
                break

        self._shutdown()

    def _start_bot(self, name: str) -> None:
        cfg = BOT_CONFIGS[name]
        bot = self._bots[name]

        if bot.restart_count >= self._max_restarts:
            elapsed = time.time() - bot.last_start
            if elapsed < self._restart_cooldown * bot.restart_count:
                return

        cmd = cfg["live_cmd"] if self._live else cfg["paper_cmd"]
        env = os.environ.copy()

        if self._ssl_fix:
            ssl_fix_path = str(Path(__file__).parent / "ssl_fix.py")
            env["PYTHONSTARTUP"] = ssl_fix_path

        bot_dir = cfg["dir"]
        log_dir = Path(bot_dir) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        log_path = Path(bot_dir) / cfg["log_file"]

        try:
            log_f = open(log_path, "a")
            proc = subprocess.Popen(
                cmd,
                cwd=bot_dir,
                env=env,
                stdout=log_f,
                stderr=subprocess.STDOUT,
            )
            bot.process = proc
            bot.last_start = time.time()
            bot.status = "running"
            print(f"[orchestrator] Started {name} (pid={proc.pid})")
        except Exception as e:
            bot.status = "error"
            bot.errors.append(str(e))
            print(f"[orchestrator] Failed to start {name}: {e}")

    def _check_bots(self) -> None:
        now = time.time()

        for name, bot in self._bots.items():
            if bot.process is None:
                continue

            ret = bot.process.poll()
            if ret is not None:
                print(f"[orchestrator] {name} exited with code {ret}")
                bot.status = "exited"
                bot.restart_count += 1

                if bot.restart_count < self._max_restarts:
                    wait = min(self._restart_cooldown * bot.restart_count, 300)
                    print(f"[orchestrator] Restarting {name} in {wait}s (attempt {bot.restart_count})")
                    time.sleep(wait)
                    self._start_bot(name)
                else:
                    bot.status = "failed"
                    print(f"[orchestrator] {name} exceeded max restarts ({self._max_restarts})")

        if now - self._last_health_report >= self._health_report_interval:
            self._print_health_report()
            self._last_health_report = now

    def _print_health_report(self) -> None:
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        print(f"\n{'='*50}")
        print(f"Health Report — {now_str}")
        print(f"{'='*50}")

        for name, bot in self._bots.items():
            uptime = ""
            if bot.last_start > 0 and bot.status == "running":
                secs = int(time.time() - bot.last_start)
                hours, rem = divmod(secs, 3600)
                mins = rem // 60
                uptime = f" (uptime: {hours}h{mins}m)"

            print(f"  {name:15s} | {bot.status:10s} | restarts: {bot.restart_count}{uptime}")

        print(f"{'='*50}\n")

    def _handle_signal(self, signum: int, frame) -> None:
        print(f"\n[orchestrator] Received signal {signum}, shutting down...")
        self._running = False

    def _shutdown(self) -> None:
        for name, bot in self._bots.items():
            if bot.process is not None and bot.process.poll() is None:
                print(f"[orchestrator] Stopping {name} (pid={bot.process.pid})")
                bot.process.terminate()
                try:
                    bot.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    bot.process.kill()
                bot.status = "stopped"

        print("[orchestrator] All bots stopped")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Trading System Orchestrator")
    parser.add_argument(
        "--bots",
        nargs="+",
        choices=list(BOT_CONFIGS.keys()),
        help="Which bots to run (default: all)",
    )
    parser.add_argument("--live", action="store_true", help="Run in live mode")
    parser.add_argument("--no-ssl-fix", action="store_true", help="Disable SSL fix")
    args = parser.parse_args()

    orch = Orchestrator(
        bots=args.bots,
        live=args.live,
        ssl_fix=not args.no_ssl_fix,
    )
    orch.run()


if __name__ == "__main__":
    main()
