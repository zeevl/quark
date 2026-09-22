"""Turn the analysis dict into a Monday memo (markdown + HTML) and optionally email it."""
from __future__ import annotations

import json
import smtplib
import time
from email.message import EmailMessage

import anthropic
import markdown

from . import config

MEMO_SYSTEM = """You are a sharp, terse controller writing a Monday-morning finance memo for the owner
of several small businesses and his personal accounts. He reads this in two minutes on his phone.

You receive a JSON object with per-entity balances, weekly spend/income by category, a list of
pre-computed flags (severity warn|info), and the week's transactions.

Write markdown with exactly these sections:

## Act on this
Only warn-severity items that need a decision or action. If none, write "Nothing urgent." in one line.

## Balances
One compact table: Entity | Net position | 7d | 30d. Then, only if notable, one sentence on trajectory.

## This week by entity
For each entity: total spend, income, top 3 categories in one line each. Skip empty entities.

## Worth a glance
Info-severity flags, grouped, one line each. Merge near-duplicates. Cap at ~10 lines.

## Housekeeping
Stale accounts, unassigned accounts, sync errors, classifier issues. Omit the section if empty.

Rules: never invent numbers — use only what is in the JSON. No filler, no praise, no "as always".
Use $ with thousands separators. Dollar deltas: show sign. Don't restate the flag text verbatim
when a shorter phrasing works. Don't list every transaction; the full list is attached below the memo.
"""


def write_memo(summary: dict, model: str | None = None) -> str:
    model = model or config.ANTHROPIC_MODEL_MEMO
    client = anthropic.Anthropic()
    slim = {k: v for k, v in summary.items() if k != "transactions"}
    slim["transaction_count"] = len(summary["transactions"])
    # Give the model the transactions too, but only the non-transfer ones over $10, for context.
    slim["notable_transactions"] = [t for t in summary["transactions"]
                                    if not t["transfer"] and abs(t["amount"]) >= 10][:150]
    msg = client.messages.create(
        model=model, max_tokens=16000, system=MEMO_SYSTEM,  # headroom: thinking tokens count against this
        messages=[{"role": "user", "content": json.dumps(slim, default=str)}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    if not text:
        raise RuntimeError(f"memo model returned no text (stop_reason={msg.stop_reason}, usage={msg.usage})")
    return text


def transactions_table(summary: dict) -> str:
    rows = ["| Date | Entity | Account | Merchant | Category | Amount |", "|---|---|---|---|---|---:|"]
    for t in summary["transactions"]:
        if t["transfer"]:
            continue
        flag = " ⏳" if t["pending"] else ""
        rows.append(f"| {t['date']} | {t['entity']} | {t['account']} | {t['merchant']}{flag} | "
                    f"{t['category']} | {t['amount']:,.2f} |")
    return "\n".join(rows)


def render_html(memo_md: str, summary: dict) -> str:
    title = f"Weekly finances — week ending {summary['week_end']}"
    body = markdown.markdown(memo_md + "\n\n---\n\n### All transactions\n\n" + transactions_table(summary),
                             extensions=["tables"])
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<style>
body{{font:15px/1.5 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;color:#222}}
h2{{margin-top:28px;border-bottom:1px solid #ddd;padding-bottom:4px}}
table{{border-collapse:collapse;width:100%;font-size:13px}} th,td{{padding:4px 8px;border-bottom:1px solid #eee;text-align:left}}
td:last-child,th:last-child{{text-align:right;font-variant-numeric:tabular-nums}}
</style></head><body><h1 style="font-size:20px">{title}</h1>{body}</body></html>"""


def send_email(subject: str, html: str, text: str) -> None:
    if not config.MAIL_TO:
        raise RuntimeError("MAIL_TO must be set to email the report.")
    if config.SMTP_USER and config.SMTP_PASS:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = config.SMTP_USER
        msg["To"] = config.MAIL_TO
        msg.set_content(text)
        msg.add_alternative(html, subtype="html")
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as s:
            s.starttls()
            s.login(config.SMTP_USER, config.SMTP_PASS)
            s.send_message(msg)
        return
    # No SMTP creds: use the exe.dev email gateway (available on exe.dev VMs).
    # Plain-text body with the full HTML report attached.
    import base64

    import requests

    resp = requests.post(
        "http://169.254.169.254/gateway/email/send",
        json={"to": config.MAIL_TO, "subject": subject, "body": text,
              "attachments": [{"filename": "finsum-report.html",
                               "content": base64.b64encode(html.encode()).decode(),
                               "content_type": "text/html"}]},
        timeout=30)
    try:
        data = resp.json()
    except ValueError:
        data = {"error": resp.text[:200]}
    if not data.get("success"):
        raise RuntimeError(f"exe.dev email gateway rejected the send: {data}")


def write_index(out_dir) -> None:
    """Static index.html linking every report, newest first (for the httpd serving out/)."""
    reports = sorted(out_dir.glob("2*.html"), reverse=True)
    items = "\n".join(f'<li><a href="{p.name}">Week ending {p.stem}</a></li>' for p in reports)
    (out_dir / "index.html").write_text(
        '<!doctype html><html><head><meta charset="utf-8"><title>finsum reports</title>'
        '<style>body{font:15px/1.6 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;max-width:760px;'
        'margin:24px auto;padding:0 16px;color:#222}</style></head>'
        f'<body><h1>finsum reports</h1><ul>{items}</ul></body></html>')


def save_report(conn, summary: dict, memo_md: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO reports(week_ending, generated_at, markdown, summary_json) VALUES (?,?,?,?)",
        (summary["week_end"], int(time.time()), memo_md, json.dumps(summary, default=str)))
