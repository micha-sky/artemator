"""
normalize.py — turn messy raw items from each source into a common schema.

Everything here is heuristic. Region/type/funded are best-effort guesses from
keywords; deadline is parsed from the text near words like "deadline"/"closes".
Treat the output as triage, not gospel — always confirm on the source page.
"""
import re
import hashlib
from datetime import datetime, date
from dateutil import parser as dparser

import geo

# --- Creative Europe / European country set (for region tagging) ---
DE_WORDS = {"germany", "deutschland", "german", "berlin", "hamburg", "munich",
            "münchen", "cologne", "köln", "frankfurt", "stuttgart", "hesse",
            "hessen", "leipzig", "dresden"}

EU_COUNTRIES = {
    "albania","austria","armenia","belgium","bosnia","bulgaria","croatia","cyprus",
    "czech","czechia","denmark","estonia","finland","france","georgia","greece",
    "hungary","iceland","ireland","italy","kosovo","latvia","liechtenstein",
    "lithuania","luxembourg","malta","moldova","montenegro","netherlands",
    "north macedonia","macedonia","norway","poland","portugal","romania","serbia",
    "slovakia","slovenia","spain","sweden","tunisia","ukraine","europe","european",
    "amsterdam","paris","london","madrid","lisbon","vienna","brussels","rome",
    "milan","stockholm","copenhagen","oslo","helsinki","zurich","geneva","uk",
    "united kingdom","switzerland","swiss",
}

# Discipline / medium tags. An item can match several (a call can be open to
# painting *and* sculpture), so guess_disciplines() returns a list. Matched with
# word boundaries to avoid false hits (e.g. "sound" inside "resounding").
DISCIPLINE_RULES = [
    ("Painting",          ["painting", "painter", "painters", "malerei"]),
    ("Drawing",           ["drawing", "drawings", "illustration", "illustrator", "zeichnung"]),
    ("Sculpture",         ["sculpture", "sculptor", "sculptural", "skulptur"]),
    ("Photography",       ["photography", "photographer", "photographic", "fotografie", "photobook"]),
    ("Film/Video",        ["film", "filmmaker", "filmmaking", "video", "cinema", "cinematic", "documentary", "animation", "moving image"]),
    ("Sound/Music",       ["sound", "sonic", "music", "musical", "musician", "composer", "composition", "audio", "field recording"]),
    ("Performance",       ["performance", "performing", "dance", "dancer", "choreography", "choreographer", "theatre", "theater", "live art", "opera"]),
    ("Writing",           ["writing", "writer", "literature", "literary", "poetry", "poet", "fiction", "nonfiction", "essay", "essays", "novel", "playwright"]),
    ("Digital/New Media", ["digital", "new media", "net art", "generative", "interactive", "video game", "game art", "virtual reality", "augmented reality", "electronic art", "creative coding", "software art"]),
    ("Printmaking",       ["printmaking", "printmaker", "etching", "lithography", "lithograph", "screenprint", "screen print", "engraving", "risograph"]),
    ("Craft/Textile",     ["textile", "textiles", "fiber art", "fibre art", "weaving", "embroidery", "ceramic", "ceramics", "glass art", "jewellery", "jewelry", "woodwork", "craft"]),
    ("Curatorial",        ["curator", "curatorial", "curating"]),
]

# Application materials a call may ask for. Matched with word boundaries like
# disciplines; a call can require several. Listing blurbs rarely spell these
# out — the detail-page text fetched by `update`'s enrich step is what usually
# populates them.
REQUIREMENT_RULES = [
    ("CV",                ["cv", "curriculum vitae", "resume", "résumé", "lebenslauf"]),
    ("Portfolio",         ["portfolio", "arbeitsproben"]),
    ("Artist statement",  ["artist statement", "artist's statement", "artists statement"]),
    ("Work samples",      ["work sample", "images of your work", "image files", "jpeg", "jpg", "bildmaterial", "documentation of work"]),
    ("Project proposal",  ["project proposal", "project description", "project plan", "proposal", "concept note", "projektbeschreibung", "projektskizze", "exposé"]),
    ("Motivation letter", ["cover letter", "letter of motivation", "motivation letter", "letter of intent", "motivationsschreiben", "anschreiben"]),
    ("References",        ["letter of recommendation", "letters of recommendation", "reference letter", "referee", "empfehlungsschreiben"]),
    ("Budget",            ["budget", "kostenplan", "finanzierungsplan"]),
    ("Application form",  ["application form", "online form", "entry form", "submission form", "online portal", "bewerbungsformular", "antragsformular"]),
]

