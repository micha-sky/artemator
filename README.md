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

## Deploy to Netlify (with automatic daily refresh)

The dashboard is a **static site** — it deploys to Netlify as-is. The Python
scraper does **not** run on Netlify; a **GitHub Action** runs it on a schedule,
commits a fresh `opportunities.js`, and the push triggers a Netlify redeploy.

1. **Push this repo to GitHub.**
2. **Connect it to Netlify** → *Add new site → Import from Git*. Netlify reads
   `netlify.toml` (repo root): it publishes the `artist-aggregator/` folder and
   serves `dashboard.html` at `/`. No build command needed.
3. **Let it auto-refresh.** `.github/workflows/update.yml` runs daily at 06:00
   UTC (and on-demand from the Actions tab), regenerates the data, and commits
   it back. `data.db` is committed too, so "NEW since last run" and first-seen
   timestamps survive across CI runs.

That's it — the site updates itself once a day with no server to run. The
dashboard shows a **source-health strip** (per-source item counts; a red dot +
`error:` when a scraper breaks) and an **"updated Nm ago"** freshness badge, so
a silently-broken source is visible at a glance.

> Auth-walled or heavily bot-protected sources may return 0 items from GitHub's
> IP range even when they work locally — watch the health strip after the first
> CI run and tune selectors in `sources.py` if a source reads 0.

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
