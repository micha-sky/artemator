# Artist Funding & Open-Call Aggregator

Pulls art-funding open calls from several sources, works out what's **new** since
the last run, tags each with best-effort region / type / funded guesses, and feeds
a filterable dashboard. You write the applications; this does discovery,
aggregation, dedup, new-detection, deadline tracking — and per-call application
prep (full description, "apply with" checklist, notes, reusable kit).

```
aggregator.py     orchestrator + CLI (update / list / mark / reapply)
sources.py        one fetcher per source (RSS + HTML scrapers) + apply-link extraction
normalize.py      deadline / region / type / funded / fee / career-stage / fit extraction (heuristic)
geo.py            offline gazetteer: text → country, region groups, map coordinates
store.py          SQLite storage, new-detection, filtering, export
prefs.py          loads the active personalization profile (AGG_PROFILE)
profiles/*.json   per-person tuning: regions, disciplines, sources, fit keywords
dashboard.html    filterable UI with list + map views (reads opportunities.js)
opportunities.js  generated data (a sample is included to start)
```

## Setup
```bash
pip install -r requirements.txt
python test_sources.py                   # offline checks (apply-link + sitemap rules)
python aggregator.py update              # 'default' profile: every source, neutral ranking
AGG_PROFILE=sash python aggregator.py update    # tailored to a specific practice
open dashboard.html                      # or serve the folder
```

## Profiles — one codebase, tailored per person
Which sources run, which region-groups and disciplines are "mine", and how the
**fit** score is computed all live in `profiles/<name>.json`, selected at runtime
with the `AGG_PROFILE` env var (default `default`). The active profile is baked
into `opportunities.js`, so the **same dashboard** adapts itself — the fit preset
and best-fit sort simply hide themselves under the neutral `default` profile.

```jsonc
// profiles/sash.json
{
  "label": "SYMBIONT — sound & media art (DE)",
  "preferred_groups": ["Germany", "German-speaking", "Western Europe", ...],
  "my_disciplines": ["Sound/Music", "Digital/New Media", "Performance"],
  "region_boost": {"DE": 2, "EU": 1},
  "enabled_sources": ["resartis", "onthemove", ..., "zkm", "ctm"],
  "fit_keywords": [ {"weight": 3, "phrases": ["biofeedback", "sound art", ...]}, ... ]
}
```
Ship a profile per person (`sash`, `kasti`, …); each runs `update` under their own
`AGG_PROFILE` to regenerate their view. `default` is a neutral superset (all
sources, no fit scoring) so the tool is useful out of the box.

