from __future__ import annotations

import argparse
from pathlib import Path

from biubiu_invest.data_providers import (
    AkShareProvider,
    FetchRequest,
    SampleCsvProvider,
    load_watchlist,
)
from biubiu_invest.audit import make_report_event, write_audit_event
from biubiu_invest.momentum import compute_momentum
from biubiu_invest.policy import load_policy
from biubiu_invest.paths import audit_log_path, data_dir, default_db_path, reports_dir, repo_root
from biubiu_invest.report import ReportConfig, render_markdown
from biubiu_invest.storage import connect, init_schema, load_daily_bars, upsert_daily_bars


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate A-share daily momentum report (MVP).")
    p.add_argument("--provider", default="sample", choices=["sample", "akshare"])
    p.add_argument("--watchlist", default=str(data_dir() / "watchlist.example.txt"))
    p.add_argument("--start", default=None, help="YYYY-MM-DD (optional)")
    p.add_argument("--end", default=None, help="YYYY-MM-DD (optional)")
    p.add_argument("--lookback", type=int, default=3, help="lookback days for momentum (MVP default small)")
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--db", default=str(default_db_path()))
    p.add_argument("--out", default=None, help="Output markdown path (default: reports/YYYY-MM-DD.md)")
    p.add_argument("--policy", default=None, help="Path to policy JSON (default: policies/default_strict.json)")
    p.add_argument("--audit", default=str(audit_log_path()), help="Append-only audit log path (JSONL)")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    wl_path = Path(args.watchlist).expanduser().resolve()
    ts_codes = load_watchlist(wl_path)
    if not ts_codes:
        raise SystemExit(f"Empty watchlist: {wl_path}")

    if args.provider == "sample":
        provider = SampleCsvProvider(csv_path=(data_dir() / "sample_daily.csv"))
    else:
        provider = AkShareProvider()

    req = FetchRequest(ts_codes=ts_codes, start_date=args.start, end_date=args.end)
    bars = provider.fetch_daily_bars(req)

    db_path = Path(args.db).expanduser().resolve()
    conn = connect(db_path)
    init_schema(conn)
    upsert_daily_bars(conn, bars)

    stored = load_daily_bars(conn, ts_codes=ts_codes, start_date=args.start, end_date=args.end)
    signals = compute_momentum(stored, lookback=args.lookback)

    asof = signals[0].asof_date if signals else (args.end or "N/A")
    policy = load_policy(args.policy)
    md = render_markdown(asof, signals, cfg=ReportConfig(top_n=args.top), policy=policy)

    out_path: Path
    if args.out:
        out_path = Path(args.out).expanduser().resolve()
    else:
        reports_dir().mkdir(parents=True, exist_ok=True)
        out_path = (reports_dir() / f"{asof}.md").resolve()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")

    audit_path = Path(args.audit).expanduser().resolve()
    write_audit_event(
        audit_path,
        make_report_event(
            policy_id=policy.id,
            provider=args.provider,
            watchlist_path=str(wl_path),
            db_path=str(db_path),
            out_path=str(out_path),
            params={
                "start": args.start,
                "end": args.end,
                "lookback": args.lookback,
                "top": args.top,
            },
        ),
    )

    rel = out_path.relative_to(repo_root()) if repo_root() in out_path.parents else out_path
    print(f"Report written: {rel}")


if __name__ == "__main__":
    main()


