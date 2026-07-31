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
    discipline TEXT, requirements TEXT, details TEXT,
    country    TEXT, place TEXT, region_group TEXT,
    career_stage TEXT, fee_eur REAL, lat REAL, lon REAL,
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
        # Migrations for DBs created before newer columns existed.
        cols = {r["name"] for r in c.execute("PRAGMA table_info(opportunities)")}
        for col in ("discipline", "requirements", "details",
                    "country", "place", "region_group", "career_stage"):
            if col not in cols:
                c.execute(f"ALTER TABLE opportunities ADD COLUMN {col} TEXT")
        for col in ("fee_eur", "lat", "lon"):
            if col not in cols:
                c.execute(f"ALTER TABLE opportunities ADD COLUMN {col} REAL")


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
                       (id,title,org,url,source,summary,deadline,region,type,funded,amount,
                        discipline,requirements,country,place,region_group,career_stage,
                        fee_eur,lat,lon,first_seen,last_seen)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (it["id"], it["title"], it["org"], it["url"], it["source"], it["summary"],
                     it["deadline"], it["region"], it["type"], it["funded"], it["amount"],
                     it.get("discipline", ""), it.get("requirements", ""),
                     it.get("country", ""), it.get("place", ""), it.get("region_group", ""),
                     it.get("career_stage", "any"), it.get("fee_eur"),
                     it.get("lat"), it.get("lon"), now, now),
                )
            else:
                # requirements/location/fee/stage: listing-page text is a subset of
                # what enrichment saw, so never let an emptier re-scrape blank or
                # downgrade an enriched value.
                c.execute(
                    """UPDATE opportunities SET title=?,org=?,url=?,source=?,summary=?,
                       deadline=?,region=?,type=?,funded=?,amount=?,discipline=?,
                       requirements=COALESCE(NULLIF(?,''),requirements),
                       country=COALESCE(NULLIF(?,''),country),
                       place=COALESCE(NULLIF(?,''),place),
                       region_group=COALESCE(NULLIF(?,''),region_group),
                       career_stage=COALESCE(NULLIF(?,'any'),career_stage,'any'),
                       fee_eur=COALESCE(?,fee_eur),
                       lat=COALESCE(?,lat), lon=COALESCE(?,lon),
                       last_seen=? WHERE id=?""",
                    (it["title"], it["org"], it["url"], it["source"], it["summary"],
                     it["deadline"], it["region"], it["type"], it["funded"], it["amount"],
                     it.get("discipline", ""), it.get("requirements", ""),
                     it.get("country", ""), it.get("place", ""), it.get("region_group", ""),
                     it.get("career_stage", "any"), it.get("fee_eur"),
                     it.get("lat"), it.get("lon"), now, it["id"]),
                )
    return new_items


def needing_details(limit=25):
    """Items whose own page hasn't been fetched yet (details is NULL — a failed
    fetch leaves it NULL so it's retried on a later run). Soonest deadline first
    so the most urgent calls get enriched before the cap cuts off."""
    with _conn() as c:
        rows = c.execute(
            """SELECT id, url, title, summary, deadline, amount, funded FROM opportunities
               WHERE details IS NULL ORDER BY COALESCE(deadline,'9999'), first_seen DESC LIMIT ?""",
            (limit,)).fetchall()
    return [dict(r) for r in rows]


def save_details(opp_id, details, requirements=None, deadline=None, amount=None,
                 funded=None, location=None, fee_eur=None, career_stage=None):
    """Store detail-page text; fill deadline/amount/funded/location/fee/stage
    only where the listing-level guess left a gap. `location` is the dict from
    normalize.guess_location (may be {})."""
    loc = location or {}
    with _conn() as c:
        c.execute(
            """UPDATE opportunities SET details=?,
               requirements=COALESCE(NULLIF(?,''),requirements),
               deadline=COALESCE(deadline,?),
               amount=CASE WHEN amount IS NULL OR amount='' THEN COALESCE(?,amount) ELSE amount END,
               funded=CASE WHEN (funded IS NULL OR funded='unknown') AND ? IS NOT NULL THEN ? ELSE funded END,
               country=COALESCE(NULLIF(country,''),NULLIF(?,'')),
               place=COALESCE(NULLIF(place,''),NULLIF(?,'')),
               region_group=COALESCE(NULLIF(region_group,''),NULLIF(?,'')),
               lat=COALESCE(lat,?), lon=COALESCE(lon,?),
               fee_eur=COALESCE(fee_eur,?),
               career_stage=CASE WHEN (career_stage IS NULL OR career_stage='any')
                                 AND ? IS NOT NULL THEN ? ELSE career_stage END
               WHERE id=?""",
            (details, requirements, deadline, amount, funded, funded,
             loc.get("country", ""), loc.get("place", ""), loc.get("region_group", ""),
             loc.get("lat"), loc.get("lon"), fee_eur,
             career_stage, career_stage, opp_id))


def set_mark(opp_id, mark=None, notes=None):
    with _conn() as c:
        c.execute("INSERT INTO status (id,mark,notes) VALUES (?,?,?) "
                  "ON CONFLICT(id) DO UPDATE SET mark=COALESCE(?,mark), notes=COALESCE(?,notes)",
                  (opp_id, mark, notes, mark, notes))


def query(region=None, type=None, funded=None, source=None, search=None,
          within_days=None, new_since=None, has_deadline=None, sort="deadline",
          discipline=None, group=None, stage=None, max_fee=None):
    sql = "SELECT o.*, s.mark, s.notes FROM opportunities o LEFT JOIN status s ON o.id=s.id WHERE 1=1"
    args = []
    if region:   sql += " AND o.region=?";               args.append(region)
    if type:     sql += " AND o.type=?";                 args.append(type)
    if funded:   sql += " AND o.funded=?";               args.append(funded)
    if source:   sql += " AND o.source=?";               args.append(source)
    if discipline: sql += " AND o.discipline LIKE ?";    args.append(f"%{discipline}%")
    if group:    sql += " AND o.region_group LIKE ?";    args.append(f"%{group}%")
    # stage/fee fail open: 'any'-stage and unknown-fee calls stay visible
    if stage:    sql += " AND (o.career_stage=? OR o.career_stage='any' OR o.career_stage IS NULL)"; \
                 args.append(stage)
    if max_fee is not None: sql += " AND (o.fee_eur IS NULL OR o.fee_eur<=?)"; args.append(max_fee)
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
    # full detail text is kept in the DB for parsing; the dashboard only needs
    # enough to read, so cap it to keep opportunities.js light
    for r in rows:
        if r.get("details") and len(r["details"]) > 1500:
            r["details"] = r["details"][:1500].rsplit(" ", 1)[0] + " …"
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
