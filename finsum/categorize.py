"""Zero-maintenance categorization.

Each distinct merchant_key is sent to Claude exactly once; the result is cached in the
`merchants` table. Nothing here ever asks a human anything.
"""
from __future__ import annotations

import json
import re

import anthropic

from . import config, db

BATCH = 40

SYSTEM = f"""You classify bank/credit-card transaction descriptors into a fixed taxonomy.
Return ONLY a JSON array, no prose, no markdown fences. One object per input, same order:
{{"key": <input key verbatim>, "merchant": <clean human merchant name>, "category": <one of the categories>, "recurring": <true if this is typically a subscription/recurring bill/payroll/loan payment, else false>}}

Categories: {", ".join(config.CATEGORIES)}

Rules:
- Positive amounts are inflows. Payroll deposits, Stripe/customer payouts -> income_revenue. Refunds -> refund.
- Bank-to-bank moves, Zelle/Venmo to self, "ONLINE TRANSFER", brokerage sweeps -> transfer.
- Payments TO a credit card (e.g. "AMEX EPAYMENT", "CHASE CREDIT CRD AUTOPAY") -> credit_card_payment.
- When unsure between two, pick the more specific business category; use other only as a last resort.
"""


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def _parse_json_array(text: str) -> list[dict]:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("["), text.rfind("]")
    return json.loads(text[start:end + 1])


def classify_batch(client: anthropic.Anthropic, rows: list[dict], model: str) -> list[dict]:
    payload = [{"key": r["key"], "description": r["description"], "payee": r["payee"],
                "avg_amount": round(r["avg_amount"], 2), "count": r["n"]} for r in rows]
    msg = client.messages.create(
        model=model, max_tokens=4000, system=SYSTEM,
        messages=[{"role": "user", "content": json.dumps(payload)}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    out = _parse_json_array(text)
    valid = set(config.CATEGORIES)
    by_key = {r["key"]: r for r in rows}
    results = []
    for o in out:
        k = o.get("key")
        if k not in by_key:
            continue
        cat = o.get("category") if o.get("category") in valid else "other"
        results.append({"key": k, "merchant": (o.get("merchant") or k).strip()[:80],
                        "category": cat, "recurring": bool(o.get("recurring")),
                        "sample_description": by_key[k]["description"]})
    # anything the model dropped gets "other" so we never re-ask forever
    seen = {r["key"] for r in results}
    for r in rows:
        if r["key"] not in seen:
            results.append({"key": r["key"], "merchant": r["key"].title(), "category": "other",
                            "recurring": False, "sample_description": r["description"]})
    return results


def classify_pending(conn, model: str | None = None, verbose: bool = True) -> int:
    model = model or config.ANTHROPIC_MODEL_CATEGORIZE
    rows = [dict(r) for r in db.unclassified_merchants(conn)]
    if not rows:
        return 0
    client = _client()
    done = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        results = classify_batch(client, chunk, model)
        db.save_merchants(conn, results, model)
        conn.commit()
        done += len(results)
        if verbose:
            print(f"  classified {done}/{len(rows)} merchants")
    return done
