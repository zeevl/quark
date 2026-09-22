Weekly cross-account finance memo. Pipeline: SimpleFIN Bridge → SQLite → Claude merchant
categorization (cached per merchant, never re-asked) → deterministic anomaly rules → Claude-written
memo → email.

## Layout

- `finsum/simplefin.py` — bridge client (claim token, fetch /accounts, chunked backfill)
- `finsum/db.py` — schema, idempotent upserts, `merchant_key()` normalization
- `finsum/categorize.py` — batch classification of new merchant keys into `config.CATEGORIES`
- `finsum/rules.py` — `link_transfers()` and `analyze()`; all flags are produced here, no LLM
- `finsum/report.py` — memo prompt, HTML render, SMTP
- `finsum/cli.py` — commands; `finsum run` is the cron entry point
- `accounts.yaml` — the ONE piece of human config: account id → entity. Auto-stubbed on first sync.

## Principles

- Zero ongoing maintenance for the user. Never add a step that asks a human to confirm a category.
- Rules are plain arithmetic in `rules.py`; the LLM only names merchants and writes prose.
- Syncs must stay idempotent (PK on transaction id + account). Never delete history.
- Bank data never leaves the machine except: descriptors → Anthropic API for categorization,
  and the aggregated summary → Anthropic API for the memo. No account numbers are sent.

## Common tasks

- New flag type: add a block in `rules.analyze()` appending to `flags` with `type/severity/entity/text`,
  then mention it in `report.MEMO_SYSTEM` if it needs its own treatment.
- Tune thresholds: `thresholds:` in `accounts.yaml` (see `config.Thresholds`).
- Bad merchant normalization: fix `db.merchant_key()`; then `DELETE FROM merchants` for affected keys
  and rerun `finsum categorize`. Keys are recomputed only on insert, so also
  `UPDATE transactions SET merchant_key=...` or re-run a backfill.
- Test without hitting banks: `sqlite3 finsum.db` and craft rows, then `finsum report`.
