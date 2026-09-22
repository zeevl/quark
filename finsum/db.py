"""SQLite persistence. One file, append-only history, idempotent syncs."""
from __future__ import annotations

import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    org TEXT, name TEXT, currency TEXT,
    first_seen INTEGER, last_seen INTEGER
);
CREATE TABLE IF NOT EXISTS balances (
    account_id TEXT, as_of INTEGER, balance REAL, available REAL,
    PRIMARY KEY (account_id, as_of)
);
CREATE TABLE IF NOT EXISTS transactions (
    id TEXT, account_id TEXT, posted INTEGER, amount REAL,
    description TEXT, payee TEXT, memo TEXT, pending INTEGER DEFAULT 0,
    merchant_key TEXT, transfer_group TEXT,
    first_seen INTEGER,
    PRIMARY KEY (id, account_id)
);
CREATE INDEX IF NOT EXISTS idx_txn_posted ON transactions(posted);
CREATE INDEX IF NOT EXISTS idx_txn_merchant ON transactions(merchant_key);
CREATE TABLE IF NOT EXISTS merchants (
    key TEXT PRIMARY KEY,
    merchant TEXT, category TEXT, recurring INTEGER DEFAULT 0,
    sample_description TEXT, classified_at INTEGER, model TEXT
);
CREATE TABLE IF NOT EXISTS sync_log (
    ran_at INTEGER, ok INTEGER, new_txns INTEGER, errors TEXT
);
CREATE TABLE IF NOT EXISTS reports (
    week_ending TEXT PRIMARY KEY, generated_at INTEGER, markdown TEXT, summary_json TEXT
);
"""

_PREFIXES = re.compile(
    r"^(SQ|TST|SP|PAYPAL|PP|CHECKCARD|POS|PURCHASE|DEBIT|ACH|RECURRING PAYMENT|AUTOPAY|WEB|ONLINE|"
    r"CARD PURCHASE|VISA|MASTERCARD|DDA)\s*[\*:#-]*\s*", re.I)
_STATES = {"AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS","KY","LA","ME","MD",
           "MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC",
           "SD","TN","TX","UT","VT","VA","WA","WV","WI","WY","DC"}


def merchant_key(description: str, payee: str | None = None) -> str:
    """Stable, human-blind key for a merchant. Same merchant -> same key across
    dates, card suffixes, store numbers, auth codes and cities. Classified once, cached forever."""
    s = (payee or description or "").upper()
    s = _PREFIXES.sub("", s)
    s = re.sub(r"[^A-Z0-9&\. ]", " ", s)
    tokens = [t for t in s.split() if not re.search(r"\d", t) and t.strip(".&")]
    # drop trailing "CITY ST" (state abbreviation plus the word before it)
    if len(tokens) >= 2 and tokens[-1] in _STATES:
        tokens = tokens[:-2]
    elif len(tokens) >= 1 and tokens[-1] in _STATES and len(tokens) > 1:
        tokens = tokens[:-1]
    # drop URL-ish duplicates of the brand ("WWW.ZOOM.US")
    tokens = [t for t in tokens if not t.startswith("WWW.")]
    return " ".join(tokens[:4]) or "UNKNOWN"


@contextmanager
def connect(path: Path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_accounts(conn, accounts: list[dict], now: int) -> None:
    for a in accounts:
        org = (a.get("org") or {}).get("name") or (a.get("org") or {}).get("domain") or ""
        conn.execute(
            """INSERT INTO accounts(id, org, name, currency, first_seen, last_seen)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET org=excluded.org, name=excluded.name,
                    currency=excluded.currency, last_seen=excluded.last_seen""",
            (a["id"], org, a.get("name", ""), a.get("currency", "USD"), now, now))
        if a.get("balance-date"):
            conn.execute(
                "INSERT OR IGNORE INTO balances(account_id, as_of, balance, available) VALUES (?,?,?,?)",
                (a["id"], int(a["balance-date"]), float(a.get("balance") or 0),
                 float(a.get("available-balance") or a.get("balance") or 0)))


def upsert_transactions(conn, accounts: list[dict], now: int) -> int:
    new = 0
    for a in accounts:
        for t in a.get("transactions", []):
            posted = int(t.get("posted") or t.get("transacted_at") or 0)
            key = merchant_key(t.get("description", ""), t.get("payee"))
            cur = conn.execute(
                """INSERT INTO transactions(id, account_id, posted, amount, description, payee, memo,
                        pending, merchant_key, first_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id, account_id) DO UPDATE SET
                        posted=CASE WHEN excluded.posted>0 THEN excluded.posted ELSE posted END,
                        pending=excluded.pending, amount=excluded.amount""",
                (str(t["id"]), a["id"], posted, float(t["amount"]), t.get("description", ""),
                 t.get("payee"), t.get("memo"), 1 if t.get("pending") else 0, key, now))
            if cur.rowcount and conn.execute(
                    "SELECT first_seen FROM transactions WHERE id=? AND account_id=?",
                    (str(t["id"]), a["id"])).fetchone()[0] == now:
                new += 1
    return new


def unclassified_merchants(conn) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT t.merchant_key AS key, MIN(t.description) AS description, MIN(t.payee) AS payee,
                  COUNT(*) AS n, AVG(t.amount) AS avg_amount
           FROM transactions t LEFT JOIN merchants m ON m.key = t.merchant_key
           WHERE m.key IS NULL GROUP BY t.merchant_key ORDER BY n DESC""").fetchall()


def save_merchants(conn, rows: list[dict], model: str) -> None:
    now = int(time.time())
    for r in rows:
        conn.execute(
            """INSERT INTO merchants(key, merchant, category, recurring, sample_description, classified_at, model)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(key) DO UPDATE SET merchant=excluded.merchant, category=excluded.category,
                    recurring=excluded.recurring, classified_at=excluded.classified_at, model=excluded.model""",
            (r["key"], r["merchant"], r["category"], 1 if r.get("recurring") else 0,
             r.get("sample_description", ""), now, model))


def log_sync(conn, ok: bool, new_txns: int, errors: list[str]) -> None:
    conn.execute("INSERT INTO sync_log VALUES (?,?,?,?)",
                 (int(time.time()), 1 if ok else 0, new_txns, "; ".join(errors)))
