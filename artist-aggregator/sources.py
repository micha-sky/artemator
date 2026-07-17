"""
sources.py — one fetcher per source. Each returns a list of raw dicts:
    {title, url, summary, source, country?}

RSS sources are reliable. HTML scrapers depend on each site's markup, which
changes over time — the CSS selectors marked "TUNE" are the bits you'll adjust
on the first live run (open the page, inspect, fix the selector). Every fetcher
is wrapped so one broken source never kills the whole run.
"""
import requests
import feedparser
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "artist-aggregator/1.0 (personal opportunity tracker)"}
TIMEOUT = 20


def _get(url):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


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

def fetch_resartis():
    """Res Artis open calls.

    The open-calls page sits behind an sgcaptcha bot-challenge that serves a
    182-byte meta-refresh instead of listings, so plain HTTP can't read it.
    Detect the challenge and fail loudly rather than emit garbage — the health
    strip then shows this source as broken instead of silently empty.
    """
    html = _get("https://resartis.org/open-calls/")
    if "sgcaptcha" in html or "http-equiv=\"refresh\"" in html.lower() or len(html) < 1000:
        raise RuntimeError("bot-challenge (sgcaptcha) — needs a real browser")
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for card in soup.select("article, .open-call, .listing, li"):
        a = card.find("a", href=True)
        if not a:
            continue
        title = a.get_text(" ", strip=True)
        if len(title) < 6:
            continue
        text = card.get_text(" ", strip=True)
        if "deadline" not in text.lower():
            continue
        out.append({"title": title, "url": a["href"],
                    "summary": text, "source": "Res Artis"})
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
}
