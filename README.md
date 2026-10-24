# Friday Markets Note

A weekly markets recap for Canadian fixed-income readers, sent automatically
every Friday at 17:15 America/New_York. Canada rates and credit lead, the US
follows as the read-across, and everything else stays short.

Every number in the note traces to a fetched value. Nothing is estimated,
carried forward, or written from memory. If the data is bad or the text
cannot be verified, the note does not go out.

Follow this file top to bottom with no prior context and you will have the
note landing in your own inbox, then a test group, then the full list.

---

## 1. What happens each Friday

1. GitHub Actions wakes up before 17:15 ET and sleeps to the exact send time.
2. The data layer fetches 29 instruments from official and free sources and
   computes every week-over-week and one-day change itself.
3. Each value passes level, move, and staleness checks. Correlated pairs are
   cross-checked against each other. Anything that fails is dropped, logged,
   and omitted.
4. A web search pass gathers the week's material news and next week's
   calendar, every item carrying a source link.
5. Claude writes the narrative: three weekly points and a short comment per
   section. It never writes a table.
6. Every numeral in that narrative is checked against the data payload. One
   unverified number aborts the run.
7. The note renders to Outlook-safe HTML plus a plain-text part and goes out
   BCC to the configured tier.

If anything breaks, nothing is sent to the list and you get an alert.

## 2. Why 17:15 ET

US cash equities close at 16:00, the H.15 Treasury release lands about 16:15,
and Bank of Canada closing bond yields post about 16:30. Sending at 17:15
gives those same-day closes time to publish without pushing the note into the
evening.

Official daily series still sometimes lag by a day. When that happens the
note uses the latest close it actually has and the footer states that date,
rather than implying it has Friday data. The weekly window is always close to
close over one trading week, whatever that latest close turns out to be.

Change the time in `config/config.yaml` under `delivery.send_time_local`. If
you move it by more than about an hour, also move the two crons in
`.github/workflows/friday-note.yml` (see section 7).

## 3. Layout

```
config/config.yaml            ALL settings: instruments, thresholds, schedule, model
marketbrief/
  config.py                   Typed config loading
  models.py                   DataPoint / Auction / HealthReport / MarketSnapshot
  schedule.py                 DST-correct send math, NYSE and SIFMA calendars
  health.py                   Degraded-run (fail-closed) decision
  runner.py                   Orchestration: send / preview / dry-run
  alert.py                   Failure and degradation alerting
  render_table.py             Human verification table
  cli.py                      Argument wiring only, no logic
  data/
    base.py                   Retries and backoff, HTTP session, week-ago lookback
    yahoo.py                  Yahoo chart API (FX, commodities, indices, vol, BTC)
    fred.py                   FRED (UST curve, breakevens, IG and HY OAS)
    boc.py                    Bank of Canada Valet (GoC curve, CORRA, target rate)
    foreign.py                Bundesbank, Japan MoF, BoE (available, not currently used)
    treasury.py               TreasuryDirect auction calendar and results
    sanity.py                 Level, move, staleness, and correlated-pair checks
    snapshot.py               Orchestrator, the only producer of MarketSnapshot
  news/search.py              Weekly news and next-week calendar via web search
  generate/
    generator.py              Narrative generation and the house style prompt
    verify.py                 Numeric post-validation, the last wall before send
  deliver/
    recipients.py             Staged tiers: self / test / full
    email_html.py             Outlook-safe HTML and plain-text rendering
    resend_client.py          Resend API client, idempotency-keyed
tests/                        81 tests, all external calls mocked
.github/workflows/
  friday-note.yml             The scheduled Friday job
  fetch-snapshot.yml          Data-layer-only verification run
```

**Why the model cannot invent a number.** Every numeric table is rendered by
code directly from the snapshot. The model only writes prose, and that prose
is then scanned for numerals. Any numeral not present in the payload aborts
the run before anything is sent. This has already caught a real error in
development: narrative text quoting figures from a slightly earlier fetch.

## 4. Secrets and variables

**GitHub → Settings → Secrets and variables → Actions → Secrets:**

