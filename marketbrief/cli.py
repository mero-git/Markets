"""Command-line interface. All logic lives in the modules; this only wires."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import DEFAULT_CONFIG_PATH, load_config
from .data.snapshot import build_snapshot
from .render_table import render_table


def _cmd_fetch(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    snap = build_snapshot(cfg)
    if args.json:
        Path(args.json).write_text(json.dumps(snap.to_dict(), indent=2))
        logging.info("snapshot written to %s", args.json)
    print(render_table(snap))
    if args.strict and snap.health.degraded:
        return 2
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    from .runner import run

    cfg = load_config(args.config)
    result = run(cfg, args.mode)
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.outcome != "failed" else 1


def _cmd_render(args: argparse.Namespace) -> int:
    """Re-render the email offline from a saved snapshot.json (no network)."""
    from .deliver.email_html import render_email
    from .generate.generator import placeholder_narrative
    from .models import MarketSnapshot
    from .news.search import NewsBundle

    cfg = load_config(args.config)
    snap = MarketSnapshot.from_dict(json.loads(Path(args.snapshot).read_text()))
    subject, html, text = render_email(
        cfg, snap, placeholder_narrative(),
        NewsBundle(available=False, note="offline render"), tier="self")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "email.html").write_text(html)
    (out / "email.txt").write_text(f"Subject: {subject}\n\n{text}")
    print(f"rendered {out}/email.html and {out}/email.txt")
    return 0


def _cmd_schedule(args: argparse.Namespace) -> int:
    from datetime import date

    from .schedule import next_send_datetimes_utc

    cfg = load_config(args.config)
    for dt in next_send_datetimes_utc(cfg, date.today(), n=args.n):
        print(f"{dt.isoformat()}  (= {cfg.delivery.send_time_local} {cfg.timezone})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="marketbrief")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch", help="fetch market data and print the verification table")
    p_fetch.add_argument("--json", help="also write the full snapshot JSON to this path")
    p_fetch.add_argument("--strict", action="store_true",
                         help="exit non-zero if the run would be degraded")
    p_fetch.set_defaults(func=_cmd_fetch)

    p_run = sub.add_parser("run", help="full pipeline: fetch, news, generate, verify, deliver")
    p_run.add_argument("--mode", choices=["send", "preview", "dry-run"], default="dry-run",
                       help="send: scheduled staged send; preview: email owner only now; "
                            "dry-run: render to out/, send nothing")
    p_run.set_defaults(func=_cmd_run)

    p_render = sub.add_parser("render", help="re-render the email offline from a snapshot.json")
    p_render.add_argument("--snapshot", default="out/snapshot.json")
    p_render.add_argument("--out", default="out")
    p_render.set_defaults(func=_cmd_render)

    p_sched = sub.add_parser("schedule", help="print the next send times in UTC (DST check)")
    p_sched.add_argument("-n", type=int, default=5)
    p_sched.set_defaults(func=_cmd_schedule)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    return args.func(args)
