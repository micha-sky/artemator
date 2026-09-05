"""
sources.py — one fetcher per source. Each returns a list of raw dicts:
    {title, url, summary, source, country?}

RSS sources are reliable. HTML scrapers depend on each site's markup, which
changes over time — the CSS selectors marked "TUNE" are the bits you'll adjust
on the first live run (open the page, inspect, fix the selector). Every fetcher
is wrapped so one broken source never kills the whole run.
"""
import functools
import json
import re
import subprocess
import time
from urllib.parse import unquote, urljoin, urlparse

import requests
import feedparser
from bs4 import BeautifulSoup

import geo

# ISO code -> primary country name, for sources that ship codes ("CA") instead
# of names: the gazetteer matches names, so translate before building summaries.
_ISO_NAME = {iso: names[0].title() for iso, (names, _c, _g) in geo.COUNTRIES.items()}

HEADERS = {"User-Agent": "artist-aggregator/1.0 (personal opportunity tracker)"}
TIMEOUT = 20
TRIES = 3          # a scraper run is once a day — a couple of retries is cheap
# Statuses worth retrying: rate limits and the origin/CDN hiccups that make a
# single-shot fetch report a permanently-dead source.
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


def _request(url, tries=TRIES, lax=False):
    """GET with retries and exponential backoff.

    Transient failures (DNS blips, TLS resets, connection timeouts, a 502 from
    a CDN) are the single biggest source of false "this scraper is broken"
    reports in the health strip — resartis.org, kunstfonds.de and
    culture360.asef.org have each shown up as a one-run
    `HTTPSConnectionPool(...)` error and been fine the next day. Retrying turns
    those into a working run instead of a red dot and a day of lost listings.

    `lax=True` ignores the status code (resartis.org serves its wp-sitemap
    sub-files with a 404 status and the real XML in the body) but still retries
    connection-level failures.
    """
    last = None
    for attempt in range(max(1, tries)):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if lax:
                return r.text
            if r.status_code in RETRY_STATUS:
                last = requests.HTTPError(f"HTTP {r.status_code} for {url}", response=r)
            else:
                r.raise_for_status()
                return r.text
        except requests.RequestException as e:
            last = e
        if attempt < tries - 1:
            time.sleep(2 * 2 ** attempt)          # 2s, 4s
    raise last


def _get(url, tries=TRIES):
    return _request(url, tries=tries)


def _get_lax(url, tries=TRIES):
    """Like _get but ignores the status code — see _request."""
    return _request(url, tries=tries, lax=True)


# Bodies that come back with a 200 but are a bot wall, not the page. Without
# this a challenge page is parsed as if it were the real markup and the source
# reports a confident but wrong diagnosis ("layout changed") instead of "we got
# blocked".
_BOT_WALL_MARKERS = ("sgcaptcha", "captcha", "just a moment",
                     "checking your browser", "cf-browser-verification",
                     "enable javascript and cookies", "attention required!",
                     "ddos protection by", "please verify you are a human")


def _looks_like_bot_wall(text):
    head = (text or "")[:4000].lower()
    return any(m in head for m in _BOT_WALL_MARKERS)


def _feed(url, source):
    """Parse an RSS/Atom feed, fetched through _get so it gets our User-Agent
    and the retry/backoff above (feedparser's own fetcher sends its default UA,
    which some publishers 403 — that reads as a silently empty feed).

    A feed that parses to zero entries is treated as broken and raises: an
    empty channel is indistinguishable from a working source on a quiet day,
    and e-flux sat at 0 items for weeks without anything going red.
    """
    parsed = feedparser.parse(_get(url))
    if not parsed.entries:
        reason = getattr(parsed, "bozo_exception", None)
        raise RuntimeError(
            f"{source} feed parsed to 0 entries ({url})"
            + (f" — {type(reason).__name__}: {reason}" if reason else
               " — feed reachable but empty; check the feed URL"))
    return parsed.entries


def _get_curl(url, tries=2):
    """Fetch via the system curl. transartists.org's Cloudflare tier blocks
    python-requests by TLS fingerprint (403 regardless of headers) but serves
    curl normally, so this fetcher shells out. Transient connection resets
    (exit 56) get one retry after a pause."""
    for attempt in range(tries):
        r = subprocess.run(
            ["curl", "-sL", "--fail", "-A", HEADERS["User-Agent"],
             "-m", str(TIMEOUT), url],
            capture_output=True, text=True)
        if r.returncode == 0 and r.stdout:
            return r.stdout
        if attempt < tries - 1:
            time.sleep(4)
    raise RuntimeError(f"curl exit {r.returncode} for {url}")


# ---------- RSS sources (robust) ----------

def _feed_items(url, source):
    return [{"title": e.get("title", ""), "url": e.get("link", ""),
             "summary": BeautifulSoup(e.get("summary", ""), "html.parser").get_text(" ", strip=True),
             "source": source} for e in _feed(url, source)]


def fetch_colossal():
    """This Is Colossal — dedicated monthly 'Opportunities' roundup feed.

    Each roundup post lists many calls in its body; we emit the post itself.
    """
    return _feed_items("https://www.thisiscolossal.com/category/opportunities/feed/",
                       "Colossal")


def fetch_eflux():
    """e-flux announcements. Read through _feed: fetched with our own
    User-Agent (feedparser's default UA gets filtered by some publishers) and
    loud about an empty channel rather than quietly reporting 0 items."""
    return _feed_items("https://www.e-flux.com/announcements/feed/", "e-flux")


