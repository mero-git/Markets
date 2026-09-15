# Blackleaf Capital — Daily Market Brief

Automated 6:30am ET weekday market note. Fixed income first, every number
traceable to a fetched value, fail-closed by design.

Follow this file top to bottom with zero prior context and you will have a
working daily brief landing in your own inbox, then your test group, then
the full list.

---

## 1. What it does

Every weekday (skipping US market holidays), GitHub Actions wakes up before
6:30am ET, fetches ~40 instruments from official and free sources, searches
for material overnight news, has Claude write the narrative, **machine-checks
every numeral in that narrative against the fetched data**, renders an
Outlook-safe HTML email, and sends it BCC to your list at 6:30am ET exactly.

If anything is wrong — data degraded, generation failed, a number that
doesn't trace to the payload, more than 25 minutes late — it does **not**
send to the list. It alerts you instead. A missing email is recoverable; a
wrong one to a trading MD is not.

---

## 2. Layout

```
config/config.yaml           ALL settings: tickers, thresholds, schedule, model, send time
marketbrief/
  config.py                  Typed config loading
  models.py                  DataPoint / Auction / HealthReport / MarketSnapshot
  schedule.py                DST-correct send math, NYSE/SIFMA holiday calendars
  health.py                  Degraded-run (fail-closed) decision
  runner.py                  Run orchestration: send / preview / dry-run modes
  alert.py                   Failure + degradation alerting
  render_table.py            Human verification table
  cli.py                     Argument wiring only — no logic
  data/
    base.py                  Retries/backoff, HTTP session, RawQuote
    yahoo.py  fred.py  boc.py  foreign.py  treasury.py     Source clients
    sanity.py                Level/move plausibility + staleness checks
    snapshot.py              Orchestrator — the only producer of MarketSnapshot
  news/search.py             Material news + econ calendar via Claude web search
  generate/
    generator.py             Narrative generation (model writes prose, never tables)
    verify.py                Numeric post-validation — the last wall before send
  deliver/
    recipients.py            Staged tiers: self / test / full
    email_html.py            Outlook-safe HTML + plain-text alternative
    resend_client.py         Resend API client (idempotency-keyed)
tests/                       71 tests, all external calls mocked
.github/workflows/
  daily-brief.yml            The scheduled job
  fetch-snapshot.yml         Data-layer-only verification run
```

**Why the model can't invent a number:** all numeric tables are rendered by
code directly from the snapshot. The model only writes narrative prose, and
that prose is then scanned for numerals — any numeral not present in the
data payload aborts the run before sending.

---

## 3. Keys and secrets

Set these in **GitHub → Settings → Secrets and variables → Actions → Secrets**:

| Secret | Required | Where to get it |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | https://console.anthropic.com → API Keys |
| `RESEND_API_KEY` | Yes (to send) | https://resend.com → API Keys (free tier: 3,000/mo, 100/day) |
| `OWNER_EMAIL` | Yes | Your own address. Always the visible `To:` recipient. |
| `EMAIL_FROM` | Yes | e.g. `Blackleaf Capital <brief@yourdomain.com>` — must be on your verified domain |
| `EMAIL_REPLY_TO` | Recommended | Where unsubscribe replies go (defaults to `OWNER_EMAIL`) |
| `RECIPIENTS_TEST` | For tier `test` | Comma-separated addresses |
| `RECIPIENTS_FULL` | For tier `full` | Comma-separated addresses — the real list |
| `FRED_API_KEY` | Optional | https://fred.stlouisfed.org/docs/api/api_key.html — free; without it a public CSV fallback is used |