TYPE_RULES = [
    ("Residency", ["residency", "residencies", "résidence", "residence", "atelier", "artist-in-residence", "air "]),
    ("Grant",     ["grant", "stipend", "stipendium", "stipendien", "fellowship", "bursary", "förder", "funding", "scholarship"]),
    ("Mobility",  ["mobility", "travel grant", "touring"]),
    ("Prize",     ["prize", "award", "preis", "competition", "biennial", "biennale"]),
    ("Open Call", ["open call", "call for", "exhibition", "juried", "submissions"]),
]

FUNDED_POS = ["stipend", "stipendium", "bursary", "fully funded", "fully-funded",
              "covers travel", "accommodation provided", "daily allowance",
              "monthly allowance", "honorarium", "honoraria", "production budget",
              "materials budget", "flights", "airfare", "living costs", "no fee",
              "free of charge", "€", "eur ", "usd", "$", "£"]
FEE_WORDS  = ["application fee", "entry fee", "submission fee", "participation fee",
              "tuition", "fee to apply", "self-funded", "self funded"]

# Explicit "free to apply" signals → fee_eur = 0.
NO_FEE_PHRASES = ["no fee", "no application fee", "no entry fee", "no submission fee",
                  "no participation fee", "free to apply", "free to enter",
                  "free to submit", "free of charge", "no cost to apply",
                  "kostenlos", "gebührenfrei", "keine gebühr", "keine bewerbungsgebühr"]

# money amounts, with the currency marker on either side: €30 / 30€ / 30 EUR / USD 25
_MONEY_RE = re.compile(
    r"([€$£])\s?(\d{1,4}(?:[.,]\d{2})?)(?!\d)"
    r"|(\d{1,4}(?:[.,]\d{2})?)\s?([€$£])"
    r"|(\d{1,4}(?:[.,]\d{2})?)\s?(eur|usd|gbp|euros?|dollars?|pounds?)\b"
    r"|\b(eur|usd|gbp)\s?(\d{1,4}(?:[.,]\d{2})?)(?!\d)",
    re.IGNORECASE)
# fixed rough conversion — this is triage, not accounting
_TO_EUR = {"€": 1.0, "eur": 1.0, "euro": 1.0, "euros": 1.0,
           "$": 0.9, "usd": 0.9, "dollar": 0.9, "dollars": 0.9,
           "£": 1.15, "gbp": 1.15, "pound": 1.15, "pounds": 1.15}

# Career-stage vocabulary. "emerging" wins over "established" when both appear
# ("open to emerging and mid-career artists" IS open to an emerging artist).
EMERGING_WORDS = ["emerging", "early career", "early-career", "young artist",
                  "young artists", "young creatives", "recent graduate",
                  "recent graduates", "up-and-coming", "under 30", "under 35",
                  "nachwuchs", "débutant", "debut"]
ESTABLISHED_WORDS = ["mid-career", "mid career", "established artist",
                     "established artists", "established practice",
                     "professional track record", "extensive exhibition history"]
_YEARS_REQ_RE = re.compile(
    r"(?:at least|minimum(?: of)?|min\.?)\s+\d+\s+years?", re.IGNORECASE)

DEADLINE_CUES = ["deadline", "closes", "closing", "apply by", "applications close",
                 "until", "submit by", "bewerbungsschluss", "einsendeschluss", "frist"]

DATE_RE = re.compile(
    r"(\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{4})"
    r"|((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})"
    r"|(\d{4}-\d{2}-\d{2})"
    r"|(\d{1,2}[./]\d{1,2}[./]\d{4})",
    re.IGNORECASE,
)


def _clean(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "")).strip()


# Words that mark an item as an actual opportunity worth tracking, used by
# is_relevant() to reject noise (e.g. museum exhibition notices) that slips
# through broad HTML scrapers. Only consulted for items typed "Other": since
# that type means guess_type() already found no TYPE_RULES keyword, the live
# signals here are the application/funding vocabularies, not the type keywords.
OPP_SIGNALS = (
    ["open call", "call for", "apply", "application", "applications", "submit",
     "submission", "eligible", "eligibility", "nominate", "nomination",
     "ausschreibung", "bewerbung", "bewerbungen", "einreichung"]
    + DEADLINE_CUES + FUNDED_POS
)