def fetch_hyperallergic():
    """Hyperallergic — dedicated 'Opportunities' tag feed (open calls, grants,
    fellowships, residencies). Reliable RSS."""
    return _feed_items("https://hyperallergic.com/tag/opportunities/feed/",
                       "Hyperallergic")


# ---------- HTML scrapers (TUNE selectors on first live run) ----------

_RESARTIS_INDEX = "https://resartis.org/wp-sitemap.xml"
_RESARTIS_OC_MAP = "https://resartis.org/wp-sitemap-posts-open_call-{n}.xml"
_RESARTIS_MAP_RE = re.compile(r"https://resartis\.org/wp-sitemap-posts-open_call-(\d+)\.xml")
_RESARTIS_LOC_RE = re.compile(r"<loc>(https://resartis\.org/open-call/[^<]+)</loc>")


def _resartis_open_call_maps():
    """URLs of the open_call sub-sitemaps, newest last.

    Normally they're listed in the sitemap index. The index is also the one
    resartis.org URL that intermittently comes back as the sgcaptcha wall or a
    connection error, which used to fail the whole source with a confidently
    wrong "sitemap layout changed?". So when the index is unusable, probe the
    numbered sub-sitemaps directly — they are served (with a 404 status and a
    real XML body, hence _get_lax) even when the index isn't.
    """
    index, index_err = "", None
    try:
        index = _get(_RESARTIS_INDEX)
    except Exception as e:                       # noqa: BLE001 — reported below
        index_err = e
    nums = sorted({int(n) for n in _RESARTIS_MAP_RE.findall(index)})
    if nums:
        return [_RESARTIS_OC_MAP.format(n=n) for n in nums]

    probed = []
    for n in range(1, 11):                       # stop at the first gap
        try:
            body = _get_lax(_RESARTIS_OC_MAP.format(n=n), tries=2)
        except requests.RequestException:
            break
        if not _RESARTIS_LOC_RE.search(body):
            break
        probed.append(_RESARTIS_OC_MAP.format(n=n))
    if probed:
        return probed

    # Nothing worked — say which of the three it actually was.
    if index_err is not None:
        raise RuntimeError(
            f"resartis sitemap unreachable — {type(index_err).__name__}: {index_err}")
    if _looks_like_bot_wall(index):
        raise RuntimeError("resartis sitemap returned the sgcaptcha bot wall, not XML "
                           "— needs a headless browser to revive")
    raise RuntimeError("no open_call sitemap found — sitemap layout changed?")


def fetch_resartis(newest=100):
    """Res Artis open calls, via the WordPress sitemap.

    The /open-calls/ listing page sits behind an sgcaptcha bot-challenge, but
    the wp-sitemap and the individual /open-call/<slug>/ pages are served
    normally. So: read the open_call post-type sitemap (entries are in post
    order — the tail is the newest), emit the newest N as slug-titled stubs,
    and let `update`'s enrich step fetch each call's own page, which carries
    the structured "Application deadline YYYY-MM-DD … Location <Country>"
    block the extractors feed on.
    """
    oc_maps = _resartis_open_call_maps()
    urls = []
    for sm in oc_maps[-2:]:                       # last two files cover the newest posts
        # _get_lax: these sub-sitemaps come back with a 404 status + real XML body
        body = _get_lax(sm)
        found = _RESARTIS_LOC_RE.findall(body)
        if not found and _looks_like_bot_wall(body):
            raise RuntimeError(f"resartis served the bot wall for {sm} instead of XML")
        urls += found
    if not urls:
        raise RuntimeError("resartis sitemaps carried no /open-call/ URLs "
                           "— post type renamed?")
    out = []
    for u in urls[-newest:]:
        slug = unquote(u.rstrip("/").rsplit("/", 1)[-1])
        title = re.sub(r"-\d+$", "", slug).replace("-", " ").strip().capitalize()
        if len(title) < 6:
            continue
        out.append({"title": title, "url": u, "summary": "",
                    "source": "Res Artis", "type": "Residency"})
    return _dedupe_local(out)


def fetch_transartists():
    """TransArtists (DutchCulture) 'Call for artists' board — the largest
    residency database, strong Asia/Eastern-Europe coverage. Drupal view table:
    each row holds the ad in td.views-field-field-your-ad (title in an h2,
    links inline). The board itself has no per-ad pages, so the url is the
    ad's first external link.

    Only the bare board URL passes Cloudflare (?page=N gets the JS challenge),
    so each run reads the newest ~10 ads; the daily cadence accumulates the
    older ones. _get_curl because python-requests' TLS fingerprint is 403'd."""
    html = _get_curl("https://www.transartists.org/en/call-artists")
    if "Just a moment" in html[:3000]:
        raise RuntimeError("Cloudflare JS challenge — needs a real browser")
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for td in soup.select("td.views-field-field-your-ad"):
        head = td.find("h2")
        if not head:
            continue
        title = head.get_text(" ", strip=True)
        if len(title) < 6:
            continue
        url = next((a["href"] for a in td.find_all("a", href=True)
                    if a["href"].startswith("http")
                    and "transartists.org" not in a["href"]
                    and "dutchculture.nl" not in a["href"]), "")
        if not url:
            continue                      # no external link → nothing to apply to
        out.append({"title": title, "url": url,
                    "summary": td.get_text(" ", strip=True)[:1200],
                    "source": "TransArtists"})
    return _dedupe_local(out)


_AC_TYPE = {"RESIDENCY": "Residency", "OPEN_CALL": "Open Call", "GRANT": "Grant",
            "COMPETITION": "Prize", "EXHIBITION": "Open Call", "JOB": "Other"}


