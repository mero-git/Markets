# Blackleaf Capital — Daily Market Brief

Automated 6:30am ET weekday market note. Fixed income first; every number
traceable to a fetched value; fail-closed by design.

**Status: Phase 1 (data layer) complete.** Generation, delivery, and
scheduling land next — see the build plan at the bottom.

## Layout

```
config/config.yaml          All settings: tickers, thresholds, schedule, model
marketbrief/
  config.py                 Typed config loading
  models.py                 DataPoint / Auction / HealthReport / MarketSnapshot
  schedule.py               DST-correct send-time math, holiday calendars
  health.py                 Degraded-run (fail-closed) decision
  render_table.py           Human verification table
  data/
    base.py                 Retries/backoff, HTTP session, RawQuote
    yahoo.py                Yahoo chart API (futures, indices, FX, vol, crypto)
    fred.py                 FRED (UST curve, TIPS, breakevens, IG/HY OAS)
    boc.py                  Bank of Canada Valet (GoC yields)
    foreign.py              Bundesbank (Bund), Japan MoF (JGB), BoE (Gilt)
    treasury.py             TreasuryDirect auction calendar + results
    sanity.py               Level/move plausibility + staleness checks
    snapshot.py             Orchestrator — the only producer of MarketSnapshot
tests/                      Sanity, DST/holiday, health-gate, parser tests
```

## Data guarantees

- Every value carries `source`, `fetched_at`, and an as-of timestamp or
  observation date. Daily official series (FRED, BoC, MoF, Bundesbank, BoE)
  are labeled prior-close and never presented as live.
- Sanity checks reject implausible levels and moves (per-class bounds in
  `config.yaml`); staleness thresholds fail old "live" quotes. Rejects are
  logged and **omitted** — never estimated or carried forward.
- Each fetch is wrapped in retries with backoff; one bad ticker cannot take
  down the run.
- Yield-curve changes are quoted in bp; credit OAS is converted to bp; all
  spreads (2s10s, 5s30s, Canada–US 10Y) are computed in the data layer so
  the generation model never does arithmetic.
- Health gate: run is DEGRADED if any critical field (UST 2Y/10Y, 10Y yield
  future, ES, IG/HY OAS) is missing or >25% of fetches fail. Degraded runs
  will never send beyond `self` once delivery lands.

## Run locally

```bash
pip install -e . && pip install pytest
python -m pytest tests/ -q          # no network needed (all mocked)
python -m marketbrief fetch --json snapshot.json   # real fetch + table
python -m marketbrief schedule      # next send instants in UTC (DST check)
```

## Run on GitHub Actions

Actions → **Fetch snapshot (verification)** → Run workflow. The job log
shows the verification table; the full JSON uploads as an artifact.

## Keys

| Secret | Needed for | Where to get it |
|---|---|---|
| `FRED_API_KEY` | Optional now (public CSV fallback exists); recommended for production stability | free at https://fred.stlouisfed.org/docs/api/api_key.html |

(Anthropic + email provider keys arrive with phases 2–3.)

## Known source limitations (free tier — read before trusting)

- **UST intraday moves** come from CBOT micro yield futures (2YY/5YY/10Y/30Y),
  a cash-market proxy; official H.15 closes are fetched separately and both
  are labeled. **Bund/JGB/Gilt/GoC** are official prior-close prints only.
- **IG/HY OAS** (ICE BofA via FRED) publish with a 1–2 day lag — timestamped
  honestly, wider staleness window.
- **No free source** for: IG/HY new-issue calendar and pricing, auction
  tails (needs 1pm when-issued quotes), TSX (SXF) futures, VIX futures,
  Canadian credit OAS. These are omitted rather than half-built.
- Yahoo Finance is unofficial and can break or throttle; it sits behind
  `data/yahoo.py` so a paid feed can replace it without touching anything else.

## Build plan

1. ✅ Data layer (this phase) — verify numbers by hand before going further
2. Generation: Claude writes the note from the snapshot; numeric
   post-validation fails the run if any number lacks provenance
3. Delivery: Resend + domain auth (SPF/DKIM/DMARC), Outlook-safe HTML,
   BCC list from secrets, `RECIPIENT_TIER` staged rollout
4. Scheduling: GitHub Actions cron firing early + in-process sleep to
   6:30 ET, idempotency marker, alerting on degraded/failed runs
