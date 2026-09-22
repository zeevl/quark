"""Minimal SimpleFIN Bridge client. Protocol: https://www.simplefin.org/protocol.html

Flow:
  1. You create a Setup Token at https://bridge.simplefin.org (one time, after connecting banks).
  2. `claim(setup_token)` POSTs to the decoded claim URL and returns an Access URL
     (https://user:pass@bridge.simplefin.org/simplefin). Setup tokens are single-use.
  3. `fetch(access_url, start, end)` GETs {access_url}/accounts with Basic auth embedded in the URL.
"""
from __future__ import annotations

import base64
import time
from datetime import datetime, timedelta

import requests

TIMEOUT = 120


def claim(setup_token: str) -> str:
    claim_url = base64.b64decode(setup_token.strip()).decode("utf-8")
    r = requests.post(claim_url, timeout=TIMEOUT)
    if r.status_code == 403:
        raise RuntimeError("Setup token already used or invalid (403). Generate a new one at the Bridge.")
    r.raise_for_status()
    return r.text.strip()


def fetch(access_url: str, start: datetime | None = None, end: datetime | None = None,
          pending: bool = True) -> dict:
    params: dict = {}
    if start:
        params["start-date"] = int(start.timestamp())
    if end:
        params["end-date"] = int(end.timestamp())
    if pending:
        params["pending"] = 1
    r = requests.get(access_url.rstrip("/") + "/accounts", params=params, timeout=TIMEOUT)
    if r.status_code == 403:
        raise RuntimeError("SimpleFIN auth failed (403). Access URL may have been revoked.")
    r.raise_for_status()
    data = r.json()
    data.setdefault("errors", [])
    data.setdefault("accounts", [])
    return data


def fetch_range_chunked(access_url: str, start: datetime, end: datetime | None = None,
                        chunk_days: int = 60) -> dict:
    """The Bridge caps history per request; walk backwards in chunks and merge."""
    end = end or datetime.now()
    merged: dict = {"errors": [], "accounts": {}}
    cursor_end = end
    while cursor_end > start:
        cursor_start = max(start, cursor_end - timedelta(days=chunk_days))
        data = fetch(access_url, cursor_start, cursor_end)
        merged["errors"].extend(e for e in data["errors"] if e not in merged["errors"])
        for a in data["accounts"]:
            slot = merged["accounts"].setdefault(a["id"], {**a, "transactions": []})
            # keep the newest balance snapshot
            if a.get("balance-date", 0) >= slot.get("balance-date", 0):
                for k in ("balance", "available-balance", "balance-date"):
                    if k in a:
                        slot[k] = a[k]
            slot["transactions"].extend(a.get("transactions", []))
        cursor_end = cursor_start
        time.sleep(0.5)
    merged["accounts"] = list(merged["accounts"].values())
    return merged
