"""Recipient resolution for staged sending.

Tiers: self (owner only) -> test (small trusted group) -> full (everyone).
Lists live in environment/secrets, never in code. Sending beyond `self`
from a shared/test domain is refused outright — a note to an MD must come
from an authenticated domain the sender owns.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

VALID_TIERS = ("self", "test", "full")

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SHARED_TEST_DOMAINS = ("resend.dev", "resend.com")


class RecipientError(Exception):
    """Misconfigured recipients — fail closed rather than mis-send."""


@dataclass(slots=True)
class Recipients:
    tier: str
    to: list[str]        # visible recipient (the owner)
    bcc: list[str]       # the distribution list — never in To/Cc
    from_addr: str


def _split(raw: str | None) -> list[str]:
    out: list[str] = []
    for part in (raw or "").replace(";", ",").split(","):
        addr = part.strip()
        if not addr:
            continue
        if not _EMAIL.match(addr):
            raise RecipientError(f"invalid email address in list: {addr!r}")
        if addr not in out:
            out.append(addr)
    return out


def _from_domain(from_addr: str) -> str:
    inner = from_addr.split("<")[-1].rstrip(">").strip()
    if "@" not in inner:
        raise RecipientError(f"EMAIL_FROM has no domain: {from_addr!r}")
    return inner.rsplit("@", 1)[1].lower()


def resolve_recipients(tier: str) -> Recipients:
    if tier not in VALID_TIERS:
        raise RecipientError(f"unknown RECIPIENT_TIER {tier!r}; use one of {VALID_TIERS}")

    owner = _split(os.environ.get("OWNER_EMAIL"))
    if not owner:
        raise RecipientError("OWNER_EMAIL is not set")
    from_addr = os.environ.get("EMAIL_FROM", "").strip()
    if not from_addr:
        raise RecipientError("EMAIL_FROM is not set")

    if tier == "self":
        bcc: list[str] = []
    else:
        domain = _from_domain(from_addr)
        if any(domain == d or domain.endswith("." + d) for d in _SHARED_TEST_DOMAINS):
            raise RecipientError(
                f"refusing to send tier={tier!r} from shared test domain {domain!r}; "
                "verify your own sending domain first (see README)")
        var = "RECIPIENTS_TEST" if tier == "test" else "RECIPIENTS_FULL"
        bcc = [a for a in _split(os.environ.get(var)) if a not in owner]
        if not bcc:
            raise RecipientError(f"{var} is empty, nothing to send for tier {tier!r}")

    return Recipients(tier=tier, to=owner, bcc=bcc, from_addr=from_addr)
