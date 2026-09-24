"""finsum command line.

  finsum claim <setup-token>     exchange a one-time SimpleFIN setup token for an access URL
  finsum sync [--days N]         pull accounts + transactions (default: last 14 days)
  finsum backfill --days 365     pull history in chunks to build the baseline
  finsum categorize              classify any new merchants (runs automatically in sync)
  finsum report [--email]        analyze the last 7 days and write out/<date>.{md,html}
  finsum run [--email]           sync + categorize + report — what the cron job calls
  finsum accounts                list known accounts and their entity assignment
  finsum web                   regenerate out/index.html (dark dashboard) from the latest report in the DB
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta

from . import categorize, config, db, report, rules, simplefin, web


def _access_url() -> str:
    if not config.SIMPLEFIN_ACCESS_URL:
        sys.exit("SIMPLEFIN_ACCESS_URL is not set. Run `finsum claim <setup-token>` first and put the result in .env")
    return config.SIMPLEFIN_ACCESS_URL


def cmd_claim(args) -> None:
    url = simplefin.claim(args.setup_token)
    print("Access URL (store this as SIMPLEFIN_ACCESS_URL in .env — it is the only credential):\n")
    print(url)


def cmd_sync(args, days: int | None = None) -> int:
    days = days or args.days
    url = _access_url()
    start = datetime.now() - timedelta(days=days)
    print(f"Syncing last {days} days from SimpleFIN…")
    data = simplefin.fetch_range_chunked(url, start) if days > 60 else simplefin.fetch(url, start)
    now = int(time.time())
    with db.connect(config.DB_PATH) as conn:
        db.upsert_accounts(conn, data["accounts"], now)
        new = db.upsert_transactions(conn, data["accounts"], now)
        db.log_sync(conn, ok=True, new_txns=new, errors=data["errors"])
        added = config.write_accounts_stub([
            {"id": a["id"], "org": (a.get("org") or {}).get("name") or (a.get("org") or {}).get("domain") or "",
             "name": a.get("name", "")} for a in data["accounts"]])
        print(f"  {len(data['accounts'])} accounts, {new} new transactions")
        if data["errors"]:
            print("  bridge errors:", "; ".join(data["errors"]))
        if added:
            print(f"  {added} new account(s) added to {config.ACCOUNTS_PATH} — assign an entity to each (one time).")
        cfg = config.load_config()
        linked = rules.link_transfers(conn, cfg, since=int(start.timestamp()))
        if linked:
            print(f"  linked {linked} transfer pair(s)")
        if not args.no_categorize:
            n = categorize.classify_pending(conn)
            print(f"  categorized {n} new merchant(s)" if n else "  no new merchants to categorize")
    return new


def cmd_backfill(args) -> None:
    args.no_categorize = False
    cmd_sync(args, days=args.days)


def cmd_categorize(args) -> None:
    with db.connect(config.DB_PATH) as conn:
        n = categorize.classify_pending(conn)
        print(f"categorized {n} merchant(s)")


def cmd_accounts(args) -> None:
    cfg = config.load_config()
    with db.connect(config.DB_PATH) as conn:
        for r in conn.execute("SELECT * FROM accounts ORDER BY org, name"):
            bal = conn.execute("SELECT balance FROM balances WHERE account_id=? ORDER BY as_of DESC LIMIT 1",
                               (r["id"],)).fetchone()
            print(f"{cfg.entity_for(r['id']):<14} {r['org'][:24]:<24} {r['name'][:28]:<28} "
                  f"{(bal['balance'] if bal else 0):>12,.2f}   {r['id']}")


def cmd_report(args) -> None:
    cfg = config.load_config()
    with db.connect(config.DB_PATH) as conn:
        summary = rules.analyze(conn, cfg)
        print(f"{len(summary['flags'])} flag(s), {len(summary['transactions'])} transactions this week. Writing memo…")
        memo = report.write_memo(summary)
        report.save_report(conn, summary, memo)
    html = report.render_html(memo, summary)
    config.OUT_DIR.mkdir(parents=True, exist_ok=True)
    md_path = config.OUT_DIR / f"{summary['week_end']}.md"
    html_path = config.OUT_DIR / f"{summary['week_end']}.html"
    md_path.write_text(memo + "\n\n---\n\n" + report.transactions_table(summary))
    html_path.write_text(html)
    web.write_dashboard(config.OUT_DIR)
    print(f"wrote {md_path} and {html_path}")
    if args.email:
        report.send_email(f"Weekly finances — week ending {summary['week_end']}", html, memo)
        print(f"emailed to {config.MAIL_TO}")
    elif not args.quiet:
        print("\n" + memo)


def cmd_web(args) -> None:
    index = web.write_dashboard()
    print(f"wrote {index}")


def cmd_run(args) -> None:
    args.no_categorize = False
    args.days = 14
    cmd_sync(args)
    cmd_report(args)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="finsum", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("claim"); s.add_argument("setup_token"); s.set_defaults(fn=cmd_claim)
    s = sub.add_parser("sync"); s.add_argument("--days", type=int, default=14)
    s.add_argument("--no-categorize", action="store_true"); s.set_defaults(fn=cmd_sync)
    s = sub.add_parser("backfill"); s.add_argument("--days", type=int, default=365); s.set_defaults(fn=cmd_backfill)
    s = sub.add_parser("categorize"); s.set_defaults(fn=cmd_categorize)
    s = sub.add_parser("accounts"); s.set_defaults(fn=cmd_accounts)
    s = sub.add_parser("web"); s.set_defaults(fn=cmd_web)
    for name, fn in (("report", cmd_report), ("run", cmd_run)):
        s = sub.add_parser(name); s.add_argument("--email", action="store_true")
        s.add_argument("--quiet", action="store_true"); s.set_defaults(fn=fn)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
