"""Deterministic analysis. Produces a JSON-able summary + list of flags for the memo writer.

Everything here is plain arithmetic over SQLite so it is cheap, repeatable and auditable.
"""
from __future__ import annotations

import statistics
import time
from collections import defaultdict
from datetime import datetime, timedelta

from .config import BUSINESS_LEANING, NON_SPEND, PERSONAL_ENTITY, PERSONAL_LEANING, Config

DAY = 86400


def _ts(d: datetime) -> int:
    return int(d.timestamp())


def _fmt_day(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%a %b %d") if ts else "pending"


def _txns(conn, start: int, end: int):
    return [dict(r) for r in conn.execute(
        """SELECT t.*, m.merchant, m.category, m.recurring, a.name AS account_name, a.org
           FROM transactions t
           LEFT JOIN merchants m ON m.key = t.merchant_key
           JOIN accounts a ON a.id = t.account_id
           WHERE t.posted >= ? AND t.posted < ?
           ORDER BY t.posted""", (start, end)).fetchall()]


# ---------------------------------------------------------------- transfers

def link_transfers(conn, cfg: Config, since: int) -> int:
    """Pair opposite-sign transactions of equal amount across different accounts within a few
    days and tag them with a shared transfer_group so they don't count as spend/income."""
    window = cfg.thresholds.transfer_match_window_days * DAY
    rows = [dict(r) for r in conn.execute(
        "SELECT id, account_id, posted, amount FROM transactions WHERE posted >= ? AND transfer_group IS NULL",
        (since - window,)).fetchall()]
    by_amount: dict[float, list] = defaultdict(list)
    for r in rows:
        by_amount[round(abs(r["amount"]), 2)].append(r)
    linked = 0
    for amt, group in by_amount.items():
        if amt < 1 or len(group) < 2:
            continue
        outs = sorted([r for r in group if r["amount"] < 0], key=lambda r: r["posted"])
        ins = sorted([r for r in group if r["amount"] > 0], key=lambda r: r["posted"])
        used = set()
        for o in outs:
            for i in ins:
                if i["id"] in used or i["account_id"] == o["account_id"]:
                    continue
                if abs(i["posted"] - o["posted"]) <= window:
                    gid = f"{o['account_id']}:{o['id']}"
                    for r in (o, i):
                        conn.execute("UPDATE transactions SET transfer_group=? WHERE id=? AND account_id=?",
                                     (gid, r["id"], r["account_id"]))
                    used.add(i["id"])
                    linked += 1
                    break
    return linked


# ---------------------------------------------------------------- analysis

def analyze(conn, cfg: Config, as_of: datetime | None = None) -> dict:
    as_of = as_of or datetime.now()
    end = _ts(as_of.replace(hour=23, minute=59, second=59))
    start = end - 7 * DAY
    th = cfg.thresholds
    now = int(time.time())

    flags: list[dict] = []
    active_accounts = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM accounts")}
    for aid, ac in cfg.accounts.items():
        if ac.ignore:
            active_accounts.pop(aid, None)

    # ---- balances per account / entity, with 7d and 30d deltas
    def balance_at(aid: str, ts: int):
        r = conn.execute("SELECT balance FROM balances WHERE account_id=? AND as_of<=? ORDER BY as_of DESC LIMIT 1",
                         (aid, ts)).fetchone()
        return r["balance"] if r else None

    entities: dict[str, dict] = defaultdict(lambda: {"accounts": [], "net_worth": 0.0,
                                                     "net_7d": 0.0, "net_30d": 0.0,
                                                     "spend": 0.0, "income": 0.0,
                                                     "by_category": defaultdict(float)})
    for aid, a in active_accounts.items():
        ent = cfg.entity_for(aid)
        latest = conn.execute("SELECT balance, as_of FROM balances WHERE account_id=? ORDER BY as_of DESC LIMIT 1",
                              (aid,)).fetchone()
        bal = latest["balance"] if latest else None
        bal_ts = latest["as_of"] if latest else 0
        b7, b30 = balance_at(aid, end - 7 * DAY), balance_at(aid, end - 30 * DAY)
        kind = cfg.accounts.get(aid).kind if aid in cfg.accounts else "unknown"
        rec = {"id": aid, "label": cfg.label_for(aid, f"{a['org']} {a['name']}".strip()),
               "kind": kind, "balance": bal, "as_of": _fmt_day(bal_ts),
               "delta_7d": None if (bal is None or b7 is None) else round(bal - b7, 2),
               "delta_30d": None if (bal is None or b30 is None) else round(bal - b30, 2)}
        entities[ent]["accounts"].append(rec)
        if bal is not None:
            # credit cards report positive balance = money owed on most bridges; treat as liability
            sign = -1 if kind in ("credit", "loan") else 1
            entities[ent]["net_worth"] += sign * bal
            if rec["delta_7d"] is not None:
                entities[ent]["net_7d"] += sign * rec["delta_7d"]
            if rec["delta_30d"] is not None:
                entities[ent]["net_30d"] += sign * rec["delta_30d"]
        if bal_ts and (now - bal_ts) > th.stale_account_days * DAY:
            flags.append({"type": "stale_account", "severity": "warn", "entity": ent,
                          "text": f"{rec['label']} has not synced since {_fmt_day(bal_ts)} — likely needs re-authentication at SimpleFIN Bridge."})
        if ent == "unassigned":
            flags.append({"type": "unassigned_account", "severity": "info", "entity": ent,
                          "text": f"{rec['label']} has no entity in accounts.yaml (id: {aid}). Set it once and this goes away."})

    # ---- this week's transactions
    week = _txns(conn, start, end)
    spend_txns = [t for t in week if t["amount"] < 0 and not t["transfer_group"]
                  and (t["category"] or "other") not in NON_SPEND]
    for t in week:
        ent = cfg.entity_for(t["account_id"])
        cat = t["category"] or "uncategorized"
        if t["transfer_group"] or cat in ("transfer", "credit_card_payment"):
            continue
        if t["amount"] < 0:
            entities[ent]["spend"] += -t["amount"]
            entities[ent]["by_category"][cat] += -t["amount"]
        elif cat in ("income_revenue", "refund"):
            entities[ent]["income"] += t["amount"]

    # ---- large single transactions
    for t in spend_txns:
        ent = cfg.entity_for(t["account_id"])
        if -t["amount"] < th.large_txn(ent):
            continue
        if t["recurring"]:
            # a bill we've paid before at roughly this amount is not news
            prior = conn.execute(
                """SELECT 1 FROM transactions WHERE merchant_key=? AND account_id=? AND posted < ?
                   AND ABS(ABS(amount) - ?) <= 0.05 * ? LIMIT 1""",
                (t["merchant_key"], t["account_id"], start, -t["amount"], -t["amount"])).fetchone()
            if prior:
                continue
        if True:
            flags.append({"type": "large_txn", "severity": "warn", "entity": ent,
                          "text": f"${-t['amount']:,.2f} to {t['merchant'] or t['description']} on {_fmt_day(t['posted'])} ({t['account_name']})."})

    # ---- new merchants (first ever appearance this week)
    first_seen = {r["merchant_key"]: r["m"] for r in conn.execute(
        "SELECT merchant_key, MIN(posted) AS m FROM transactions GROUP BY merchant_key")}
    seen_new = set()
    for t in spend_txns:
        k = t["merchant_key"]
        if first_seen.get(k, 0) >= start and k not in seen_new and -t["amount"] >= 20:
            seen_new.add(k)
            flags.append({"type": "new_merchant", "severity": "info", "entity": cfg.entity_for(t["account_id"]),
                          "text": f"New vendor: {t['merchant'] or t['description']} — ${-t['amount']:,.2f} ({t['category'] or 'uncategorized'}, {t['account_name']})."})

    # ---- vendor weekly spend vs trailing median
    this_week_by_vendor: dict[str, float] = defaultdict(float)
    for t in spend_txns:
        this_week_by_vendor[t["merchant_key"]] += -t["amount"]
    for k, amt in this_week_by_vendor.items():
        if amt < th.vendor_spike_min or k in seen_new:
            continue
        weeks = defaultdict(float)
        for r in conn.execute(
                """SELECT posted, amount FROM transactions WHERE merchant_key=? AND amount<0
                   AND posted >= ? AND posted < ? AND transfer_group IS NULL""",
                (k, start - th.vendor_baseline_weeks * 7 * DAY, start)):
            weeks[(start - r["posted"]) // (7 * DAY)] += -r["amount"]
        hist = [weeks.get(i, 0.0) for i in range(th.vendor_baseline_weeks)]
        med = statistics.median(hist)
        if med > 0 and amt > th.vendor_spike_multiple * med:
            name = conn.execute("SELECT merchant FROM merchants WHERE key=?", (k,)).fetchone()
            flags.append({"type": "vendor_spike", "severity": "warn",
                          "entity": cfg.entity_for(next(t["account_id"] for t in spend_txns if t["merchant_key"] == k)),
                          "text": f"{(name and name['merchant']) or k}: ${amt:,.2f} this week vs ${med:,.2f} typical weekly ({amt / med:.1f}x)."})

    # ---- duplicates: same account, same merchant, same amount, within N days
    for i, a in enumerate(spend_txns):
        for b in spend_txns[i + 1:]:
            if (a["account_id"] == b["account_id"] and a["merchant_key"] == b["merchant_key"]
                    and abs(a["amount"] - b["amount"]) < 0.005 and a["id"] != b["id"]
                    and abs(a["posted"] - b["posted"]) <= th.duplicate_window_days * DAY):
                flags.append({"type": "possible_duplicate", "severity": "warn",
                              "entity": cfg.entity_for(a["account_id"]),
                              "text": f"Possible duplicate: {a['merchant'] or a['description']} ${-a['amount']:,.2f} on {_fmt_day(a['posted'])} and {_fmt_day(b['posted'])} ({a['account_name']})."})

    # ---- recurring charges: amount changed, or expected but missing
    recurring = conn.execute("SELECT key, merchant FROM merchants WHERE recurring=1").fetchall()
    for m in recurring:
        hist = [dict(r) for r in conn.execute(
            """SELECT posted, amount, account_id FROM transactions WHERE merchant_key=? AND amount<0
               AND transfer_group IS NULL AND posted < ? ORDER BY posted DESC LIMIT 6""", (m["key"], end))]
        if len(hist) < 3:
            continue
        gaps = [hist[i]["posted"] - hist[i + 1]["posted"] for i in range(len(hist) - 1)]
        cadence = statistics.median(gaps)
        if cadence < 20 * DAY or cadence > 45 * DAY:
            continue  # only handle roughly-monthly bills; weekly/annual are too noisy to call "missing"
        last = hist[0]
        ent = cfg.entity_for(last["account_id"])
        if last["posted"] >= start:
            prev = hist[1]["amount"]
            change = (abs(last["amount"]) - abs(prev)) / abs(prev) * 100 if prev else 0
            if abs(change) >= th.recurring_amount_change_pct:
                flags.append({"type": "recurring_changed", "severity": "warn", "entity": ent,
                              "text": f"{m['merchant']} changed: ${abs(prev):,.2f} → ${abs(last['amount']):,.2f} ({change:+.0f}%)."})
        elif end - last["posted"] > cadence + th.recurring_missing_grace_days * DAY:
            flags.append({"type": "recurring_missing", "severity": "info", "entity": ent,
                          "text": f"{m['merchant']} usually bills ~every {cadence / DAY:.0f} days; last seen {_fmt_day(last['posted'])}. Cancelled, or card declined?"})

    # ---- entity mismatch: personal-looking on business, business-looking on personal
    for t in spend_txns:
        ent = cfg.entity_for(t["account_id"])
        cat = t["category"]
        if ent == "unassigned" or not cat or -t["amount"] < 25:
            continue
        if ent == PERSONAL_ENTITY and cat in BUSINESS_LEANING:
            flags.append({"type": "entity_mismatch", "severity": "info", "entity": ent,
                          "text": f"Business-looking charge on a personal account: {t['merchant']} ${-t['amount']:,.2f} ({cat})."})
        elif ent != PERSONAL_ENTITY and cat in PERSONAL_LEANING:
            flags.append({"type": "entity_mismatch", "severity": "info", "entity": ent,
                          "text": f"Personal-looking charge on {ent}: {t['merchant']} ${-t['amount']:,.2f} ({cat}, {t['account_name']})."})

    # ---- uncategorized / other pile-up
    other = [t for t in spend_txns if (t["category"] or "other") == "other"]
    if len(other) >= 5:
        flags.append({"type": "uncategorized", "severity": "info", "entity": "all",
                      "text": f"{len(other)} transactions landed in 'other' this week — the classifier may need a taxonomy tweak."})

    # ---- sync health
    last_sync = conn.execute("SELECT * FROM sync_log ORDER BY ran_at DESC LIMIT 1").fetchone()
    if last_sync and last_sync["errors"]:
        flags.append({"type": "sync_errors", "severity": "warn", "entity": "all",
                      "text": f"SimpleFIN reported: {last_sync['errors']}"})

    # ---- shape output
    for e in entities.values():
        e["by_category"] = {k: round(v, 2) for k, v in sorted(e["by_category"].items(), key=lambda kv: -kv[1])}
        for k in ("net_worth", "net_7d", "net_30d", "spend", "income"):
            e[k] = round(e[k], 2)

    sev_order = {"warn": 0, "info": 1}
    flags.sort(key=lambda f: (sev_order.get(f["severity"], 2), f["entity"]))

    return {
        "week_start": datetime.fromtimestamp(start).strftime("%Y-%m-%d"),
        "week_end": as_of.strftime("%Y-%m-%d"),
        "entities": dict(entities),
        "flags": flags,
        "transactions": [
            {"date": _fmt_day(t["posted"]), "entity": cfg.entity_for(t["account_id"]),
             "account": t["account_name"], "merchant": t["merchant"] or t["description"],
             "category": t["category"] or "uncategorized", "amount": t["amount"],
             "transfer": bool(t["transfer_group"]), "pending": bool(t["pending"])}
            for t in week],
    }