def fetch_artconnect(pages=5):
    """ArtConnect opportunities, residency category. Next.js app: listings sit
    fully structured (deadline, fee, country, artistic fields) in the
    __NEXT_DATA__ JSON blob, so no selector guessing. Fee/location/disciplines
    are folded into the summary text in the exact vocabulary normalize.py's
    extractors look for."""
    out = []
    for p in range(1, pages + 1):
        html = _get(f"https://www.artconnect.com/opportunities?category=Residencies&page={p}")
        m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                      html, re.S)
        if not m:
            raise RuntimeError("__NEXT_DATA__ not found — page layout changed?")
        payload = json.loads(m.group(1))
        try:
            data = payload["props"]["pageProps"]["opportunities"]["data"]
        except (KeyError, TypeError):
            raise RuntimeError("opportunities JSON moved — inspect __NEXT_DATA__")
        for o in data:
            title = (o.get("title") or "").strip()
            if len(title) < 6:
                continue
            prof = o.get("profile") or {}
            desc = " ".join(d.get("content", "") for d in (o.get("description") or [])
                            if isinstance(d, dict))
            desc = re.sub(r"[*_#\\]+", "", desc)          # strip markdown noise
            city = o.get("city") or prof.get("city") or ""
            iso = o.get("country") or prof.get("country") or ""
            country = _ISO_NAME.get(iso, iso or "")
            place = ", ".join(filter(None, [city, country]))
            fields = " ".join(f.replace("_", " ").lower()
                              for f in (o.get("artisticFields") or []))
            fee = o.get("fee")
            fee_txt = ("No application fee." if fee == "FREE" else
                       (o.get("feeDescription") or ""))
            bits = [desc[:900], f"Location: {place}." if place else "",
                    fields, fee_txt]
            deadline = (o.get("deadline") or "")[:10] or None
            out.append({"title": title,
                        "url": f"https://www.artconnect.com/opportunities/{o.get('id', '')}",
                        "summary": " ".join(b for b in bits if b).strip(),
                        "source": "ArtConnect", "deadline": deadline,
                        "type": _AC_TYPE.get(o.get("type"), None),
                        "country": place})
    return _dedupe_local(out)


# On the Move groups listings by Drupal view "deadline blocks", each carrying a
# view-display-id-<category> class. Map the useful ones to a type; skip the
# categories that aren't funding/open-call opportunities.
_OTM_CATEGORY_TYPE = {
    "residencies": "Residency", "fellowships": "Grant", "project_funding": "Grant",
    "commissions": "Open Call", "presenting_work": "Open Call",
    "competitions": "Prize", "training": "Other",
}
_OTM_SKIP = {"jobs", "meeting", "surveys"}


def fetch_onthemove():
    """On the Move deadlines — real listings are /news/ links inside the
    .view-deadline-blocks views; nav/boilerplate links live outside them."""
    html = _get("https://on-the-move.org/news/deadlines")
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for view in soup.select("[class*=view-display-id-]"):
        cat = next((c.split("view-display-id-", 1)[1] for c in view.get("class", [])
                    if c.startswith("view-display-id-")), "")
        if cat in _OTM_SKIP:
            continue
        for a in view.select('a[href^="/news/"]'):
            title = a.get_text(" ", strip=True)
            if len(title) < 10 or "?" in a["href"]:  # skip country/region facet links
                continue
            # climb to the nearest wrapper carrying the "Deadline: …" text; a real
            # listing always has one — facet/nav links inside the view don't.
            summary, node = None, a
            for _ in range(6):
                node = node.parent
                if node is None:
                    break
                t = node.get_text(" ", strip=True)
                if "deadline" in t.lower():
                    summary = t
                    break
            if summary is None:
                continue
            out.append({"title": title, "url": "https://on-the-move.org" + a["href"],
                        "summary": summary, "source": "On the Move",
                        "type": _OTM_CATEGORY_TYPE.get(cat)})
    return _dedupe_local(out)


# kunstfonds.de/aktuelles is a general news feed (obituaries, statements,
# retrospective "we distributed €X" press releases) with the open calls mixed
# in. Require a German call/application signal so only actual calls come through.
_KFN_CALL_SIGNALS = ("ausschreibung", "bewerbung", "bewerbungsschluss",
                     "einsendeschluss", "frist", "jetzt bewerben", "call for",
                     "deadline", "stipendienprogramm")


def fetch_kunstfonds():
    """Stiftung Kunstfonds — German federal visual-arts funding foundation. Each
    post is a .kfn-newsPreviews__listItem (title in an h4); keep only posts whose
    text carries a call/application signal, then normalize/is_relevant do the rest."""
    html = _get("https://www.kunstfonds.de/aktuelles/")
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for it in soup.select(".kfn-newsPreviews__listItem"):
        a = it.find("a", href=True)
        head = it.select_one("h4") or a
        if not a or not head:
            continue
        title = head.get_text(" ", strip=True)
        if len(title) < 8:
            continue
        text = it.get_text(" ", strip=True)
        if not any(s in text.lower() for s in _KFN_CALL_SIGNALS):
            continue
        href = a["href"]
        if href.startswith("/"):
            href = "https://www.kunstfonds.de" + href
        out.append({"title": title, "url": href, "org": "Stiftung Kunstfonds",
                    "summary": text, "source": "Kunstfonds", "region": "DE"})
    return _dedupe_local(out)


_C360_CAT_TYPE = {"residencies": "Residency", "grants": "Grant", "open calls": "Open Call",
                  "competitions": "Prize", "festivals": "Open Call"}


