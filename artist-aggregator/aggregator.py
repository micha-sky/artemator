#!/usr/bin/env python3
"""
aggregator.py — combine multiple art-funding sources, learn what's NEW, filter.

Usage
-----
  python aggregator.py update                 # fetch all sources, store, export, show new
  python aggregator.py update --sources colossal,resartis
  python aggregator.py update --email         # also send a digest of new items (SMTP env)

  python aggregator.py list                    # everything, soonest deadline first
  python aggregator.py list --region DE --funded likely --within 60
  python aggregator.py list --type Residency --search painting
  python aggregator.py list --new --since-days 7      # only calls first seen in last 7 days
  python aggregator.py list --sort newest

  python aggregator.py mark <id> --status applied --notes "sent 12 Aug"

Run `update` on a schedule (cron / launchd / a Docker sidecar) to keep the
dashboard's opportunities.js fresh and get a digest of anything new.
"""
import argparse
import os
import smtplib
import sys
from datetime import datetime, timedelta
from email.mime.text import MIMEText

import store
import sources as src
from normalize import (normalize, is_relevant, extract_requirements,
                       extract_deadline, extract_amount, guess_funded)


def cmd_update(args):
    store.init()
    which = args.sources.split(",") if args.sources else list(src.SOURCES)
    raw_all, errors, health = [], [], {}
    for name in which:
        name = name.strip()
        fn = src.SOURCES.get(name)
        if not fn:
            errors.append(f"unknown source '{name}'")
            continue
        try:
            items = fn()
            raw_all += items
            health[name] = len(items)
            print(f"  {name:12s} {len(items):4d} items")
        except Exception as e:  # one bad source must not kill the run
            errors.append(f"{name}: {type(e).__name__}: {e}")
            health[name] = f"error: {e}"[:60] or f"error: {type(e).__name__}"
            print(f"  {name:12s} FAILED — {e}")

    normalized, seen, dropped = [], set(), 0
    for r in raw_all:
        n = normalize(r)
        if n["id"] in seen or not n["title"]:
            continue
        seen.add(n["id"])
        if not is_relevant(n):   # expired, or no opportunity signal → skip storage
            dropped += 1
            continue
        normalized.append(n)

    new_items = store.upsert_many(normalized)
    if args.enrich:
        _enrich(args.enrich)
    total = store.export(sources=health)
    print(f"\n{len(normalized)} unique · {len(new_items)} NEW · {dropped} filtered · {total} in store")
    if errors:
        print("errors: " + "; ".join(errors))

    if new_items:
        print("\n── NEW SINCE LAST RUN " + "─" * 40)
        for it in sorted(new_items, key=lambda x: x["deadline"] or "9999"):
            print(_fmt_line(it))
        if args.email:
            _send_digest(new_items)


def _enrich(cap):
    """Fetch each not-yet-enriched call's own page (soonest deadline first, up
    to `cap` per run) to pull the full description and detect what the
    application asks for; fills deadline/amount/funded only where missing."""
    todo = store.needing_details(limit=cap)
    if not todo:
        return
    print(f"\nenriching {len(todo)} item(s) from their detail pages…")
    ok = 0
    for it in todo:
        try:
            details = src.fetch_detail(it["url"])
        except Exception as e:  # leave details NULL → retried on a later run
            print(f"  ✗ {it['title'][:60]} — {type(e).__name__}: {e}")
            continue
        blob = " ".join(filter(None, [it["title"], it["summary"], details]))
        funded = guess_funded(blob)
        store.save_details(
            it["id"], details,
            requirements=", ".join(extract_requirements(blob)),
            deadline=extract_deadline(blob),
            amount=extract_amount(blob),
            funded=funded if funded != "unknown" else None,
        )
        ok += 1
    print(f"  enriched {ok}/{len(todo)}")


