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
# through broad HTML scrapers. Complements TYPE_RULES: an item typed "Other"
# is kept only if it still carries one of these application/funding signals.
OPP_SIGNALS = (
    ["open call", "call for", "apply", "application", "applications", "submit",
     "submission", "eligible", "eligibility", "nominate", "nomination",
     "ausschreibung", "bewerbung", "bewerbungen", "einreichung"]
    + DEADLINE_CUES + [k for _, kws in TYPE_RULES for k in kws] + FUNDED_POS
)


def is_relevant(n: dict) -> bool:
    """Keep-or-drop gate applied before storing a normalized item.

    Drops (a) anything whose parsed deadline is already in the past — an expired
    call is noise for a tracker — and (b) generic items ("Other" type) that show
    no application/funding signal at all. Conservative on purpose: anything with
    a real opportunity type or a future deadline is always kept.
    """
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
    return {
        "id": stable_id(raw.get("url", ""), title),
        "title": title,
        "org": _clean(raw.get("org", "")),
        "url": _clean(raw.get("url", "")),
        "source": raw.get("source", "?"),
        "summary": summary[:400],
        "deadline": extract_deadline(blob) or (raw.get("deadline") or None),
        "region": raw.get("region") or guess_region(blob),
        "type": raw.get("type") or guess_type(blob),
        "funded": guess_funded(blob),
        "amount": extract_amount(blob),
    }