def fetch_culture360(pages=3):
    """ASEF culture360 opportunities — the Asia-Europe Foundation's board and
    the main aggregator for Asia-side (incl. Southeast Asia / Mekong) open
    calls and residencies. Cards are .c360-card-opportunity with the title in
    h3.card-title (usually "Country | Title" — the gazetteer feeds on that),
    plus category and "deadline: 09 Aug 2026" text. Their RSS feed 502s, so
    HTML it is; ?page=N pagination works unchallenged."""
    out = []
    for p in range(1, pages + 1):
        url = "https://culture360.asef.org/opportunities/" + (f"?page={p}" if p > 1 else "")
        soup = BeautifulSoup(_get(url), "html.parser")
        for card in soup.select(".c360-card-opportunity"):
            a = card.select_one("h3.card-title a") or card.select_one("h3 a")
            if not a or not a.get("href"):
                continue
            title = a.get_text(" ", strip=True)
            if len(title) < 6:
                continue
            href = a["href"]
            if href.startswith("/"):
                href = "https://culture360.asef.org" + href
            cat = (card.select_one(".item-footer-category") or card).get_text(" ", strip=True).lower()
            out.append({"title": title, "url": href,
                        "summary": card.get_text(" ", strip=True)[:400],
                        "source": "culture360",
                        "type": next((t for k, t in _C360_CAT_TYPE.items() if k in cat), None)})
    return _dedupe_local(out)


# ---------- Sound / media-art sources (SYMBIONT-tuned) ----------

_IM_PROG_RE = re.compile(r"https://www\.initiative-musik\.de/([a-zäöü-]+f[öo]erderung)/?$")


