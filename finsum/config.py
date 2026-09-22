"""Configuration: environment variables, accounts.yaml, thresholds, categories."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(os.environ.get("FINSUM_HOME", Path.cwd()))
DB_PATH = Path(os.environ.get("FINSUM_DB", ROOT / "finsum.db"))
ACCOUNTS_PATH = Path(os.environ.get("FINSUM_ACCOUNTS", ROOT / "accounts.yaml"))
OUT_DIR = Path(os.environ.get("FINSUM_OUT", ROOT / "out"))

SIMPLEFIN_ACCESS_URL = os.environ.get("SIMPLEFIN_ACCESS_URL", "")
ANTHROPIC_MODEL_CATEGORIZE = os.environ.get("FINSUM_MODEL_CATEGORIZE", "claude-haiku-4-5-20251001")
ANTHROPIC_MODEL_MEMO = os.environ.get("FINSUM_MODEL_MEMO", "claude-sonnet-5")

# SMTP (optional). Gmail works with an app password.
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
MAIL_TO = os.environ.get("MAIL_TO", "")

# Entities are free-form strings you assign per account in accounts.yaml.
# "personal" is special-cased for business/personal mismatch detection.
PERSONAL_ENTITY = "personal"

# Fixed taxonomy. Small on purpose: fewer buckets = more consistent classification.
CATEGORIES = [
    "software_saas", "cloud_hosting", "contractors_payroll", "advertising_marketing",
    "professional_services", "office_supplies", "shipping_postage", "travel",
    "meals_entertainment", "fuel_auto", "insurance", "taxes_gov_fees", "bank_fees_interest",
    "loan_payment", "rent_mortgage", "utilities_telecom", "groceries", "restaurants",
    "shopping_retail", "health_medical", "home_improvement", "subscriptions_media",
    "donations_gifts", "education", "income_revenue", "refund", "transfer",
    "credit_card_payment", "cash_atm", "other",
]

# Categories that look personal if they land on a business account, and vice-versa.
PERSONAL_LEANING = {"groceries", "restaurants", "shopping_retail", "health_medical",
                    "home_improvement", "subscriptions_media", "cash_atm"}
BUSINESS_LEANING = {"software_saas", "cloud_hosting", "contractors_payroll",
                    "advertising_marketing", "professional_services", "shipping_postage"}

NON_SPEND = {"transfer", "credit_card_payment", "income_revenue", "refund"}


@dataclass
class Thresholds:
    large_txn_default: float = 1000.0          # single transaction size that always gets flagged
    large_txn_by_entity: dict[str, float] = field(default_factory=dict)
    vendor_spike_multiple: float = 2.0         # weekly vendor spend vs trailing median
    vendor_spike_min: float = 100.0            # ignore spikes below this dollar amount
    vendor_baseline_weeks: int = 12
    duplicate_window_days: int = 3
    recurring_amount_change_pct: float = 5.0
    recurring_missing_grace_days: int = 7
    stale_account_days: int = 3
    transfer_match_window_days: int = 4

    def large_txn(self, entity: str) -> float:
        return self.large_txn_by_entity.get(entity, self.large_txn_default)


@dataclass
class AccountConfig:
    entity: str | None = None
    label: str | None = None
    kind: str = "unknown"      # checking | savings | credit | loan | brokerage | unknown
    ignore: bool = False


@dataclass
class Config:
    accounts: dict[str, AccountConfig]
    thresholds: Thresholds
    raw: dict

    def entity_for(self, account_id: str) -> str:
        acct = self.accounts.get(account_id)
        return (acct.entity if acct and acct.entity else "unassigned")

    def label_for(self, account_id: str, fallback: str) -> str:
        acct = self.accounts.get(account_id)
        return (acct.label if acct and acct.label else fallback)


def load_config() -> Config:
    raw: dict = {}
    if ACCOUNTS_PATH.exists():
        raw = yaml.safe_load(ACCOUNTS_PATH.read_text()) or {}
    accounts = {
        str(k): AccountConfig(**{kk: vv for kk, vv in (v or {}).items()
                                 if kk in AccountConfig.__dataclass_fields__})
        for k, v in (raw.get("accounts") or {}).items()
    }
    t = raw.get("thresholds") or {}
    thresholds = Thresholds(**{k: v for k, v in t.items() if k in Thresholds.__dataclass_fields__})
    return Config(accounts=accounts, thresholds=thresholds, raw=raw)


def write_accounts_stub(discovered: list[dict]) -> int:
    """Add any newly discovered accounts to accounts.yaml with entity: null.
    Returns the number of accounts added. Never overwrites existing entries."""
    raw: dict = {}
    if ACCOUNTS_PATH.exists():
        raw = yaml.safe_load(ACCOUNTS_PATH.read_text()) or {}
    raw.setdefault("accounts", {})
    raw.setdefault("thresholds", {"large_txn_default": 1000, "large_txn_by_entity": {"personal": 500}})
    added = 0
    for a in discovered:
        if a["id"] not in raw["accounts"]:
            raw["accounts"][a["id"]] = {
                "label": f"{a['org']} {a['name']}".strip(),
                "entity": None,          # <- set to e.g. scribeware | giving_group | personal
                "kind": "unknown",
            }
            added += 1
    if added or not ACCOUNTS_PATH.exists():
        ACCOUNTS_PATH.write_text(
            "# One-time setup: assign each account an entity. Everything else is automatic.\n"
            "# entity: scribeware | giving_group | realchemy | personal  (any string works)\n"
            "# kind:   checking | savings | credit | loan | brokerage\n"
            "# ignore: true  to exclude an account entirely\n\n"
            + yaml.safe_dump(raw, sort_keys=False)
        )
    return added
