"""Run orchestration — the only place the pipeline stages are wired together.

Modes:
- ``send``     scheduled path: send-day and delivery-window checks, sleep to
               the 17:15 ET Friday target, idempotency guard, staged
               recipients.
- ``preview``  full pipeline, sent to the owner only, immediately.
- ``dry-run``  full pipeline, writes out/email.html + out/email.txt, sends
               nothing (works without any API keys — narrative is a labeled
               placeholder when ANTHROPIC_API_KEY is missing).

Fail-closed invariants:
- degraded data  -> never distributed beyond self; owner gets a [DEGRADED]
  copy plus an alert, so the failure is visible without embarrassing anyone.
- generation or numeric-verification failure -> nothing is sent at all;
  alert only. A missing email is recoverable; a wrong one is not.
- more than an hour past the target -> skip and alert rather than send late.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import requests

from .alert import send_alert
from .config import Config
from .data.snapshot import build_snapshot
from .deliver.email_html import render_email
from .deliver.recipients import RecipientError, resolve_recipients
from .deliver.resend_client import send_email
from .generate.generator import (
    BriefNarrative,
    GenerationError,
    generate_narrative,
    placeholder_narrative,
)
from .news.search import NewsBundle, gather_news
from .render_table import render_table
from .schedule import local_trading_date, seconds_until_delivery, should_run

log = logging.getLogger(__name__)

OUT_DIR = Path("out")


@dataclass(slots=True)
class RunResult:
    mode: str
    outcome: str                      # sent | preview_sent | dry_run | skipped | failed
    detail: str = ""
    tier: str | None = None
    email_id: str | None = None
    recipients_bcc: int = 0
    health: dict[str, Any] | None = None
    artifacts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in
                ("mode", "outcome", "detail", "tier", "email_id",
                 "recipients_bcc", "health", "artifacts")}


def _already_sent_today(today: date) -> bool:
    """Idempotency guard: has a scheduled run of this workflow already
    succeeded today? Uses the GitHub API when running inside Actions; falls
    back to a local marker file elsewhere. Fails open to the local marker
    (the concurrency group in the workflow prevents parallel racing)."""
    marker = OUT_DIR / f"sent-{today.isoformat()}.marker"
    if marker.exists():
        return True
    repo = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GITHUB_TOKEN")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if not (repo and token and run_id):
        return False
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{repo}/actions/workflows/friday-note.yml/runs",
            params={"event": "schedule", "status": "success",
                    "created": f">={today.isoformat()}T00:00:00Z", "per_page": 10},
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json"},
            timeout=20,
        )
        resp.raise_for_status()
        runs = resp.json().get("workflow_runs", [])
        return any(str(r.get("id")) != run_id for r in runs)
    except Exception as exc:  # noqa: BLE001
        log.warning("idempotency API check failed (%s); relying on marker/concurrency", exc)
        return False


def _mark_sent(today: date) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / f"sent-{today.isoformat()}.marker").write_text(
        datetime.now(timezone.utc).isoformat())


def _sleep_until(target_seconds_from_now: float) -> None:
    while target_seconds_from_now > 0:
        chunk = min(target_seconds_from_now, 300)
        log.info("sleeping %.0fs toward delivery target", chunk)
        time.sleep(chunk)
        target_seconds_from_now -= chunk


def _write_outputs(subject: str, html: str, text: str, snapshot, result: RunResult) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "email.html").write_text(html)
    (OUT_DIR / "email.txt").write_text(f"Subject: {subject}\n\n{text}")
    (OUT_DIR / "snapshot.json").write_text(json.dumps(snapshot.to_dict(), indent=2))
    (OUT_DIR / "verification-table.txt").write_text(render_table(snapshot))
    result.artifacts = ["out/email.html", "out/email.txt", "out/snapshot.json",
                        "out/verification-table.txt"]


def _write_summary(result: RunResult) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "run-summary.json").write_text(json.dumps({
        **result.to_dict(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2))


def run(cfg: Config, mode: str) -> RunResult:
    result = RunResult(mode=mode, outcome="failed")
    try:
        result = _run_inner(cfg, mode, result)
    except Exception as exc:
        log.exception("run failed")
        result.outcome = "failed"
        result.detail = f"{type(exc).__name__}: {exc}"
        send_alert(f"run failed ({mode})", result.detail)
        _write_summary(result)
        raise
    _write_summary(result)
    return result


def _run_inner(cfg: Config, mode: str, result: RunResult) -> RunResult:
    now = datetime.now(timezone.utc)
    tier = os.environ.get("RECIPIENT_TIER", cfg.run.recipient_tier).strip() or "self"
    result.tier = tier
    today = now.astimezone().date()

    if mode == "send":
        ok, why = should_run(cfg, now)
        if not ok:
            log.info("send skipped: %s", why)
            if "too late" in why:
                send_alert("late run skipped", why)
            result.outcome, result.detail = "skipped", why
            return result
        today = local_trading_date(cfg, now)
        if _already_sent_today(today):
            result.outcome, result.detail = "skipped", "already sent today (idempotency)"
            log.info(result.detail)
            return result
        # Fetch close to the wire: sleep until ~12 minutes before target.
        lead = seconds_until_delivery(cfg, datetime.now(timezone.utc)) - 12 * 60
        if lead > 0:
            _sleep_until(lead)

    # ---- Data ----
    snapshot = build_snapshot(cfg)
    result.health = snapshot.health.to_dict()
    log.info("snapshot: fetched=%d failed=%d rejected=%d degraded=%s",
             snapshot.health.fetched, snapshot.health.failed,
             snapshot.health.rejected, snapshot.health.degraded)

    # ---- News (best-effort) ----
    news: NewsBundle = gather_news(cfg, snapshot)

    # ---- Narrative (fail-closed except in dry-run without a key) ----
    narrative: BriefNarrative
    if not os.environ.get("ANTHROPIC_API_KEY") and mode == "dry-run":
        narrative = placeholder_narrative()
    else:
        try:
            narrative = generate_narrative(cfg, snapshot, news)
        except GenerationError as exc:
            send_alert("generation failed, nothing sent",
                       f"{exc}\n\nHealth: {json.dumps(result.health, indent=1)}")
            raise

    # ---- Fail closed on degraded data ----
    effective_tier = tier
    if snapshot.health.degraded and tier != "self":
        effective_tier = "self"
        send_alert("degraded data, send restricted to self",
                   "\n".join(snapshot.health.reasons))
        log.warning("degraded run: tier %s -> self", tier)
    result.tier = effective_tier

    subject, html, text = render_email(cfg, snapshot, narrative, news,
                                       tier=effective_tier)
    _write_outputs(subject, html, text, snapshot, result)

    if mode == "dry-run":
        result.outcome = "dry_run"
        result.detail = "rendered to out/email.html; nothing sent"
        log.info(result.detail)
        return result

    try:
        recipients = resolve_recipients("self" if mode == "preview" else effective_tier)
    except RecipientError as exc:
        send_alert("recipient configuration error, nothing sent", str(exc))
        raise

    if mode == "send":
        # Hold the send until the delivery instant itself.
        remaining = seconds_until_delivery(cfg, datetime.now(timezone.utc))
        if remaining > 0:
            _sleep_until(remaining)

    email_id = send_email(
        subject=subject, html=html, text=text,
        to=recipients.to, bcc=recipients.bcc, from_addr=recipients.from_addr,
        reply_to=os.environ.get("EMAIL_REPLY_TO") or None,
        idempotency_key=f"brief-{today.isoformat()}-{mode}",
    )
    result.email_id = email_id
    result.recipients_bcc = len(recipients.bcc)
    if mode == "send":
        _mark_sent(today)
        result.outcome = "sent"
    else:
        result.outcome = "preview_sent"
    result.detail = (f"sent to {len(recipients.to)} + {len(recipients.bcc)} bcc "
                     f"(tier={recipients.tier})")
    log.info(result.detail)
    return result
