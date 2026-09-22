# finsum

A Monday-morning memo covering every bank and card you own — business and personal — with balances,
week-over-week movement, spend by category, and anything unusual. No budgeting app, no manual
categorization, no maintenance beyond re-logging into a bank when it breaks its own login.

```
SimpleFIN Bridge ──▶ SQLite ──▶ Claude names each new merchant once ──▶ rules flag oddities ──▶ Claude writes memo ──▶ email
```

## Setup (one afternoon, mostly bank logins)

1. **Connect your banks at [bridge.simplefin.org](https://bridge.simplefin.org)** ($15/yr, read-only,
   up to 25 institutions). Add every checking, savings, credit card and loan you want covered.
   Then create a *Setup Token* under the app section.

2. **Install and claim:**
   ```bash
   git clone <this repo> && cd finsum
   python -m venv .venv && source .venv/bin/activate
   pip install -e .
   cp .env.example .env
   finsum claim <paste-setup-token>       # prints your Access URL — put it in .env
   ```
   Add your `ANTHROPIC_API_KEY` to `.env` as well.

3. **Backfill history to build the baseline:**
   ```bash
   finsum backfill --days 365
   ```
   This pulls a year in 60-day chunks, categorizes every merchant it has never seen (a few hundred
   Haiku calls, well under a dollar), and writes `accounts.yaml` listing every account it found.

4. **Assign entities — the only config you'll ever do.** Open `accounts.yaml` and set `entity:` on each
   account (`scribeware`, `giving_group`, `realchemy`, `personal`, whatever you like) and `kind:`
   (`checking | savings | credit | loan`). Credit/loan balances count as liabilities in net position.

5. **Generate a report:**
   ```bash
   finsum report            # prints memo, writes out/<date>.md and .html
   finsum report --email    # after filling SMTP_* and MAIL_TO in .env
   ```

## Schedule it

**On a box you own (e.g. the mini PC):**
```cron
0 6 * * 1  cd /opt/finsum && .venv/bin/finsum run --email --quiet >> finsum.log 2>&1
```

**On GitHub Actions:** `.github/workflows/weekly.yml` is included. Add the five secrets from
`.env.example` to the repo, then run it once manually via *workflow_dispatch* to seed the cache
with your `finsum.db` and `accounts.yaml` (or commit them to a private repo — your choice; the DB
contains transaction descriptions but no account numbers).

## What gets flagged

| Flag | What it means |
|---|---|
| `large_txn` | Single charge over the entity's threshold (default $1,000; $500 personal) |
| `vendor_spike` | A vendor's weekly total is >2× its trailing 12-week median |
| `new_merchant` | First time this vendor has ever appeared |
| `possible_duplicate` | Same account, vendor, amount within 3 days |
| `recurring_changed` | A monthly bill's amount moved ≥5% |
| `recurring_missing` | A monthly bill that hasn't shown up when expected |
| `entity_mismatch` | Personal-looking spend on a business account, or vice versa |
| `stale_account` | An account hasn't synced in 3+ days — usually needs a bank re-login at the Bridge |
| `unassigned_account` | A new account appeared and has no entity yet |

Transfers between your own accounts are paired automatically (equal and opposite amounts within
4 days across different accounts) and excluded from spend and income.

Thresholds live under `thresholds:` in `accounts.yaml`; see `finsum/config.py::Thresholds`.

## Design notes

- **Categorization is cached per merchant, forever.** Bank descriptors are noisy but stable per
  merchant; `db.merchant_key()` strips store numbers, dates, auth codes and city/state so
  `SQ *BLUE BOTTLE 0412 SEATTLE WA` and `SQ *BLUE BOTTLE 0088 AUSTIN TX` share a key. Each key is
  classified once by Claude. You never confirm anything.
- **Rules are deterministic.** Everything in "Act on this" comes from arithmetic in `rules.py`. The
  model only names merchants and writes prose, so it can't hallucinate a problem into existence.
- **Only credential held:** the SimpleFIN access URL, which is read-only and revocable from the
  Bridge dashboard.

## Suggested first month

Run it, read three or four memos, and notice what you skim past versus what you actually act on.
Then tighten thresholds and delete flag types you don't care about. The rules that survive are your
definition of "unusual".