def cmd_list(args):
    store.init()
    since = None
    if args.new:
        since = (datetime.utcnow() - timedelta(days=args.since_days)).isoformat(timespec="seconds")
    rows = store.query(
        region=args.region, type=args.type, funded=args.funded, source=args.source,
        search=args.search, within_days=args.within, new_since=since,
        has_deadline=args.has_deadline, sort=args.sort, discipline=args.discipline,
    )
    if not rows:
        print("no matches.")
        return
    print(f"{len(rows)} result(s):\n")
    for r in rows:
        line = _fmt_line(r)
        if r.get("mark"):
            line += f"  [{r['mark']}]"
        print(line)
        print(f"      {r['id']}  ·  {r['url']}")


def cmd_mark(args):
    store.init()
    store.set_mark(args.id, mark=args.status, notes=args.notes)
    store.export()
    print(f"marked {args.id}: {args.status or ''} {('· '+args.notes) if args.notes else ''}")


def _fmt_line(r):
    dl = r.get("deadline") or "—"
    dleft = r.get("days_left")
    days = f"{dleft:>4}d" if isinstance(dleft, int) else "  --"
    fund = {"likely": "€", "fee-based": "fee", "mixed": "€/fee", "unknown": " ? "}.get(r.get("funded"), " ? ")
    disc = f"  {{{r['discipline']}}}" if r.get("discipline") else ""
    return f"{dl:<11} {days}  {fund:>4}  [{r.get('region','?'):<4}] {r.get('type','?'):<10} {r['title'][:70]}{disc}  ({r['source']})"


def _send_digest(new_items):
    host = os.getenv("SMTP_HOST"); user = os.getenv("SMTP_USER")
    pw = os.getenv("SMTP_PASS"); to = os.getenv("DIGEST_TO")
    if not all([host, user, pw, to]):
        print("(email skipped — set SMTP_HOST/SMTP_USER/SMTP_PASS/DIGEST_TO)")
        return
    body = "New art-funding calls since last run:\n\n" + "\n".join(
        f"• {_fmt_line(it)}\n  {it['url']}" for it in new_items)
    msg = MIMEText(body)
    msg["Subject"] = f"[art-funding] {len(new_items)} new call(s)"
    msg["From"] = user; msg["To"] = to
    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587"))) as s:
        s.starttls(); s.login(user, pw); s.send_message(msg)
    print(f"digest emailed to {to}")


def build_parser():
    p = argparse.ArgumentParser(description="Aggregate & track art-funding open calls.")
    sub = p.add_subparsers(dest="cmd", required=True)

    u = sub.add_parser("update", help="fetch sources, store, detect new, export")
    u.add_argument("--sources", help="comma list, e.g. colossal,resartis (default: all)")
    u.add_argument("--email", action="store_true", help="email a digest of new items")
    u.add_argument("--enrich", type=int, default=25, metavar="N",
                   help="fetch up to N detail pages for description/requirements (0 = off, default 25)")
    u.set_defaults(func=cmd_update)

    l = sub.add_parser("list", help="filter stored opportunities")
    l.add_argument("--region", choices=["DE", "EU", "Intl"])
    l.add_argument("--type", help="Residency / Grant / Mobility / Prize / Open Call / Other")
    l.add_argument("--discipline", help="Painting / Sound/Music / Writing / Photography / … (substring)")
    l.add_argument("--funded", choices=["likely", "fee-based", "mixed", "unknown"])
    l.add_argument("--source")
    l.add_argument("--search", help="keyword in title/summary")
    l.add_argument("--within", type=int, metavar="DAYS", help="deadline within N days")
    l.add_argument("--has-deadline", action="store_true", help="only calls with a parsed deadline")
    l.add_argument("--new", action="store_true", help="only recently first-seen")
    l.add_argument("--since-days", type=int, default=7)
    l.add_argument("--sort", choices=["deadline", "newest"], default="deadline")
    l.set_defaults(func=cmd_list)

    m = sub.add_parser("mark", help="set your own status/notes on a call")
    m.add_argument("id")
    m.add_argument("--status", choices=["interested", "applied", "skip"])
    m.add_argument("--notes")
    m.set_defaults(func=cmd_mark)
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    args.func(args)