def fetch_initiativemusik():
    """Initiative Musik — Germany's federal music-funding agency (artist
    development, structural / export / live-music grants). Its WordPress RSS feed
    is an empty channel, so scrape the /foerderprogramme/ hub instead: each
    active programme is a top-level /<name>förderung/ page. Emit those; enrich
    fills each programme's conditions and current deadline."""
    soup = BeautifulSoup(_get("https://www.initiative-musik.de/foerderprogramme/"), "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = _IM_PROG_RE.match(href)
        if not m or href in seen:
            continue
        seen.add(href)
        title = a.get_text(" ", strip=True)
        if len(title) < 4:
            title = m.group(1).title()
        out.append({"title": f"Initiative Musik — {title}", "url": href,
                    "summary": title, "source": "Initiative Musik",
                    "org": "Initiative Musik", "region": "DE", "type": "Grant"})
    return _dedupe_local(out)


def fetch_musikfonds():
    """Stiftung Musikfonds — Germany's federal fund for contemporary/experimental
    music (Projektförderung up to €50k, the STIP stipends, Outer Ear). The
    /foerderprogramme page lists each programme as an h2/h3 section; emit the
    open ones (skip everything under 'Abgeschlossene Programme') with a couple
    of paragraphs of body text so normalize picks up deadlines/stipend signals.
    Each shares the page URL, so a title-slug fragment keeps their ids distinct."""
    soup = BeautifulSoup(_get("https://www.musikfonds.de/foerderprogramme"), "html.parser")
    _SECTION_LABELS = {"reguläre förderprogramme", "aktuelle sonderprogramme",
                       "laufende sonderprogramme", "abgeschlossene programme"}
    out, closed = [], False
    for h in soup.find_all(["h2", "h3"]):
        title = h.get_text(" ", strip=True)
        low = title.lower()
        if "abgeschlossen" in low:            # completed programmes → stop emitting
            closed = True
        if closed or not title or low in _SECTION_LABELS:
            continue
        ps, sib = [], h
        while len(ps) < 2:                    # walk forward to the next heading
            sib = sib.find_next(["p", "h2", "h3"])
            if sib is None or sib.name in ("h2", "h3"):
                break
            txt = sib.get_text(" ", strip=True)
            if txt:
                ps.append(txt)
        slug = re.sub(r"[^a-z0-9]+", "-", low).strip("-")[:40]
        out.append({"title": title,
                    "url": f"https://www.musikfonds.de/foerderprogramme#{slug}",
                    "summary": (title + " — " + " ".join(ps))[:800],
                    "source": "Musikfonds", "org": "Stiftung Musikfonds",
                    "region": "DE", "type": "Grant"})
    return _dedupe_local(out)


def fetch_zkm():
    """ZKM | Center for Art and Media, Karlsruhe — its Hertzlab runs sound /
    immersive / media-art open calls (the searcher's institutional sweet spot,
    home of the Sonic Experiments residency). The /en/open-calls page links each
    live call as /en/open-call-<slug>; emit those and let enrich fill the page."""
    soup = BeautifulSoup(_get("https://zkm.de/en/open-calls"), "html.parser")
    out = []
    for a in soup.select("a[href*='/open-call-']"):    # the trailing '-' skips the index /open-calls
        href = a["href"]
        if href.startswith("/"):
            href = "https://zkm.de" + href
        title = a.get_text(" ", strip=True)
        if len(title) < 8:
            continue
        out.append({"title": title, "url": href, "summary": title,
                    "source": "ZKM", "org": "ZKM Karlsruhe",
                    "country": "Karlsruhe, Germany", "region": "DE", "type": "Open Call"})
    return _dedupe_local(out)


def fetch_ctm():
    """CTM Festival, Berlin — adventurous electronic & experimental music; runs
    annual open calls (performance, radio lab, research networking). The festival
    year sits in the URL, so discover the current open-calls index from the
    homepage nav rather than hard-coding it, then emit each sub-page."""
    soup = BeautifulSoup(_get("https://www.ctm-festival.de/"), "html.parser")
    idx = next((a["href"] for a in soup.find_all("a", href=True)
                if re.search(r"/open-calls/?$", a["href"])), None)
    if not idx:
        raise RuntimeError("CTM open-calls index link not found on homepage")
    if idx.startswith("/"):
        idx = "https://www.ctm-festival.de" + idx
    base = idx.rstrip("/")
    soup = BeautifulSoup(_get(idx), "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/open-calls/" not in href:
            continue
        full = href if href.startswith("http") else "https://www.ctm-festival.de" + href
        if full.rstrip("/") == base:          # the index itself, not a call
            continue
        title = a.get_text(" ", strip=True)
        if len(title) < 8:
            continue
        out.append({"title": title, "url": full, "summary": title,
                    "source": "CTM", "org": "CTM Festival",
                    "country": "Berlin, Germany", "region": "DE", "type": "Open Call"})
    return _dedupe_local(out)


def fetch_arselectronica():
    """Ars Electronica, Linz — Prix Ars Electronica, the leading media-art prize
    (Digital Musics & Sound Art, Interactive Art, AI). One annual open call, so
    emit the Prix page as a single item; enrich pulls the current deadline and
    category text (which carries strong sound/new-media keyword signal)."""
    url = "https://ars.electronica.art/prix/en/"
    soup = BeautifulSoup(_get(url), "html.parser")
    h = soup.find("h1")
    title = re.sub(r"\s+", " ", h.get_text(" ", strip=True)) if h else "Prix Ars Electronica"
    body = soup.find("main") or soup.body or soup
    summary = re.sub(r"\s+", " ", body.get_text(" ", strip=True))[:600]
    return [{"title": title[:100], "url": url, "summary": summary,
             "source": "Ars Electronica", "org": "Ars Electronica",
             "country": "Linz, Austria", "region": "EU", "type": "Prize"}]


# ---------- German / EU funders (config-driven) ----------
#
# The German-language funding landscape is the coverage gap: the English
# aggregators carry the Bund/Länder foundations badly or not at all. Each one is
# a small institutional site with a news or Förderung page, and writing fifteen
# bespoke scrapers would mean fifteen selectors to re-tune every redesign. So
# this is one fetcher driven by config, deliberately markup-agnostic:
#
#   1. use the site's RSS/Atom feed when it has one (auto-discovered from the
#      page's <link rel="alternate">), because a feed never needs re-tuning;
#   2. otherwise scan the listing page for call-shaped links — same host, real
#      anchor text, and German call vocabulary somewhere in the surrounding
#      block. That's how fetch_kunstfonds works, minus the site-specific CSS.
#
# A wrong path is survivable: a 404 on the configured URL retries the site root
# and auto-discovers from there. Anything still broken shows up in the health
# strip by name rather than failing quietly.

_DE_CALL_SIGNALS = _KFN_CALL_SIGNALS + (
    "ausschreibungen", "bewerbungsfrist", "antragsfrist", "abgabefrist",
    "einreichfrist", "einreichungsfrist", "bewerbungsverfahren", "antragstellung",
    "förderprogramm", "foerderprogramm", "förderung", "stipendium", "stipendien",
    "residenz", "residency", "open call", "wettbewerb", "preis", "call",
)
# Href fragments worth following on a funder site; everything else is chrome.
_DE_LINK_HINTS = ("ausschreibung", "foerder", "förder", "stipend", "bewerb",
                  "antrag", "residen", "programm", "call", "wettbewerb",
                  "preis", "news", "aktuel", "grant", "fellowship", "opportunit")


def _discover_feed(html, base):
    """The page's declared RSS/Atom feed, if it has one."""
    soup = BeautifulSoup(html, "html.parser")
    link = soup.find("link", rel=lambda v: v and "alternate" in
                     (v if isinstance(v, str) else " ".join(v)).lower(),
                     type=re.compile(r"rss|atom", re.I))
    return urljoin(base, link["href"]) if link and link.get("href") else None


def _funder_from_feed(feed_url, cfg):
    out = []
    for e in _feed(feed_url, cfg["source"]):
        title = _clean_ws(e.get("title", ""))
        summary = BeautifulSoup(e.get("summary", ""), "html.parser").get_text(" ", strip=True)
        if len(title) < 8:
            continue
        if cfg.get("require_signal", True) and not _has_signal(title + " " + summary):
            continue
        out.append(_funder_item(title, e.get("link", ""), summary, cfg))
    return out


def _funder_from_html(html, page_url, cfg):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "nav", "header", "footer"]):
        t.decompose()
    host = _reg_root(urlparse(page_url).netloc)
    out = []
    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, a["href"].strip())
        pu = urlparse(href)
        if pu.scheme not in ("http", "https") or _reg_root(pu.netloc) != host:
            continue
        title = _clean_ws(a.get_text(" ", strip=True))
        if len(title) < 12 or title.lower() in _NAV_TEXT:
            continue
        low = (pu.path or "").lower()
        if not any(h in low for h in _DE_LINK_HINTS) and not _has_signal(title):
            continue
        # Climb to the block that carries this link's own call/deadline wording,
        # and stop before the container that holds the *other* listings —
        # otherwise every item inherits its neighbours' text and a news post
        # passes the signal check on the strength of the call next to it.
        text, node = title, a
        for _ in range(5):
            node = node.parent
            if node is None or len(node.find_all("a", href=True)) > 2:
                break
            t = _clean_ws(node.get_text(" ", strip=True))
            if len(t) > len(text):
                text = t
            if len(text) > 400:
                break
        if cfg.get("require_signal", True) and not _has_signal(text):
            continue
        out.append(_funder_item(title, href, text[:1200], cfg))
    return out


