"""Failure/degradation alerting.

Two channels, both best-effort:
- email to the owner via Resend (when configured);
- the GitHub Actions workflow additionally opens/updates an issue on any
  failed run (see .github/workflows/daily-brief.yml), which needs no
  secrets at all.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def send_alert(subject: str, body: str) -> bool:
    """Email the owner about a failed/degraded run. Never raises."""
    try:
        from .deliver.resend_client import send_email

        owner = os.environ.get("OWNER_EMAIL", "").strip()
        from_addr = os.environ.get("EMAIL_FROM", "").strip()
        if not owner or not from_addr or not os.environ.get("RESEND_API_KEY"):
            log.warning("alert email not configured; relying on workflow issue alert")
            return False
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        send_email(
            subject=f"[market-brief ALERT] {subject}",
            html=f"<pre>{body}</pre>",
            text=body,
            to=[owner],
            bcc=[],
            from_addr=from_addr,
            idempotency_key=f"alert-{stamp}",
        )
        return True
    except Exception as exc:  # noqa: BLE001 — alerting must never mask the real error
        log.error("alert email failed: %s", exc)
        return False