| Secret | Required | Where to get it |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | console.anthropic.com → API Keys |
| `RESEND_API_KEY` | Yes, to send | resend.com, free tier covers a weekly send comfortably |
| `OWNER_EMAIL` | Yes | Your address. Always the visible `To:` recipient. |
| `EMAIL_FROM` | Yes | e.g. `Friday Markets Note <note@yourdomain.com>` |
| `EMAIL_REPLY_TO` | Recommended | Where removal requests go. Defaults to `OWNER_EMAIL`. |
| `RECIPIENTS_TEST` | For tier `test` | Comma-separated addresses |
| `RECIPIENTS_FULL` | For tier `full` | Comma-separated addresses, the real list |
| `FRED_API_KEY` | Optional | fred.stlouisfed.org, free. Without it a public CSV fallback is used. |

**Variables** (not secrets, these are not sensitive):

| Variable | Values | Meaning |
|---|---|---|
| `RECIPIENT_TIER` | `self` / `test` / `full` | Who receives it. Unset means `self`. |
| `MODEL` | e.g. `claude-opus-5`, `claude-sonnet-5` | Overrides `config.yaml`. Sonnet is cheaper. |

Recipient lists live in secrets, so **adding or removing people never needs a
commit**.

## 5. Email domain authentication

Required before tier `test`. The code refuses to send beyond `self` from a
shared test domain, so this is not optional.

1. **Own a domain.** Any registrar.
2. **Resend → Domains → Add Domain.** Resend shows the exact records to add.
   The values below are the shape; copy the real ones from Resend, they are
   account-specific.
3. **Add these DNS records at your registrar:**

   | Type | Name | Value | Purpose |
   |---|---|---|---|
   | `MX` | `send` | `feedback-smtp.us-east-1.amazonses.com`, priority 10 | Bounce handling |
   | `TXT` | `send` | `v=spf1 include:amazonses.com ~all` | SPF, authorizes Resend to send as you |
   | `TXT` | `resend._domainkey` | the long `p=MIGf...` key from Resend | DKIM, signs your mail |
   | `TXT` | `_dmarc` | `v=DMARC1; p=none; rua=mailto:you@yourdomain.com` | DMARC, start here |

4. **Click Verify in Resend.** Usually minutes, up to 48 hours.
5. **After about two weeks of clean sending**, tighten DMARC from `p=none` to
   `p=quarantine`, then later `p=reject`. Do not start at `reject` or you will
   bounce your own mail while things settle.

Deliverability notes that matter with bank recipients: ramp volume (which the
tiers do for you), the plain-text alternative part is always generated because
HTML-only mail is penalized, and recipients sit in BCC so nobody sees the list.

## 6. Run it locally

```bash
pip install -e . && pip install pytest
python -m pytest tests/ -q                  # 81 tests, no network needed

python -m marketbrief fetch                 # data layer only, verification table
python -m marketbrief schedule              # next send instants in UTC (DST check)
python -m marketbrief run --mode dry-run    # full pipeline, writes out/, sends nothing
python -m marketbrief render --snapshot out/snapshot.json   # re-render offline
```

`dry-run` works with no API keys at all. The narrative becomes a labeled
placeholder and the tables are still real fetched data.

`.env` is gitignored. Never commit a key.

## 7. Run it on GitHub Actions

**Actions → Friday Markets Note → Run workflow**, pick a mode:

- **`preview`** sends a real note to you only, immediately. This is the
  on-demand button.
- **`dry-run`** renders and uploads artifacts, sends nothing.
- **`send`** is the scheduled path, with the send-day and window checks.

**Actions → Fetch snapshot (verification)** runs the data layer alone and
prints the verification table, for checking numbers by hand. Tick
`publish_snapshot` to also force-push the snapshot to the `data-snapshot`
branch, which lets you re-render offline with `marketbrief render`.

Every run uploads `email.html`, `email.txt`, `snapshot.json`,
`verification-table.txt`, and `run-summary.json`, retained 30 days. That is
your audit trail when someone questions a number weeks later.

