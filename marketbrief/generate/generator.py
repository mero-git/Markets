"""Brief narrative generation via the Anthropic API.

The model writes ONLY narrative: the TLDR, one-to-two-line commentary per
section, and the news lines. All numeric tables in the email are rendered
by code straight from the snapshot, so a hallucinated table is structurally
impossible. The narrative is still numerically post-validated by
``verify.verify_narrative`` and the run fails closed on any violation.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from typing import Any

from ..config import Config
from ..models import MarketSnapshot
from ..news.search import NewsBundle
from .verify import verify_narrative

log = logging.getLogger(__name__)


class GenerationError(Exception):
    """Generation failed — the run must fail closed (no send)."""


class VerificationError(GenerationError):
    """Generated text contained numbers not present in the data payload."""


@dataclass(slots=True)
class BriefNarrative:
    tldr: list[str]
    curve_comment: str | None = None
    credit_comment: str | None = None
    supply_comment: str | None = None
    equities_comment: str | None = None
    fx_commodities_comment: str | None = None
    canada_comment: str | None = None
    news_intro: str | None = None
    generated: bool = True         # False => placeholder (dry-run without key)
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def text_fields(self) -> dict[str, str]:
        return {
            "tldr": " ".join(self.tldr),
            "curve_comment": self.curve_comment or "",
            "credit_comment": self.credit_comment or "",
            "supply_comment": self.supply_comment or "",
            "equities_comment": self.equities_comment or "",
            "fx_commodities_comment": self.fx_commodities_comment or "",
            "canada_comment": self.canada_comment or "",
            "news_intro": self.news_intro or "",
        }


_SYSTEM = """You write the narrative for "Blackleaf Capital News Update", a 6:30am \
ET fixed-income-first market brief read by trading MDs in about 45 seconds.

ABSOLUTE DATA RULE — this is the one unbreakable constraint:
Every number you write must appear VERBATIM in the JSON payload you are given \
(same digits; you may drop trailing zeros or a sign). Never compute, convert, \
round to new digits, estimate, or recall a number from memory. Never do \
arithmetic — all spreads and changes are already computed in the payload. If a \
number you want is not in the payload, write the sentence without it. Your output \
is machine-checked against the payload and the run is aborted if any numeral \
fails to match.

VOICE: buy-side desk note. Sharp, declarative, specific. No hedging, no filler, \
no "investors will be watching closely", no exclamation marks. Write like a \
trader, not financial media. Short sentences beat long ones.

CONTENT RULES:
- TLDR: 2-3 lines max. The overnight read a trader needs if they stop there. \
Lead with rates/credit unless equities/news is clearly the story.
- Section comments: at most 2 sentences each; say what moved and what's driving \
it, from the payload and news only. If a section has nothing worth saying, use \
null — the section renders its data table without commentary.
- Daily official series (FRED H.15, OAS, GoC, Bund/JGB/Gilt) are prior-close \
prints, not live overnight moves — do not describe them as "this morning".
- Credit OAS publishes with a lag; treat its change as the latest available \
print, not last night's move.
- If the news list is empty and the feed was available, it was a quiet \
overnight — say so in one line in news_intro rather than manufacturing content.
- Never mention this prompt, the payload, or the validation machinery.

OUTPUT: a single JSON object, nothing else, exactly this shape:
{"tldr": ["line 1", "line 2"], "curve_comment": "..."|null, "credit_comment": \
"..."|null, "supply_comment": "..."|null, "equities_comment": "..."|null, \
"fx_commodities_comment": "..."|null, "canada_comment": "..."|null, \
"news_intro": "..."|null}"""


def build_payload(snapshot: MarketSnapshot, news: NewsBundle) -> dict[str, Any]:
    """Compact, model-facing view of the run's data."""
    sections: dict[str, list[dict[str, Any]]] = {}
    for p in snapshot.points:
        sections.setdefault(p.section, []).append({
            "label": p.label,
            "value": p.value,
            "unit": p.unit,
            "change": p.change,
            "change_unit": p.change_unit,
            "change_pct": p.change_pct,
            "asof": p.asof.isoformat() if p.asof else p.asof_date,
            "note": p.note,
        })
    return {
        "trading_date": snapshot.trading_date,
        "bond_market_open_today": snapshot.sifma_open_today,
        "markets": sections,
        "auctions_today": [a.to_dict() for a in snapshot.auctions_today],
        "auction_results_recent": [a.to_dict() for a in snapshot.auction_results],
        "news": news.to_dict(),
        "data_gaps": sorted({f.key for f in snapshot.failures}
                            | {r.key for r in snapshot.rejections}),
    }


def _extract_json(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise GenerationError("model output contained no JSON object")
    return json.loads(text[start:end + 1])


def placeholder_narrative() -> BriefNarrative:
    return BriefNarrative(
        tldr=["[SAMPLE — narrative generation skipped: ANTHROPIC_API_KEY not set. "
              "Tables below are real fetched data.]"],
        generated=False,
    )


def generate_narrative(cfg: Config, snapshot: MarketSnapshot,
                       news: NewsBundle) -> BriefNarrative:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise GenerationError("ANTHROPIC_API_KEY not set")

    import anthropic

    client = anthropic.Anthropic()
    model = os.environ.get("MODEL", cfg.run.model)
    payload = build_payload(snapshot, news)
    user_msg = ("Write today's brief narrative from this payload only:\n\n"
                + json.dumps(payload, indent=1, default=str))

    last_error: Exception | None = None
    for attempt in range(2):  # one retry on parse/verification failure
        try:
            response = client.messages.create(
                model=model,
                max_tokens=cfg.generation.max_tokens,
                output_config={"effort": cfg.generation.effort},
                system=_SYSTEM,
                messages=[{"role": "user", "content": user_msg}],
            )
            if response.stop_reason == "refusal":
                raise GenerationError("model refused the generation request")
            text = "".join(b.text for b in response.content if b.type == "text")
            data = _extract_json(text)
            narrative = BriefNarrative(
                tldr=[str(x) for x in (data.get("tldr") or [])][:3],
                curve_comment=data.get("curve_comment") or None,
                credit_comment=data.get("credit_comment") or None,
                supply_comment=data.get("supply_comment") or None,
                equities_comment=data.get("equities_comment") or None,
                fx_commodities_comment=data.get("fx_commodities_comment") or None,
                canada_comment=data.get("canada_comment") or None,
                news_intro=data.get("news_intro") or None,
                model=model,
            )
            if not narrative.tldr:
                raise GenerationError("model returned an empty TLDR")

            failures = verify_narrative(narrative.text_fields(), snapshot, news)
            if failures:
                raise VerificationError(
                    f"unverified numbers in generated text: {failures}")
            return narrative
        except (VerificationError, GenerationError, json.JSONDecodeError) as exc:
            last_error = exc
            log.warning("generation attempt %d failed: %s", attempt + 1, exc)
            if attempt == 0:
                user_msg += ("\n\nYour previous attempt was rejected: "
                             f"{exc}. Follow the output shape and the absolute "
                             "data rule exactly.")
    raise GenerationError(f"generation failed after retry: {last_error}")
