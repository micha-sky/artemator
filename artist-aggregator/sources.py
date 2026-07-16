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
    """Res Artis open calls — deadline + country render in list rows."""
    html = _get("https://resartis.org/open-calls/")
    soup = BeautifulSoup(html, "html.parser")
    out = []
    # TUNE: inspect the actual list item wrapper; this targets anchor cards.
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


def fetch_onthemove():
    """On the Move deadlines page — entries carry country + deadline in the text."""
    html = _get("https://on-the-move.org/news/deadlines")
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.select("a[href]"):  # TUNE: narrow to the deadline-list container
        title = a.get_text(" ", strip=True)
        parent_text = a.find_parent().get_text(" ", strip=True) if a.find_parent() else title
        if "deadline" not in parent_text.lower() or len(title) < 10:
            continue
        href = a["href"]
        if href.startswith("/"):
            href = "https://on-the-move.org" + href
        out.append({"title": title, "url": href,
                    "summary": parent_text, "source": "On the Move"})
    return _dedupe_local(out)


def fetch_kunstfonds():
    """Stiftung Kunstfonds — German federal visual-arts funding announcements."""
    html = _get("https://www.kunstfonds.de/aktuelles/")
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for card in soup.select("article, .news-item, a[href]"):  # TUNE
        a = card if card.name == "a" else card.find("a", href=True)
        if not a or not a.get("href"):
            continue
        title = a.get_text(" ", strip=True)
        if len(title) < 8:
            continue
        href = a["href"]
        if href.startswith("/"):
            href = "https://www.kunstfonds.de" + href
        out.append({"title": title, "url": href, "org": "Stiftung Kunstfonds",
                    "summary": card.get_text(" ", strip=True), "source": "Kunstfonds",
                    "region": "DE"})
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