And in **Variables** (not secrets — these aren't sensitive):

| Variable | Values | Meaning |
|---|---|---|
| `RECIPIENT_TIER` | `self` / `test` / `full` | Who gets it. Leave unset = `self`. |
| `MODEL` | e.g. `claude-opus-5`, `claude-sonnet-5` | Overrides `config.yaml`. Sonnet is cheaper, Opus is sharper. |

Recipient lists live in secrets, so **adding or removing people never needs a commit**.

---

## 4. Email domain authentication (do this before tier `test`)

The code physically refuses to send beyond `self` from a shared test domain
(`resend.dev`). You must authenticate a domain you own.

1. **Buy/own a domain** (e.g. `blackleafcapital.com`). Any registrar.
2. **Resend → Domains → Add Domain.** Enter your domain. Resend shows you
   the exact records to add — the values below are the *shape*; copy the
   real values from Resend's screen, they are account-specific.
3. **Add these DNS records at your registrar** (Namecheap/Cloudflare/GoDaddy →
   DNS management):

   | Type | Name/Host | Value | Notes |
   |---|---|---|---|
   | `MX` | `send` | `feedback-smtp.us-east-1.amazonses.com` (priority 10) | Bounce handling |
   | `TXT` | `send` | `v=spf1 include:amazonses.com ~all` | **SPF** — authorizes Resend to send as you |
   | `TXT` | `resend._domainkey` | `p=MIGfMA0GCSq...` (long key from Resend) | **DKIM** — cryptographically signs your mail |
   | `TXT` | `_dmarc` | `v=DMARC1; p=none; rua=mailto:you@yourdomain.com` | **DMARC** — start here, tighten later |

4. **Click Verify in Resend.** DNS propagation is usually minutes, up to 48h.
5. **After ~2 weeks of clean sending**, tighten DMARC from `p=none` to
   `p=quarantine`, and later `p=reject`. Don't start at `reject` — you'll
   bounce your own mail while things settle.

**Why Resend:** a real free tier that covers a daily send (Postmark's
100/month can't), first-class domain auth, simple API. SES is cheaper at
scale but starts sandboxed and is slower to set up.

**Deliverability notes that matter with bank recipients:** send volume
should ramp (self → small test → full, which is what the tiers do), the
plain-text alternative part is always generated (spam filters penalize
HTML-only), and recipients are in BCC so nobody sees the list.

---

## 5. Run it locally

```bash
pip install -e . && pip install pytest
python -m pytest tests/ -q                   # 71 tests, no network needed

python -m marketbrief fetch                  # data layer only, verification table
python -m marketbrief schedule               # next send instants in UTC (DST check)

# Full pipeline, renders out/email.html, SENDS NOTHING:
python -m marketbrief run --mode dry-run

# Re-render the email offline from a saved snapshot (no network, no keys):
python -m marketbrief render --snapshot out/snapshot.json
```

`dry-run` works with no API keys at all — the narrative becomes a labeled
placeholder and the tables are still real fetched data.

Local env vars go in a `.env`-style export or your shell; `.env` is
gitignored. Never commit a key.

---

## 6. Run it on GitHub Actions

**Actions → Daily brief → Run workflow**, pick a mode:

- **`preview`** — full pipeline, emails **you only**, immediately. Use this
  to check a real brief on demand.
- **`dry-run`** — renders and uploads artifacts, sends nothing.
- **`send`** — the scheduled path (window checks + sleep to 6:30 ET). Only
  useful manually if you're inside the window.

Every run uploads `email.html`, `email.txt`, `snapshot.json`,
`verification-table.txt`, and `run-summary.json` as artifacts, retained 30
days — that's your audit trail when someone questions a number three weeks
later.

**Scheduling:** two crons fire at 09:45 and 10:45 UTC Mon–Fri. Whichever
lands 0–75 minutes before 6:30am ET proceeds and sleeps to the exact send
instant; the other exits immediately. This covers EST and EDT with no
hardcoded offset and absorbs the ~10-20 min GitHub Actions cron delay. If a
run somehow starts more than 25 minutes past 6:30, it **skips and alerts**
rather than sending a stale note.

**Idempotency:** the runner checks for an earlier successful scheduled run
today via the Actions API, writes a dated marker, sends with a Resend
`Idempotency-Key`, and the workflow uses a `concurrency` group. It will not
double-send.

---

## 7. Promoting from `self` to `full`

Deliberately manual. Do it in this order:

1. **Stay on `self` for a week.** Read it every morning like a recipient.
   Check numbers against Bloomberg/Yahoo. Confirm arrival time.
2. **Verify your domain** (section 4). The code blocks tier `test` until
   you're off the shared domain.
3. **Set `RECIPIENTS_TEST`** to 2-3 friendly people, set the
   `RECIPIENT_TIER` variable to `test`. Run a week. Ask them if it renders
   correctly in Outlook desktop specifically.
4. **Set `RECIPIENTS_FULL`**, flip `RECIPIENT_TIER` to `full`.

To pull back at any time, change the variable to `self`. No commit needed,
takes effect on the next run.

---

## 8. What fails closed, and how you find out

| Condition | Behavior |
|---|---|
| Critical field missing (UST 2Y/10Y, 10Y fut, ES, IG/HY OAS) or >25% of fetches fail | Run marked DEGRADED: tier forced to `self`, you get a `[DEGRADED]`-flagged copy plus an alert |
| A generated numeral doesn't trace to the payload | Run aborts. **Nothing is sent.** Alert raised. |
| Generation errors or the model refuses | Run aborts. Nothing sent. Alert. |
| Recipient config invalid (bad address, empty list, shared domain) | Run aborts before sending. Alert. |
| More than 25 min past 6:30 ET | Skips the send, alerts |
| Any uncaught failure | Workflow opens/updates a GitHub issue labeled `brief-failure` (needs no external secrets), plus an email alert if Resend is configured |

Sanity checks reject implausible values before they ever reach the model:
per-class level bounds and max one-day moves (a 40% index move, a negative
VIX, a 500bp overnight 10Y move), plus staleness thresholds. Rejections are
**logged and omitted** — never estimated, never carried forward.

---

## 9. Known source limitations (read before trusting)

Free sources, honestly labeled:

- **UST intraday moves** come from CBOT micro yield futures; official H.15
  closes are fetched separately and both are labeled. In practice the
  2Y/5Y/30Y micros trade thinly and their Yahoo prints are often days old —
  the staleness check then drops them, so a reliable *live* overnight bp
  move is frequently available only for the 10Y. Live 2s10s/5s30s changes
  appear only when both legs are fresh; otherwise the note uses prior-close
  spreads. A paid rates feed (CME/ICE) is the real fix.
- **Bund/JGB/Gilt/GoC** are official prior-close prints only, never live.
- **IG/HY OAS** (ICE BofA via FRED) publish with a 1–2 day lag — timestamped
  honestly, wider staleness window.
- **No free source exists** for: IG/HY new-issue calendar and pricing
  (needs IGM/Informa or Bloomberg), auction tails (needs 1pm when-issued
  quotes), TSX/SXF futures, VIX futures, Canadian credit OAS. These are
  **omitted rather than half-built**. The news pass catches jumbo deals and
  their financing; a proper DCM new-issue table needs a paid feed.
- **Econ calendar consensus** comes from web search with source links, not a
  structured feed. Times are reported when a source states them; consensus
  is omitted when it can't be verified.
- **Yahoo Finance is unofficial** and can break or throttle. It sits behind
  `data/yahoo.py` so a paid feed can replace it without touching anything
  else.

---

## 10. Two things worth knowing

**Keep the archive.** Run artifacts hold the exact email and the exact
snapshot it came from. When an MD questions a number weeks later, that's
your answer.

**CASL/CAN-SPAM.** You're sending commercial email from Canada. Keep a
record of how each recipient consented, keep the footer's identification
and unsubscribe path intact, and honour removals promptly. The footer also
states this is a personal student publication and not investment advice —
"Blackleaf Capital" reads like a registered firm to a bank MD, so that line
matters. Edit `email.sender_name` / `email.sender_affiliation` in
`config/config.yaml` to match how you want to be identified.