**Scheduling.** Two crons fire at 20:30 and 21:30 UTC on Fridays. Whichever
lands within 75 minutes before 17:15 ET proceeds and sleeps to the exact send
instant; the other exits immediately. That covers EST and EDT with no
hardcoded offset and absorbs the usual Actions cron delay. A run more than an
hour late skips and alerts instead of sending.

**Scheduled workflows only run on the repository's default branch.** This repo
currently defaults to `claude/market-brief-agent-1xcj0h`, so the cron works as
is. If you later make another branch the default, merge this code into it or
the schedule stops firing.

**Idempotency.** The runner checks for an earlier successful scheduled run
today through the Actions API, writes a dated marker, sends with a Resend
`Idempotency-Key`, and the workflow uses a `concurrency` group. It will not
double-send.

## 8. Promoting from `self` to `full`

Deliberately manual. In this order:

1. **Stay on `self` for two or three Fridays.** Read it as a recipient would.
   Spot-check numbers against Bloomberg or the source. Confirm arrival time.
2. **Verify your domain** (section 5). Tier `test` is blocked until you do.
3. **Set `RECIPIENTS_TEST`** to two or three friendly people and set the
   `RECIPIENT_TIER` variable to `test`. Run a couple of weeks. Ask them to
   confirm it renders correctly in Outlook desktop specifically.
4. **Set `RECIPIENTS_FULL`** and flip `RECIPIENT_TIER` to `full`.

To pull back, change the variable to `self`. No commit, effective next run.

## 9. What fails closed, and how you hear about it

| Condition | Behaviour |
|---|---|
| A critical field is missing (GoC 2Y or 10Y, UST 2Y or 10Y, IG or HY OAS, USD/CAD) or more than 25% of fetches fail | Run marked DEGRADED. Tier forced to `self`, you get a `[DEGRADED]`-flagged copy, alert raised. |
| A generated numeral does not trace to the payload | Run aborts. Nothing sent. Alert. |
| Generation errors or the model refuses | Run aborts. Nothing sent. Alert. |
| Recipient config invalid: bad address, empty list, shared sending domain | Run aborts before sending. Alert. |
| More than an hour past the target | Skips the send, alerts. |
| Any uncaught failure | Workflow opens or updates a GitHub issue labeled `brief-failure`, which needs no external secrets, plus an email alert if Resend is configured. |

Sanity checks reject implausible values before the model sees them: per-class
level bounds, maximum one-day and one-week moves, staleness thresholds, and a
correlated-pair screen. Rejections are logged and omitted, never estimated.

## 10. Known source limitations

Read this before you trust a section.

- **No Canadian credit spreads.** No free Canadian IG or HY option-adjusted
  spread series exists. The US indices stand in as the read-across and the
  note says so under the credit table. Real CAD credit spreads need a paid
  feed.
- **Credit spreads lag.** ICE BofA OAS via FRED publishes a day or two behind.
  Timestamped honestly, with a wider staleness window.
- **Rates are official closes, not intraday.** GoC comes from Bank of Canada
  benchmarks and UST from H.15. Both are once-a-day prints.
- **No free source exists** for: Canadian or US new-issue calendars and
  pricing, auction tails (which need when-issued quotes), Canadian auction
  results in structured form, TSX futures, or VIX futures. These are omitted
  rather than half-built. The news pass catches large deals and their
  financing; a proper DCM table needs IGM or Bloomberg.
- **Calendar consensus** comes from web search with source links, not a
  structured feed. Times appear when a source states them; consensus is
  omitted when it cannot be verified.
- **Yahoo Finance is unofficial** and can break or throttle. It sits behind
  `data/yahoo.py` so a paid feed can replace it without touching anything else.

## 11. Two things worth knowing

**Keep the archive.** Run artifacts hold the exact note and the exact snapshot
it came from. When a reader questions a number weeks later, that is your
answer.

**CASL.** You are sending commercial email from Canada. Keep a record of how
each recipient consented, keep the footer's identification and removal path
intact, and honour removals promptly. The footer also states this is a
personal publication and not investment advice. Edit `email.sender_name`,
`email.byline`, and `email.sender_descriptor` in `config/config.yaml` to change
how you are identified.