def _clean_ws(t):
    return re.sub(r"\s+", " ", t or "").strip()


def _has_signal(text):
    low = (text or "").lower()
    return any(sig in low for sig in _DE_CALL_SIGNALS)


def _funder_item(title, url, summary, cfg):
    item = {"title": title, "url": url, "summary": summary,
            "source": cfg["source"], "org": cfg.get("org", cfg["source"])}
    for k in ("region", "type", "country"):
        if cfg.get(k):
            item[k] = cfg[k]
    return item


def fetch_funder(cfg):
    """One funder site → items. Feed first, listing-page scan as the fallback."""
    url = cfg["url"]
    try:
        html = _get(url)
    except requests.RequestException:
        origin = "{0.scheme}://{0.netloc}/".format(urlparse(url))
        if origin.rstrip("/") == url.rstrip("/"):
            raise
        html = _get(origin)                  # configured path moved — try the root
        url = origin
    if _looks_like_bot_wall(html):
        raise RuntimeError(f"{cfg['source']} served a bot wall — needs a real browser")

    feed_url = cfg.get("feed") or _discover_feed(html, url)
    items = []
    if feed_url:
        try:
            items = _funder_from_feed(feed_url, cfg)
        except Exception:                    # feed dead or empty → scrape the page
            items = []
    if not items:
        items = _funder_from_html(html, url, cfg)
    if not items:
        raise RuntimeError(
            f"{cfg['source']}: no call-shaped links found at {url} "
            f"— check the page and set 'feed' or widen the config")
    return _dedupe_local(items)[:cfg.get("cap", 40)]


# Each entry becomes a registered source named by its key. `url` is the
# funding/news listing; `feed` overrides auto-discovery; region/type/org are
# defaults normalize.py would otherwise have to guess from German prose.
FUNDERS = {
    # --- Bund / national ---
    "kulturstiftungbund": {
        "source": "Kulturstiftung des Bundes", "org": "Kulturstiftung des Bundes",
        "url": "https://www.kulturstiftung-des-bundes.de/de/foerderung.html",
        "region": "DE", "country": "Germany", "type": "Grant"},
    "fondsdaku": {
        "source": "Fonds Darstellende Künste", "org": "Fonds Darstellende Künste",
        "url": "https://www.fonds-daku.de/foerderungen/",
        "region": "DE", "country": "Germany", "type": "Grant"},
    "fondssoziokultur": {
        "source": "Fonds Soziokultur", "org": "Fonds Soziokultur",
        "url": "https://www.fonds-soziokultur.de/foerderung/",
        "region": "DE", "country": "Germany", "type": "Grant"},
    "literaturfonds": {
        "source": "Deutscher Literaturfonds", "org": "Deutscher Literaturfonds",
        "url": "https://www.deutscher-literaturfonds.de/foerderung/",
        "region": "DE", "country": "Germany", "type": "Grant"},
    "kuenstlerbund": {
        "source": "Deutscher Künstlerbund", "org": "Deutscher Künstlerbund",
        "url": "https://www.kuenstlerbund.de/deutsch/ausschreibungen.html",
        "region": "DE", "country": "Germany"},
    "bbk": {
        "source": "bbk Bundesverband", "org": "bbk Bundesverband",
        "url": "https://www.bbk-bundesverband.de/aktuelles",
        "region": "DE", "country": "Germany"},
    "solitude": {
        "source": "Akademie Schloss Solitude", "org": "Akademie Schloss Solitude",
        "url": "https://www.akademie-solitude.de/en/fellowship/",
        "region": "DE", "country": "Stuttgart, Germany", "type": "Residency"},
    "scheringstiftung": {
        "source": "Schering Stiftung", "org": "Schering Stiftung",
        "url": "https://www.scheringstiftung.de/ausschreibungen/",
        "region": "DE", "country": "Berlin, Germany"},
    # --- Länder ---
    "hessischekulturstiftung": {
        "source": "Hessische Kulturstiftung", "org": "Hessische Kulturstiftung",
        "url": "https://www.hkst.de/foerderung/", "region": "DE",
        "country": "Hesse, Germany", "type": "Grant"},
    "kunststiftungnrw": {
        "source": "Kunststiftung NRW", "org": "Kunststiftung NRW",
        "url": "https://www.kunststiftungnrw.de/foerderung/",
        "region": "DE", "country": "Germany", "type": "Grant"},
    "kdfs": {
        "source": "Kulturstiftung Sachsen", "org": "Kulturstiftung des Freistaates Sachsen",
        "url": "https://www.kdfs.de/foerderung/", "region": "DE",
        "country": "Saxony, Germany", "type": "Grant"},
    "kunststiftungbw": {
        "source": "Kunststiftung BW", "org": "Kunststiftung Baden-Württemberg",
        "url": "https://www.kunststiftung.de/ausschreibungen/",
        "region": "DE", "country": "Germany", "type": "Grant"},
    # --- EU ---
    "creativeeuropede": {
        "source": "Creative Europe Desk DE", "org": "Creative Europe Desk KULTUR",
        "url": "https://www.creative-europe-desk.de/kultur/aktuelles",
        "region": "EU", "type": "Grant"},
}


