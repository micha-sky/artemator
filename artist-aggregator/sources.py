"""
sources.py — one fetcher per source. Each returns a list of raw dicts:
    {title, url, summary, source, country?}

RSS sources are reliable. HTML scrapers depend on each site's markup, which
changes over time — the CSS selectors marked "TUNE" are the bits you'll adjust
on the first live run (open the page, inspect, fix the selector). Every fetcher
is wrapped so one broken source never kills the whole run.
"""
import json
import re
import subprocess
import time
from urllib.parse import unquote

import requests
import feedparser
from bs4 import BeautifulSoup

import geo

# ISO code -> primary country name, for sources that ship codes ("CA") instead
# of names: the gazetteer matches names, so translate before building summaries.
_ISO_NAME = {iso: names[0].title() for iso, (names, _c, _g) in geo.COUNTRIES.items()}

HEADERS = {"User-Agent": "artist-aggregator/1.0 (personal opportunity tracker)"}
TIMEOUT = 20


def _get(url):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def _get_lax(url):
    """Like _get but ignores the status code. resartis.org serves its
    wp-sitemap sub-files with a 404 status and the real XML in the body."""
    return requests.get(url, headers=HEADERS, timeout=TIMEOUT).text


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

def fetch_colossal():
    """This Is Colossal — dedicated monthly 'Opportunities' roundup feed.

    Each roundup post lists many calls in its body; we emit the post itself.
    """
    feed = feedparser.parse("https://www.thisiscolossal.com/category/opportunities/feed/")
    return [{"title": e.get("title", ""), "url": e.get("link", ""),
             "summary": BeautifulSoup(e.get("summary", ""), "html.parser").get_text(" ", strip=True),
             "source": "Colossal"} for e in feed.entries]


def fetch_eflux():
    feed = feedparser.parse("https://www.e-flux.com/announcements/feed/")
    return [{"title": e.get("title", ""), "url": e.get("link", ""),
             "summary": BeautifulSoup(e.get("summary", ""), "html.parser").get_text(" ", strip=True),
             "source": "e-flux"} for e in feed.entries]


def fetch_hyperallergic():
    """Hyperallergic — dedicated 'Opportunities' tag feed (open calls, grants,
    fellowships, residencies). Reliable RSS."""
    feed = feedparser.parse("https://hyperallergic.com/tag/opportunities/feed/")
    return [{"title": e.get("title", ""), "url": e.get("link", ""),
             "summary": BeautifulSoup(e.get("summary", ""), "html.parser").get_text(" ", strip=True),
             "source": "Hyperallergic"} for e in feed.entries]


# ---------- HTML scrapers (TUNE selectors on first live run) ----------

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
    index = _get("https://resartis.org/wp-sitemap.xml")
    oc_maps = re.findall(r"https://resartis\.org/wp-sitemap-posts-open_call-\d+\.xml", index)
    if not oc_maps:
        raise RuntimeError("no open_call sitemap found — sitemap layout changed?")
    urls = []
    for sm in oc_maps[-2:]:                       # last two files cover the newest posts
        # _get_lax: these sub-sitemaps come back with a 404 status + real XML body
        urls += re.findall(r"<loc>(https://resartis\.org/open-call/[^<]+)</loc>", _get_lax(sm))
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


def fetch_detail(url):
    """Fetch a call's own page and return its readable text, best-effort.

    Used by `update`'s enrich step: listing blurbs rarely say what an
    application asks for (CV, portfolio, fee…) or even the deadline — the
    detail page usually does. Strips chrome (nav/header/footer/scripts) and
    prefers the <main>/<article> region when the page marks one.
    """
    html = _get(url)
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "nav", "header", "footer", "aside", "noscript", "form"]):
        t.decompose()
    node = soup.find("main") or soup.find("article") or soup.body or soup
    text = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
    return text[:6000]


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
