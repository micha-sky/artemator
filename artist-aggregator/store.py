"""
store.py — SQLite persistence, "new since last run" detection, filtering, export.
"""
import sqlite3
import json
import os
from datetime import datetime, date, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), "data.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS opportunities (
    id         TEXT PRIMARY KEY,
    title      TEXT, org TEXT, url TEXT, source TEXT, summary TEXT,
    deadline   TEXT, region TEXT, type TEXT, funded TEXT, amount TEXT,
    discipline TEXT,
    first_seen TEXT, last_seen TEXT
);
CREATE TABLE IF NOT EXISTS status (          -- your own marks, kept even if a call drops off
    id TEXT PRIMARY KEY, mark TEXT, notes TEXT
);
"""


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _conn() as c:
        c.executescript(SCHEMA)
        # Migration for DBs created before the discipline column existed.
        cols = {r["name"] for r in c.execute("PRAGMA table_info(opportunities)")}
        if "discipline" not in cols:
            c.execute("ALTER TABLE opportunities ADD COLUMN discipline TEXT")


def upsert_many(items):
    """Insert/update. Returns the list of items that are NEW (not seen before)."""
    now = datetime.utcnow().isoformat(timespec="seconds")
    new_items = []
    with _conn() as c:
        for it in items:
            row = c.execute("SELECT id FROM opportunities WHERE id=?", (it["id"],)).fetchone()
            if row is None:
                new_items.append(it)
                c.execute(
                    """INSERT INTO opportunities
                       (id,title,org,url,source,summary,deadline,region,type,funded,amount,discipline,first_seen,last_seen)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (it["id"], it["title"], it["org"], it["url"], it["source"], it["summary"],
                     it["deadline"], it["region"], it["type"], it["funded"], it["amount"],
                     it.get("discipline", ""), now, now),
                )
            else:
                c.execute(
                    """UPDATE opportunities SET title=?,org=?,url=?,source=?,summary=?,
                       deadline=?,region=?,type=?,funded=?,amount=?,discipline=?,last_seen=? WHERE id=?""",
                    (it["title"], it["org"], it["url"], it["source"], it["summary"],
                     it["deadline"], it["region"], it["type"], it["funded"], it["amount"],
                     it.get("discipline", ""), now, it["id"]),
                )
    return new_items


def set_mark(opp_id, mark=None, notes=None):
    with _conn() as c:
        c.execute("INSERT INTO status (id,mark,notes) VALUES (?,?,?) "
                  "ON CONFLICT(id) DO UPDATE SET mark=COALESCE(?,mark), notes=COALESCE(?,notes)",
                  (opp_id, mark, notes, mark, notes))


def query(region=None, type=None, funded=None, source=None, search=None,
          within_days=None, new_since=None, has_deadline=None, sort="deadline",
          discipline=None):
    sql = "SELECT o.*, s.mark, s.notes FROM opportunities o LEFT JOIN status s ON o.id=s.id WHERE 1=1"
    args = []
    if region:   sql += " AND o.region=?";               args.append(region)
    if type:     sql += " AND o.type=?";                 args.append(type)
    if funded:   sql += " AND o.funded=?";               args.append(funded)
    if source:   sql += " AND o.source=?";               args.append(source)
    if discipline: sql += " AND o.discipline LIKE ?";    args.append(f"%{discipline}%")
    if search:   sql += " AND (LOWER(o.title) LIKE ? OR LOWER(o.summary) LIKE ?)"; \
                 args += [f"%{search.lower()}%", f"%{search.lower()}%"]
    if has_deadline: sql += " AND o.deadline IS NOT NULL"
    if new_since:    sql += " AND o.first_seen >= ?";    args.append(new_since)
    with _conn() as c:
        rows = [dict(r) for r in c.execute(sql, args).fetchall()]
    today = date.today()
    for r in rows:
        r["days_left"] = (date.fromisoformat(r["deadline"]) - today).days if r["deadline"] else None
    if within_days is not None:
        rows = [r for r in rows if r["days_left"] is not None and 0 <= r["days_left"] <= within_days]
    if sort == "deadline":
        rows.sort(key=lambda r: (r["days_left"] is None, r["days_left"] if r["days_left"] is not None else 1e9))
    elif sort == "newest":
        rows.sort(key=lambda r: r["first_seen"], reverse=True)
    return rows


def export(js_path=None, json_path=None, sources=None):
    """Write a data file the dashboard can read. JS form works from file:// (no CORS).

    `sources` is an optional per-source health map ({name: count | "error: ..."})
    surfaced in the dashboard so a silently-broken scraper is visible. When not
    supplied (e.g. an export triggered by `mark`), the last-written health map is
    preserved rather than blanked — only `update` refreshes it.
    """
    rows = query(sort="deadline")
    base = os.path.dirname(__file__)
    js_path = js_path or os.path.join(base, "opportunities.js")
    json_path = json_path or os.path.join(base, "opportunities.json")
    if sources is None:
        try:
            with open(json_path) as f:
                sources = json.load(f).get("sources", {})
        except (OSError, ValueError):
            sources = {}
    payload = {
        "generated": datetime.utcnow().isoformat(timespec="seconds"),
        "sources": sources,
        "opportunities": rows,
    }
    with open(json_path, "w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    with open(js_path, "w") as f:
        f.write("window.OPPORTUNITIES = " + json.dumps(payload, ensure_ascii=False) + ";")
    return len(rows)