# Hosts that are never the real application page: social/share widgets and
# framework/CDN/utility links that litter listing pages.
_SOCIAL_HOSTS = ("facebook.", "twitter.", "x.com", "instagram.", "linkedin.",
                 "youtube.", "youtu.be", "pinterest.", "tiktok.", "whatsapp.",
                 "t.me", "telegram.", "reddit.", "flickr.", "vimeo.", "threads.net",
                 "mastodon.", "bsky.", "wa.me", "api.whatsapp.com",
                 "messenger.com", "linktr.ee", "linktree.")
_UTILITY_HOSTS = ("google.", "gstatic.", "googleapis.", "gravatar.", "w.org",
                  "wp.com", "wordpress.org", "fonts.", "schema.org", "goo.gl",
                  "creativecommons.org", "addtoany.", "sharethis.", "gmpg.org",
                  "zendesk.", "intercom.", "hotjar.", "doubleclick.", "cookiebot.",
                  "gmail.", "mailchimp.", "list-manage.com",
                  "buy.stripe.com", "checkout.stripe.com", "paypal.", "gofundme.")
# Anchor-text cues, split by how much they actually promise. A "apply here"
# link names the application; a "website" link is usually the organiser's
# homepage, which is exactly the generic landing page this is meant to avoid.
_APPLY_CUES_STRONG = ("apply now", "apply here", "apply online", "how to apply",
                      "application form", "apply", "application", "submit",
                      "submission", "enter now", "register", "registration",
                      "open call", "call for", "full details", "guidelines",
                      "jetzt bewerben", "zur ausschreibung", "bewerbung",
                      "bewerben", "ausschreibung", "candidature", "postuler",
                      "convocatoria", "inscription", "iscrizione")
_APPLY_CUES_WEAK = ("more info", "more information", "further information",
                    "further info", "official", "website", "read more",
                    "find out more", "learn more", "link to", "visit the",
                    "details", "más información", "weitere informationen")
_APPLY_CUES = _APPLY_CUES_STRONG + _APPLY_CUES_WEAK
_NAV_TEXT = {"home", "about", "about us", "contact", "contact us", "privacy",
             "privacy policy", "cookie", "cookies", "newsletter", "subscribe",
             "log in", "login", "sign in", "sign up", "terms", "imprint",
             "impressum", "datenschutz", "menu", "search", "donate", "shop"}
# Path fragments that mark a URL as addressing a specific call rather than a
# site's front door. Matched against the path only — "residency.example.com"
# is still just a homepage.
_APPLY_PATH_CUES = ("apply", "application", "open-call", "opencall", "open_call",
                    "call-for", "callfor", "opportunit", "residenc", "submission",
                    "submit", "fellowship", "grant", "prize", "award",
                    "competition", "programme", "program", "bewerb", "ausschreib",
                    "stipendi", "convocatoria", "form", "openkall")


# Submission portals: the host itself *is* the application, so even a
# path-less URL on one (apply.foundation.org, a forms.gle short link) is a real
# application link rather than somebody's front page.
_PORTAL_HOSTS = ("submittable.com", "forms.gle", "jotform.com", "typeform.com",
                 "airtable.com", "surveymonkey.", "cognitoforms.", "wufoo.",
                 "formstack.", "smapply.io", "slideroom.com", "openwater.",
                 "artcall.org", "callforentry.org", "zapplication.org",
                 "submit.art", "opencall.")
_PORTAL_PATHS = ("docs.google.com/forms", "google.com/forms")
# Single-segment paths that are a site section, not a call: language switches,
# boilerplate pages. Anything else with a real slug is treated as a page.
_SECTION_PATHS = {"en", "de", "fr", "es", "it", "nl", "pt", "pl", "cz", "jp",
                  "bg", "eu", "us", "uk", "home", "index", "about", "about-us",
                  "contact", "kontakt", "news", "blog", "shop", "donate",
                  "support", "privacy", "imprint", "impressum", "datenschutz",
                  "ueber-uns", "search"}


def _is_application_portal(url):
    """True for a submission-platform URL (Submittable, JotForm, Google Forms,
    an `apply.` subdomain…) — the whole point of the host is the application."""
    pu = urlparse(url)
    host = pu.netloc.lower()
    hp = host + (pu.path or "")
    return (host.startswith(("apply.", "apply-", "submit.", "application."))
            or any(h in host for h in _PORTAL_HOSTS)
            or any(h in hp for h in _PORTAL_PATHS))


def _is_bare_root(url):
    """True for a site front door — 'https://example.org' or 'https://example.org/'."""
    pu = urlparse(url)
    if (pu.path or "").strip("/") or pu.query:
        return False
    return not _is_application_portal(url)


def is_specific_apply_url(url):
    """True when a URL addresses a page you could actually apply from.

    Homepages and bare section roots ('/es', '/en') fail: landing on one and
    hunting for the call down a menu is worse than landing on the listing page
    we already have, which at least describes it. Anything that names a call, a
    submission portal, or a real page slug passes.
    """
    if not url:
        return False
    pu = urlparse(url)
    if pu.scheme not in ("http", "https") or not pu.netloc:
        return False
    if _is_application_portal(url):
        return True
    path = (pu.path or "").strip("/").lower()
    if not path:
        return bool(pu.query)          # '/?p=1234' still addresses one post
    if any(c in path for c in _APPLY_PATH_CUES):
        return True
    if pu.query or path.count("/") >= 1:   # ?id=266, /en/news/<slug> and friends
        return True
    # one path segment: a page slug is fine, a bare section code is not
    return len(path) >= 3 and path not in _SECTION_PATHS


