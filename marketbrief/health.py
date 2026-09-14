"""Degraded-run decision — the fail-closed gate.

The rule: a wrong or hollow email to the full list is worse than no email.
A run is DEGRADED (never sent beyond `self`, alert raised) when any critical
field is missing or overall fetch quality drops below the configured bar.
"""

from __future__ import annotations

from .config import HealthConfig
from .models import DataPoint, FetchFailure, HealthReport, Rejection


def assess_health(
    *,
    attempted: int,
    points: list[DataPoint],
    failures: list[FetchFailure],
    rejections: list[Rejection],
    cfg: HealthConfig,
) -> HealthReport:
    present = {p.key for p in points}
    critical_missing = [k for k in cfg.critical_keys if k not in present]
    bad = len(failures) + len(rejections)
    ratio = (bad / attempted) if attempted else 1.0

    reasons: list[str] = []
    if critical_missing:
        reasons.append(f"critical fields missing: {', '.join(critical_missing)}")
    if ratio > cfg.max_failure_ratio:
        reasons.append(f"failure ratio {ratio:.0%} exceeds {cfg.max_failure_ratio:.0%}")
    if attempted == 0:
        reasons.append("no fetches attempted")

    return HealthReport(
        attempted=attempted,
        fetched=len(points),
        failed=len(failures),
        rejected=len(rejections),
        critical_missing=critical_missing,
        failure_ratio=round(ratio, 4),
        degraded=bool(reasons),
        reasons=reasons,
    )