# Common scraper boilerplate/nav labels that broad selectors pick up as "calls".
BOILERPLATE_TITLES = {
    "skip to main content", "skip to content", "direkt zum inhalt",
    "gebärdensprache", "leichte sprache", "menu", "main navigation",
    "search", "newsletter", "home", "accessibility", "aktuelles",
}


def is_relevant(n: dict) -> bool:
    """Keep-or-drop gate applied before storing a normalized item.

    Drops (a) obvious scraper boilerplate (nav links, in-page anchors),
    (b) anything whose parsed deadline is already in the past — an expired call
    is noise for a tracker — and (c) generic items ("Other" type) that show no
    application/funding signal at all. Conservative on purpose: anything with a
    real opportunity type or a future deadline is otherwise always kept.
    """
    title = (n.get("title") or "").strip().lower()
    url = (n.get("url") or "").strip()
    if not title or title in BOILERPLATE_TITLES:
        return False
    if not url or url.startswith("#"):  # bare in-page fragment → not a real listing
        return False
    dl = n.get("deadline")
    if dl:
        try:
            if date.fromisoformat(dl) < date.today():
                return False
        except (ValueError, TypeError):
            pass  # unparseable deadline → don't reject on freshness grounds
    if n.get("type") != "Other":
        return True
    blob = (n.get("title", "") + " " + n.get("summary", "")).lower()
    return any(k in blob for k in OPP_SIGNALS)


def stable_id(url: str, title: str) -> str:
    key = (url or "").strip().lower() or re.sub(r"[^a-z0-9]", "", (title or "").lower())
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def guess_region(text: str) -> str:
    t = (text or "").lower()
    if any(w in t for w in DE_WORDS):
        return "DE"
    if any(w in t for w in EU_COUNTRIES):
        return "EU"
    return "Intl"


def guess_disciplines(text: str) -> list:
    """Return the list of discipline tags whose keywords appear in the text."""
    t = (text or "").lower()
    out = []
    for label, kws in DISCIPLINE_RULES:
        # trailing "s?" catches the domain's common "call for X-ers/-ors/-ists"
        # plural phrasing (poets, sculptors, musicians…) from the singular nouns
        if any(re.search(r"\b" + re.escape(k) + r"s?\b", t) for k in kws):
            out.append(label)
    return out


def extract_requirements(text: str) -> list:
    """Return the list of application materials whose keywords appear in the text."""
    t = (text or "").lower()
    out = []
    for label, kws in REQUIREMENT_RULES:
        if any(re.search(r"\b" + re.escape(k) + r"s?\b", t) for k in kws):
            out.append(label)
    # a fee is something you "apply with" too — reuse the funding vocabulary
    if any(w in t for w in FEE_WORDS):
        out.append("Entry fee")
    return out


def guess_type(text: str) -> str:
    t = (text or "").lower()
    for label, kws in TYPE_RULES:
        if any(k in t for k in kws):
            return label
    return "Other"


def guess_funded(text: str) -> str:
    t = (text or "").lower()
    has_fee = any(w in t for w in FEE_WORDS)
    has_pos = any(w in t for w in FUNDED_POS)
    if has_pos and not has_fee:
        return "likely"
    if has_fee and not has_pos:
        return "fee-based"
    if has_pos and has_fee:
        return "mixed"
    return "unknown"


def extract_fee(text: str):
    """Application fee in EUR: a number, 0 for explicitly free, None for no signal.

    An amount only counts as a fee when it sits within ~70 chars of a fee word
    ("application fee €30", "entry fee of $25") — otherwise grant/stipend sums
    would be misread as fees. Multiple candidates → the smallest (fee lists
    like "€15 members / €30 others" should filter optimistically)."""
    t = (text or "").lower()
    if not t:
        return None
    candidates = []
    for m in _MONEY_RE.finditer(t):
        g = m.groups()
        cur, num = (g[0], g[1]) if g[0] else (g[3], g[2]) if g[3] else \
                   (g[5], g[4]) if g[5] else (g[6], g[7])
        window = t[max(0, m.start() - 70): m.end() + 40]
        if not any(w in window for w in ("fee", "gebühr")):
            continue
        try:
            val = float(num.replace(",", "."))
        except ValueError:
            continue
        eur = round(val * _TO_EUR.get(cur.lower(), 1.0))
        if 0 < eur <= 1000:   # >1000 near "fee" is almost certainly not an application fee
            candidates.append(eur)
    if candidates:
        return min(candidates)
    if any(p in t for p in NO_FEE_PHRASES):
        return 0
    return None


