# Seed a realistic sample so dashboard.html renders before the first live `update`.
# Rows run through normalize() so every derived field (country, region groups,
# coordinates, fee, career stage…) is filled the same way live data is.
import sqlite3
from datetime import datetime, timedelta

import store
from normalize import normalize

store.init()
now = datetime.utcnow()
recent = (now - timedelta(days=2)).isoformat(timespec="seconds")   # -> shows NEW badge
older  = (now - timedelta(days=40)).isoformat(timespec="seconds")

# (title, org, url, source, summary, type_hint, first_seen)
SAMPLE = [
 ("Culture Moves Europe — Individual Mobility", "Creative Europe / Goethe-Institut",
  "https://www.goethe.de/en/kul/foe/cmm.html", "On the Move",
  "Rolling monthly mobility grant, €85/day + travel. Reopens autumn 2026.",
  "Mobility", older),
 ("KUNSTFONDS_Stipendium 2027", "Stiftung Kunstfonds", "https://www.kunstfonds.de",
  "Kunstfonds",
  "Stipendium 18.000 EUR for freelance visual artists in Germany. Deadline 15 January 2027.",
  "Grant", older),
 ("Rijksakademie Residency 2028", "Rijksakademie", "https://rijksakademie.nl",
  "On the Move",
  "Two-year Amsterdam residency, monthly stipend €19,800/yr + materials. Deadline 1 February 2027.",
  "Residency", recent),
 ("Cemeti Institute Residency — Yogyakarta", "Cemeti Institute", "https://example.org/cemeti",
  "Res Artis",
  "Three-month residency for emerging artists in Yogyakarta, Indonesia. Painting and sculpture studios, no application fee, stipend provided. Deadline 25 September 2026.",
  "Residency", recent),
 ("Nida Art Colony Autumn Residency", "Vilnius Academy of Arts", "https://example.org/nida",
  "TransArtists",
  "Residency in Nida, Lithuania for visual artists and writers. Application fee €20, accommodation provided. Deadline 10 October 2026.",
  "Residency", recent),
 ("Belgrade AIR — emerging sculptors", "Belgrade Art Space", "https://example.org/belgrade",
  "TransArtists",
  "Two-month residency in Belgrade, Serbia for early-career sculptors. Free to apply, production budget included. Deadline 18 September 2026.",
  "Residency", recent),
 ("Fondazione Studio Residency 2026", "Studio Bocconi", "https://example.org/it",
  "Res Artis",
  "Residency in Milan, Italy for established artists with at least 5 years of professional practice. Application fee €30. Deadline 5 September 2026.",
  "Residency", recent),
 ("Nomadic Art Camp — Ulaanbaatar", "Mongolian Contemporary Art Support", "https://example.org/mongolia",
  "ArtConnect",
  "Four-week nomadic residency around Ulaanbaatar, Mongolia for emerging painters and writers. No application fee; travel stipend. Deadline 30 October 2026.",
  "Residency", recent),
 ("Emerging Painters Prize 2026", "A Foundation", "https://example.org/prize", "e-flux",
  "International award for early-career painters, €10,000. Deadline 12 October 2026.",
  "Prize", recent),
 ("Open Studio juried exhibition", "City Gallery, USA", "https://example.org/us", "Colossal",
  "Open call, submissions. $25 entry fee. Deadline 30 August 2026. Ohio, USA.",
  "Open Call", older),
]

items = []
for title, org, url, srcn, summ, typ, seen in SAMPLE:
    n = normalize({"title": title, "org": org, "url": url, "summary": summ,
                   "source": srcn, "type": typ})
    items.append((n, seen))
store.upsert_many([n for n, _ in items])

# controlled first_seen so the NEW badge demo works
c = sqlite3.connect(store.DB_PATH)
for n, seen in items:
    c.execute("UPDATE opportunities SET first_seen=? WHERE id=?", (seen, n["id"]))
c.commit(); c.close()

print("seeded + exported", store.export(), "rows")
