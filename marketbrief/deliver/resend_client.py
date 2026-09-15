"""Resend delivery client.

Why Resend: real free tier that fits a daily list (3,000 emails/month,
100/day), first-class domain authentication (SPF/DKIM walked through in
their dashboard), and a simple JSON API. Postmark's free tier (100/month)
cannot cover a daily send; SES is cheaper at scale but slow to set up and
starts sandboxed. The client sets an Idempotency-Key so a retried request
cannot double-send.
"""

from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger(__name__)

API_URL = "https://api.resend.com/emails"


class DeliveryError(Exception):
    pass


def send_email(*, subject: str, html: str, text: str, to: list[str],
               bcc: list[str], from_addr: str, idempotency_key: str,
               reply_to: str | None = None, timeout: float = 30.0) -> str:
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    if not api_key:
        raise DeliveryError("RESEND_API_KEY is not set")

    payload: dict[str, object] = {
        "from": from_addr,
        "to": to,
        "subject": subject,
        "html": html,
        "text": text,
    }
    if bcc:
        payload["bcc"] = bcc
    if reply_to:
        payload["reply_to"] = reply_to

    resp = requests.post(
        API_URL,
        json=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Idempotency-Key": idempotency_key,
        },
        timeout=timeout,
    )
    if resp.status_code >= 400:
        raise DeliveryError(f"Resend {resp.status_code}: {resp.text[:500]}")
    email_id = str(resp.json().get("id", ""))
    log.info("email sent: id=%s to=%d bcc=%d", email_id, len(to), len(bcc))
    return email_id
