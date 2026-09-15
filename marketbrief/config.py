"""Typed loading of config/config.yaml — the single place settings live."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "config.yaml"


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    key: str
    label: str
    section: str
    source: str
    symbol: str
    cls: str
    freshness: str
    unit: str
    scale: float = 1.0
    note: str | None = None
    staleness_hours: float | None = None  # per-instrument override


@dataclass(frozen=True, slots=True)
class DerivedSpec:
    key: str
    label: str
    section: str
    kind: str
    a: str
    b: str
    note: str | None = None


@dataclass(frozen=True, slots=True)
class SanityRule:
    min: float
    max: float
    max_change_pct: float | None = None
    max_change_bp: float | None = None
    # bp per unit of the raw value: 100 for %-denominated series, 1 for
    # series already quoted in bp (credit OAS).
    bp_per_unit: float = 100.0


@dataclass(frozen=True, slots=True)
class DeliveryConfig:
    send_time_local: str
    holiday_calendar: str
    bond_calendar: str


@dataclass(frozen=True, slots=True)
class HealthConfig:
    critical_keys: tuple[str, ...]
    max_failure_ratio: float


@dataclass(frozen=True, slots=True)
class RunConfig:
    recipient_tier: str
    model: str


@dataclass(frozen=True, slots=True)
class GenerationConfig:
    effort: str
    max_tokens: int


@dataclass(frozen=True, slots=True)
class NewsConfig:
    max_items: int
    max_searches: int


@dataclass(frozen=True, slots=True)
class EmailConfig:
    subject_prefix: str
    sender_name: str
    sender_affiliation: str


@dataclass(frozen=True, slots=True)
class Config:
    timezone: str
    delivery: DeliveryConfig
    run: RunConfig
    generation: GenerationConfig
    news: NewsConfig
    email: EmailConfig
    instruments: tuple[InstrumentSpec, ...]
    derived: tuple[DerivedSpec, ...]
    sanity: dict[str, SanityRule]
    staleness_hours: dict[str, float]
    health: HealthConfig
    auction_typical_times_et: dict[str, str] = field(default_factory=dict)

    def instrument(self, key: str) -> InstrumentSpec:
        for spec in self.instruments:
            if spec.key == key:
                return spec
        raise KeyError(key)


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> Config:
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text())

    instruments = tuple(InstrumentSpec(**i) for i in raw["instruments"])
    keys = [i.key for i in instruments]
    if len(keys) != len(set(keys)):
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        raise ValueError(f"Duplicate instrument keys in config: {dupes}")

    derived = tuple(DerivedSpec(**d) for d in raw.get("derived", []))
    for d in derived:
        for leg in (d.a, d.b):
            if leg not in keys:
                raise ValueError(f"Derived {d.key!r} references unknown instrument {leg!r}")

    return Config(
        timezone=raw["timezone"],
        delivery=DeliveryConfig(**raw["delivery"]),
        run=RunConfig(**raw["run"]),
        generation=GenerationConfig(**raw["generation"]),
        news=NewsConfig(**raw["news"]),
        email=EmailConfig(**raw["email"]),
        instruments=instruments,
        derived=derived,
        sanity={k: SanityRule(**v) for k, v in raw["sanity"].items()},
        staleness_hours={k: float(v) for k, v in raw["staleness_hours"].items()},
        health=HealthConfig(
            critical_keys=tuple(raw["health"]["critical_keys"]),
            max_failure_ratio=float(raw["health"]["max_failure_ratio"]),
        ),
        auction_typical_times_et=dict(raw.get("auction_typical_times_et", {})),
    )
