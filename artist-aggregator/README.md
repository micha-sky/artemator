# Artist Funding & Open-Call Aggregator

Pulls art-funding open calls from several sources, works out what's **new** since
the last run, tags each with best-effort region / type / funded guesses, and feeds
a filterable dashboard. You write the applications; this does discovery,
aggregation, dedup, new-detection, deadline tracking — and per-call application
prep (full description, "apply with" checklist, notes, reusable kit).

```
aggregator.py     orchestrator + CLI (update / list / mark)
sources.py        one fetcher per source (RSS + HTML scrapers)
normalize.py      deadline / region / type / funded / fee / career-stage extraction (heuristic)
geo.py            offline gazetteer: text → country, region groups, map coordinates
store.py          SQLite storage, new-detection, filtering, export
dashboard.html    filterable UI with list + map views (reads opportunities.js)
opportunities.js  generated data (a sample is included to start)
```

## Setup
```bash
pip install -r requirements.txt
python aggregator.py update          # fetch all sources → store → export → show NEW
open dashboard.html                  # or serve the folder
```

## Sources
General art-funding: **Colossal**, **e-flux**, **Hyperallergic** (RSS); **On the Move**,
**Stiftung Kunstfonds**, **TransArtists** (via curl — Cloudflare 403s python-requests),
**ArtConnect** (`__NEXT_DATA__` JSON), **Res Artis** (WP sitemap + per-call pages),
**culture360** (ASEF's Asia-Europe board) — HTML/JSON scraped.

Sound / media-art (the SYMBIONT tilt): **Initiative Musik** and **Stiftung Musikfonds**
(Germany's federal music funders — Projektförderung, STIP stipends, Outer Ear),
**ZKM Karlsruhe** (Hertzlab open calls — the institutional sweet spot), **CTM Festival**
(Berlin; open-calls index discovered from the homepage so the festival-year URL isn't
hard-coded), **Ars Electronica** (Prix, emitted as one annual open call).

Add your own by writing a `fetch_x()` in `sources.py` that returns
`{title, url, summary, source}` dicts and registering it in `SOURCES`.

> **Not added (checked):** Goethe-Institut has no central open-call board — its
> residency/mobility calls are spread across program subpages and largely surface
> via **On the Move** already; a dedicated scraper would be fragile. Add one as a
> `fetch_x()` if a specific Goethe program becomes worth tracking.

> HTML scrapers depend on each site's markup. On the first live run, if a source
> returns 0 items, open the page, inspect it, and fix the CSS selector marked
> `# TUNE` in `sources.py`. One failing source never stops the others.

## Filtering (CLI)
```bash
python aggregator.py list --region DE --funded likely --within 60
python aggregator.py list --discipline Sound/Music --sort fit          # best-fit first
python aggregator.py list --group "German-speaking" --funded likely --max-fee 0
python aggregator.py list --new --since-days 7        # only recently-appeared
python aggregator.py mark <id> --status applied --notes "sent 12 Aug"
```
The dashboard offers the same filters (source, type, discipline, funded,
career stage, deadline window, keyword, new-only, has-deadline) plus:
- a **fit score** (`--sort fit`, "sort: best fit" in the UI): weights lineage
  keywords (biofeedback, EEG, bio-art, spatial audio, live electronics, media art…)
  plus funded / home-turf / sound-media-discipline signals. Cards show a **◈ fit N**
  badge and the matched keyword chips. Tune `FIT_KEYWORDS`/`fit_score` in `normalize.py`;
- **region-group chips** ordered home-turf first (Germany, German-speaking, Western
  Europe, Nordics, Eastern Europe, Baltic, France/Paris, Mediterranean, …) with a
  **★ my regions** preset;
- a **◈ SYMBIONT fit** preset — sound/new-media/performance work, best-fit sort
  (funded and home-turf stay *soft* preferences the ranking encodes, so nothing
  relevant is hard-filtered out; add **★ my regions** to narrow to Germany + Europe) —
  and a **⌂ Residency mode** cut of the same brief, one click each;
- an **application-fee slider** (0–200 €; "incl. unknown fee" keeps the many calls
  that never state a fee visible — stage and fee filters fail open by design);
- a **map view** (Leaflet + clustering; pins are city- or country-level from the
  offline gazetteer, red = closing ≤ 7 days, popups with Interested/Skip);
- a NEW badge and .ics export with reminders 2 weeks and 3 days before each deadline.
Filter state persists in the browser.

## Detail enrichment & applying
`update` also visits each call's own page (up to `--enrich N` per run, default 25,
soonest deadline first; `--enrich 0` disables) to pull the **full description**,
detect the **application materials** it asks for (CV, portfolio, statement, work
samples, proposal, fee…), and fill in missing deadlines/amounts.

In the dashboard every card shows an "apply with: …" chip row, and
**▾ Details / Apply** expands the card in place: full description, a checklist of
required materials you can tick off as you prepare, per-call notes, and your
**My kit** links (portfolio / CV / statement, set once via the masthead button)
with one-click copy — so applying on the source page is just paste-paste-submit.
Checklists, notes and the kit live in your browser's localStorage.

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