def guess_career_stage(text: str) -> str:
    """'emerging' / 'established' / 'any'. Emerging-friendly calls win ties."""
    t = (text or "").lower()
    if any(w in t for w in EMERGING_WORDS):
        return "emerging"
    if any(w in t for w in ESTABLISHED_WORDS) or _YEARS_REQ_RE.search(t):
        return "established"
    return "any"


def _jitter(opp_id: str, scale: float):
    """Small deterministic offset per item so same-place pins don't stack."""
    h = int(hashlib.sha1((opp_id or "").encode("utf-8")).hexdigest()[8:16], 16)
    dx = ((h & 0xffff) / 0xffff - 0.5) * scale
    dy = (((h >> 16) & 0xffff) / 0xffff - 0.5) * scale
    return dx, dy


def guess_location(text: str, opp_id: str = "") -> dict:
    """Country / region groups / map coords via the offline gazetteer.

    Returns {} when nothing matched. Country-centroid pins get a wider jitter
    than city pins — they're approximate anyway, and spreading them keeps a
    country's calls individually clickable on the map. Umbrella-phrase-only
    hits ("open to ASEAN artists") carry region groups but no pin."""
    loc = geo.locate(text)
    if not loc:
        return {}
    out = {
        "country": loc["country"],
        "place": loc["place"],
        "region_group": ", ".join(loc["groups"]),
        "lat": None,
        "lon": None,
    }
    if loc["lat"] is not None:
        dx, dy = _jitter(opp_id, 0.08 if loc["precise"] else 1.0)
        out["lat"] = round(loc["lat"] + dx, 4)
        out["lon"] = round(loc["lon"] + dy, 4)
    return out


def extract_amount(text: str) -> str:
    m = re.search(r"([€$£]\s?\d[\d.,]*\s?(?:/\s?(?:day|month|week|year|mo|yr))?)", text or "")
    if m:
        return _clean(m.group(1))
    m = re.search(r"(\d[\d.,]*\s?(?:eur|usd|gbp))", (text or ""), re.IGNORECASE)
    return _clean(m.group(1)) if m else ""


def extract_deadline(text: str):
    """Return ISO date string (YYYY-MM-DD) of the most likely deadline, or None."""
    if not text:
        return None
    low = text.lower()
    candidates = []
    for m in DATE_RE.finditer(text):
        span_start = m.start()
        window = low[max(0, span_start - 60): span_start]  # words just before the date
        near_cue = any(cue in window for cue in DEADLINE_CUES)
        raw = _clean(next(g for g in m.groups() if g))
        try:
            dt = dparser.parse(raw, dayfirst=True, fuzzy=True).date()
        except (ValueError, OverflowError):
            continue
        candidates.append((near_cue, dt))
    if not candidates:
        return None
    today = date.today()
    future = [(cue, d) for cue, d in candidates if d >= today]
    pool = future or candidates
    # prefer dates near a deadline cue, then the earliest upcoming
    pool.sort(key=lambda x: (not x[0], x[1]))
    return pool[0][1].isoformat()


def normalize(raw: dict) -> dict:
    """raw keys expected: title, url, summary, source, (optional) country, published."""
    title = _clean(raw.get("title"))
    summary = _clean(raw.get("summary"))
    blob = " ".join(filter(None, [title, summary, raw.get("country", "")]))
    opp_id = stable_id(raw.get("url", ""), title)
    loc = guess_location(blob, opp_id)
    # legacy coarse region: prefer the gazetteer country, fall back to keywords
    if loc and loc["country"]:
        region = "DE" if loc["country"] == "DE" else \
                 "EU" if loc["country"] in geo.EUROPE_ISO else "Intl"
    else:
        region = guess_region(blob)
    return {
        "id": opp_id,
        "title": title,
        "org": _clean(raw.get("org", "")),
        "url": _clean(raw.get("url", "")),
        "source": raw.get("source", "?"),
        "summary": summary[:400],
        "deadline": extract_deadline(blob) or (raw.get("deadline") or None),
        "region": raw.get("region") or region,
        "type": raw.get("type") or guess_type(blob),
        "funded": guess_funded(blob),
        "amount": extract_amount(blob),
        "discipline": ", ".join(guess_disciplines(blob)),
        "requirements": ", ".join(extract_requirements(blob)),
        "country": loc.get("country", ""),
        "place": loc.get("place", ""),
        "region_group": loc.get("region_group", ""),
        "lat": loc.get("lat"),
        "lon": loc.get("lon"),
        "fee_eur": extract_fee(blob),
        "career_stage": guess_career_stage(blob),
    }