## Sources
General art-funding: **Colossal**, **e-flux**, **Hyperallergic** (RSS); **On the Move**,
**Stiftung Kunstfonds**, **TransArtists** (via curl — Cloudflare 403s python-requests),
**ArtConnect** (`__NEXT_DATA__` JSON), **Res Artis** (WP sitemap + per-call pages),
**culture360** (ASEF's Asia-Europe board) — HTML/JSON scraped.

Sound / media-art (enabled by the `sash` profile; skipped by `kasti`): **Initiative Musik** and **Stiftung Musikfonds**
(Germany's federal music funders — Projektförderung, STIP stipends, Outer Ear),
**ZKM Karlsruhe** (Hertzlab open calls — the institutional sweet spot), **CTM Festival**
(Berlin; open-calls index discovered from the homepage so the festival-year URL isn't
hard-coded), **Ars Electronica** (Prix, emitted as one annual open call).

Add your own by writing a `fetch_x()` in `sources.py` that returns
`{title, url, summary, source}` dicts and registering it in `SOURCES`.

### German & EU funders — the coverage wedge
The English-language aggregators carry the Bund/Länder foundations badly or not
at all, so these are scraped directly: **Kulturstiftung des Bundes**, **Fonds
Darstellende Künste**, **Fonds Soziokultur**, **Deutscher Literaturfonds**,
**Deutscher Künstlerbund**, **bbk Bundesverband**, **Akademie Schloss Solitude**,
**Schering Stiftung**, plus the Länder foundations (**Hessische Kulturstiftung**,
**Kunststiftung NRW**, **Kulturstiftung Sachsen**, **Kunststiftung BW**) and
**Creative Europe Desk KULTUR**.

They share one fetcher. Fifteen bespoke scrapers would mean fifteen selectors to
re-tune after every redesign, so `fetch_funder` is deliberately markup-agnostic:
it uses the site's RSS/Atom feed when it declares one, and otherwise scans the
listing page for call-shaped links — same host, real anchor text, German call
vocabulary in the link's own block. A wrong path is survivable (a 404 retries
the site root and re-discovers). Adding a funder is a `FUNDERS` entry, not code:

```jsonc
"kunststiftungnrw": {
  "source": "Kunststiftung NRW", "org": "Kunststiftung NRW",
  "url": "https://www.kunststiftungnrw.de/foerderung/",
  "region": "DE", "country": "Germany", "type": "Grant"
  // "feed": "…"  — set explicitly if auto-discovery picks the wrong one
}
```

Check one before trusting it — `probe` runs a fetcher and shows what it would
emit, without touching the database (`✗` marks items `is_relevant` would drop):
```bash
python aggregator.py probe                          # every funder
python aggregator.py probe --sources bbk,solitude --show 10
```

> **Status:** the funder URLs are configured but **not yet confirmed against the
> live sites** — run `probe` and fix any that report `no call-shaped links
> found`. The health strip names each one that fails, so nothing breaks quietly.

### Reading German listings
`normalize.py` parses German dates and funding vocabulary, not just English:
`Bewerbungsschluss: 15. März 2027`, `Antragsfrist: 2. Jänner 2027` and
`Frist: 15. Dez. 2026` all resolve, and *Förderung / Zuschuss / Honorar /
Preisgeld* read as funding while *Teilnahmegebühr / Eigenanteil* read as a fee.
This matters more than it sounds: German funders write "15. März 2027" far more
often than "15.03.2027", and before this the parser found **0 deadlines in 55
Kunstfonds listings** — a deadline tracker with no deadlines. Add a language by
adding its month names to `_MONTH_NAMES` in `normalize.py`.

> **Not added (checked):** Goethe-Institut has no central open-call board — its
> residency/mobility calls are spread across program subpages and largely surface
> via **On the Move** already; a dedicated scraper would be fragile. Add one as a
> `fetch_x()` if a specific Goethe program becomes worth tracking.

> HTML scrapers depend on each site's markup. On the first live run, if a source
> returns 0 items, open the page, inspect it, and fix the CSS selector marked
> `# TUNE` in `sources.py`. One failing source never stops the others.

### Reading the health strip
Every HTTP fetch retries transient failures (connection resets, timeouts, 429 /
5xx) three times with backoff, so a one-off blip no longer costs a day of
listings. What reaches the strip is therefore a real failure, and the chip names
the kind — hover it for the full message:

| chip | means |
| --- | --- |
| `resartis · 100` | fine |
| `eflux · 0` | the fetcher ran and found nothing — a warning, worth a look |
| `resartis · ConnectionError` | the site was unreachable for all three tries |
| `resartis · RuntimeError` | our own diagnosis: markup moved, or we hit a bot wall |

RSS sources **raise** on an empty channel rather than reporting 0 items: a feed
that quietly returns nothing (a publisher filtering our User-Agent, a moved feed
URL) is broken, and e-flux sat at 0 for weeks without anything going red.

**Res Artis** needs two tricks. Its `/open-calls/` listing is behind an
`sgcaptcha` bot wall, so the fetcher reads the WordPress sitemap instead and lets
enrich fetch each `/open-call/<slug>/` page. The sitemap *index* is itself
occasionally served as the bot wall or a connection error — when that happens the
fetcher probes the numbered `wp-sitemap-posts-open_call-N.xml` files directly
(they answer with a 404 status and a real XML body) and only gives up if those
fail too. The three failure modes report distinctly — unreachable, bot wall, or a
genuinely changed sitemap layout — instead of all showing up as "layout changed?".

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
- a **fit score** (`--sort fit`, "sort: best fit" in the UI): weights the active
  profile's `fit_keywords` plus funded / home-region / my-discipline signals.
  Cards show a **◈ fit N** badge and the matched keyword chips. Edit the profile's
  `fit_keywords` to retune; the `default` profile scores none, so fit is inert;
- **region-group chips** ordered by the profile's `preferred_groups` first, with a
  **★ my regions** preset that toggles them all;
- a **◈ Best fit** preset — everything ranked by fit (funded and home regions stay
  *soft* preferences the ranking encodes, so nothing relevant is hard-filtered out) —
  a **⌂ Residency mode** cut of the same brief, and a **★ Interested** view that
  narrows to the calls you marked (keeping expired picks), one click each;
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

**Real apply links.** Aggregators like On the Move, culture360 and Res Artis are
middlemen: their page summarises a call and links out to the organiser's own site
where you actually apply. During enrich, `fetch_detail` digs out that outbound
link (`apply_url`) so the dashboard's **Open application page** button skips the
middleman — cards that resolved one show a **↳ direct to organiser** note.

A link is only accepted as `apply_url` when it looks like an application *and*
addresses a specific page: an organiser's homepage or a bare `/en` section root
is rejected, because landing on one and hunting for the call down a menu is worse
than landing on the listing page, which at least describes it. Submission portals
(`apply.` subdomains, Submittable, JotForm, Google Forms) count even without a
path — the host *is* the application. Social, payment and link-hub URLs never do.
When nothing clears the bar the card falls back to the listing URL and says so
(**Open listing page** · ↳ *listing page · find "apply" there*), rather than
promising an application page it can't deliver.

```bash
python aggregator.py reapply                 # backfill apply_url on old listings
python aggregator.py reapply --prune         # …and first drop links that only
                                             #   point at a homepage
python aggregator.py reapply --prune --limit 0   # prune only, no network
```

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