def is_usable_apply_url(url):
    """Would the extractor accept this URL as an application link today?

    Host rules and page-specificity in one predicate, so `reapply --prune` can
    re-check links captured under an older, looser rule without re-fetching
    every page.
    """
    if not is_specific_apply_url(url):
        return False
    host = urlparse(url).netloc.lower()
    if _is_application_portal(url):
        return True
    return not any(h in host for h in _SOCIAL_HOSTS + _UTILITY_HOSTS)


def _apply_link_score(href, text):
    """How strongly a link looks like *the* application page (higher = better)."""
    path = (urlparse(href).path or "").rstrip("/").lower()
    score = 0
    if any(c in text for c in _APPLY_CUES_STRONG):
        score += 3
    elif any(c in text for c in _APPLY_CUES_WEAK):
        score += 1
    if any(c in path for c in _APPLY_PATH_CUES):
        score += 2
    if path.count("/") >= 2:                             # a deep page, not a section
        score += 1
    if text.startswith(("http", "www.")):                # anchor text *is* a URL
        score += 1
    return score


_APPLY_ACCEPT = 3      # "apply"-ish anchor text, or a weak cue on a call-shaped URL


def _reg_root(host):
    """Rough registrable root, e.g. 'www.on-the-move.org' → 'on-the-move'. Good
    enough to tell 'a link that leaves this aggregator' from an internal one."""
    parts = host.lower().removeprefix("www.").split(".")
    return parts[-2] if len(parts) >= 2 else host


def _extract_apply_url(soup, page_url):
    """Best-effort real application link on an aggregator's listing page.

    Many sources (On the Move, culture360, Res Artis…) are intermediaries: their
    page summarises a call and links out to the organiser's own site where you
    actually apply. Find that outbound link so the dashboard's "Apply" button
    skips the middleman.

    Returns None unless the link clears two bars — it looks like an application
    link (score) *and* it addresses a specific page (is_specific_apply_url).
    Returning None is a good outcome: the caller falls back to the listing URL,
    which at least describes the call. An organiser's homepage does not, and
    handing one over as "the application page" is worse than not linking out.
    """
    page_root = _reg_root(urlparse(page_url).netloc)
    content = soup.find("main") or soup.find("article") or soup.body or soup
    best, best_score, externals = None, 0, {}
    for a in content.find_all("a", href=True):
        href = urljoin(page_url, a["href"].strip())
        pu = urlparse(href)
        if pu.scheme not in ("http", "https") or not pu.netloc:
            continue
        host = pu.netloc.lower()
        if _reg_root(host) == page_root:                     # stays on the aggregator
            continue
        if (any(h in host for h in _SOCIAL_HOSTS + _UTILITY_HOSTS)
                and not _is_application_portal(href)):
            continue
        txt = a.get_text(" ", strip=True).lower()
        if not txt or (txt in _NAV_TEXT and not any(c in txt for c in _APPLY_CUES)):
            continue                                         # bare logo / nav chrome
        if _is_bare_root(href):
            # remember the host so the single-outbound-host fallback still knows
            # the organiser, but a front door is never the application page
            externals.setdefault(host, None)
            continue
        if not externals.get(host):
            externals[host] = href
        score = _apply_link_score(href, txt)
        # prefer a higher score, then the more specific (deeper) URL
        if score > best_score or (score == best_score and score and best
                                  and href.count("/") > best.count("/")):
            best_score, best = score, href
    if best_score >= _APPLY_ACCEPT and is_specific_apply_url(best):
        return best
    # exactly one outbound organiser, and we found a real page on it (not just
    # their front door) → that page is almost surely where the call lives
    pages = [u for u in externals.values() if u and is_specific_apply_url(u)]
    if len(externals) == 1 and len(pages) == 1:
        return pages[0]
    return None


def fetch_detail(url):
    """Fetch a call's own page; return (readable_text, apply_url).

    Used by `update`'s enrich step: listing blurbs rarely say what an
    application asks for (CV, portfolio, fee…) or even the deadline — the
    detail page usually does. Strips chrome (nav/header/footer/scripts) and
    prefers the <main>/<article> region when the page marks one. Also digs out
    the organiser's real application link (see _extract_apply_url) before the
    chrome is thrown away, so aggregators don't strand you on their own page.
    """
    html = _get(url)
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    # drop chrome first so footer/sidebar sponsor links can't pose as the apply link
    for t in soup(["nav", "header", "footer", "aside", "form"]):
        t.decompose()
    apply_url = _extract_apply_url(soup, url)
    node = soup.find("main") or soup.find("article") or soup.body or soup
    text = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
    return text[:6000], apply_url


def _dedupe_local(items):
    seen, out = set(), []
    for it in items:
        k = it["url"] or it["title"]
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


# Registry: name -> callable. Toggle what runs from the CLI with --sources.
SOURCES = {
    "colossal":     fetch_colossal,
    "eflux":        fetch_eflux,
    "hyperallergic": fetch_hyperallergic,
    "resartis":     fetch_resartis,
    "onthemove":    fetch_onthemove,
    "kunstfonds":   fetch_kunstfonds,
    "transartists": fetch_transartists,
    "artconnect":   fetch_artconnect,
    "culture360":   fetch_culture360,
    # sound / media-art funders & festivals (SYMBIONT-tuned)
    "initiativemusik": fetch_initiativemusik,
    "musikfonds":   fetch_musikfonds,
    "zkm":          fetch_zkm,
    "ctm":          fetch_ctm,
    "arselectronica": fetch_arselectronica,
}

# German / EU funders: one generic fetcher, one registry entry per site.
for _name, _cfg in FUNDERS.items():
    SOURCES[_name] = functools.partial(fetch_funder, _cfg)
