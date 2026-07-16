# Artist Funding & Open-Call Aggregator

Pulls art-funding open calls from several sources, works out what's **new** since
the last run, tags each with best-effort region / type / funded guesses, and feeds
a filterable dashboard. You write the applications; this just does discovery,
aggregation, dedup, new-detection and deadline tracking.

```
aggregator.py     orchestrator + CLI (update / list / mark)
sources.py        one fetcher per source (RSS + HTML scrapers)
normalize.py      deadline / region / type / funded extraction (heuristic)
store.py          SQLite storage, new-detection, filtering, export
dashboard.html    filterable UI (reads opportunities.js)
opportunities.js  generated data (a sample is included to start)
```

## Setup
```bash
pip install -r requirements.txt
python aggregator.py update          # fetch all sources → store → export → show NEW
open dashboard.html                  # or serve the folder
```

## Sources
RSS (reliable): **Colossal** (monthly "Opportunities" roundups), **e-flux**.
HTML (scraped — selectors may need tuning): **Res Artis**, **On the Move**, **Stiftung Kunstfonds**.
Add your own by writing a `fetch_x()` in `sources.py` that returns
`{title, url, summary, source}` dicts and registering it in `SOURCES`.

> HTML scrapers depend on each site's markup. On the first live run, if a source
> returns 0 items, open the page, inspect it, and fix the CSS selector marked
> `# TUNE` in `sources.py`. One failing source never stops the others.

## Filtering (CLI)
```bash
python aggregator.py list --region DE --funded likely --within 60
python aggregator.py list --type Residency --search painting --sort newest
python aggregator.py list --new --since-days 7        # only recently-appeared
python aggregator.py mark <id> --status applied --notes "sent 12 Aug"
```
The dashboard offers the same filters (source, region, type, funded, deadline
window, keyword, new-only, has-deadline) plus a NEW badge and .ics export with
reminders 2 weeks and 3 days before each deadline.

## "Tell me when new ones appear"
Run `update` on a schedule and let it email you a digest of new items:
```bash
export SMTP_HOST=smtp.example.com SMTP_USER=you@x.com SMTP_PASS=... DIGEST_TO=you@x.com
python aggregator.py update --email
```
cron (weekdays 8am):
```
0 8 * * 1-5  cd /path/to/artist-aggregator && /usr/bin/python3 aggregator.py update --email >> agg.log 2>&1
```

## Notes / honesty
- Region, type and funding are **keyword heuristics** — triage, not truth. Confirm on the source page.
- Deadlines are auto-parsed from listing text; some will be missing or wrong.
- SQLite (`data.db`) keeps first-seen timestamps (for NEW) and your status marks even if a call drops off a source.
- To swap SQLite for Postgres/pgvector, replace `store.py` — the rest is agnostic. Embedding the summary and deduping by cosine similarity catches the same call syndicated across sites.
