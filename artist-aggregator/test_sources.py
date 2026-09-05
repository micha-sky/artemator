"""
test_sources.py — offline checks for the bits that decide what the dashboard's
"Apply" button points at, and for the Res Artis sitemap parsing.

No network: every case is a fixture. Run with `python test_sources.py`.
"""
import sys

from bs4 import BeautifulSoup

import sources as src


FAILS = []


def check(cond, label):
    if not cond:
        FAILS.append(label)
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")


def apply_url(html, page_url):
    return src._extract_apply_url(BeautifulSoup(html, "html.parser"), page_url)


LISTING = "https://resartis.org/open-call/some-residency/"

print("is_specific_apply_url — homepages and section roots are not applications")
for bad in ["https://vhaward.com", "https://www.muuscollection.com",
            "http://proyectolazona.com/", "https://residency.hubfeenix.fi",
            "https://artestudioginestrelle.wordpress.com/",
            "https://uap-athens.com/es/", "https://merz.gallery",
            "https://www.hkst.de/"]:
    check(not src.is_specific_apply_url(bad), f"reject {bad}")
for bad in ["https://socialdive.jp/en/", "https://www.cityu.edu.hk/bg/",
            "https://organiser.org/about", "https://organiser.org/contact"]:
    check(not src.is_specific_apply_url(bad), f"reject section root {bad}")
for good in ["https://www.chalkhillresidency.com/apply",
             "https://buinho.pt/residency-2/",
             "https://www.kac.or.jp/en/news/20260615/",
             "https://aca.submittable.com/submit",
             "https://www.veneziacontemporanea.com/the-la-storta-exhibition-space-venice/",
             "https://example.org/?p=1234",
             "https://sgiff.com/pitching-forum/",
             "https://www.lawayakacurrent.com/tropic",
             "https://unidee.cittadellarte.it/activity.html?id=266",
             "https://www.altart.cz/rezidence-2027/?lang=en"]:
    check(src.is_specific_apply_url(good), f"accept {good}")

print("\nsubmission portals count as applications even without a path")
for portal in ["https://apply.albeefoundation.org/",
               "https://forms.gle/dhHwMYunjEwq1VPb8",
               "https://form.jotform.com/261923916195061",
               "https://docs.google.com/forms/d/e/1FAIpQ/viewform",
               "https://foundation.submittable.com/submit"]:
    check(src.is_specific_apply_url(portal), f"accept portal {portal}")
check(src._extract_apply_url(BeautifulSoup(
    '<main><p><a href="https://docs.google.com/forms/d/e/1FAIpQ/viewform">'
    'Application form</a></p></main>', "html.parser"), LISTING)
    == "https://docs.google.com/forms/d/e/1FAIpQ/viewform",
    "a Google Form survives the utility-host filter")
check(src._extract_apply_url(BeautifulSoup(
    '<main><p><a href="https://buy.stripe.com/14kcP610p7VO532cMM">Pay the fee</a>'
    '</p></main>', "html.parser"), LISTING) is None,
    "a payment checkout link is not an application page")

print("\nis_usable_apply_url — what `reapply --prune` keeps")
check(not src.is_usable_apply_url("https://buy.stripe.com/14kcP610p7VO532cMM"),
      "a stored Stripe checkout link is pruned")
check(not src.is_usable_apply_url("https://wa.me/4915259430884"),
      "a stored WhatsApp link is pruned")
check(src.is_usable_apply_url("https://forms.gle/dhHwMYunjEwq1VPb8"),
      "a stored Google Forms link is kept")
check(src.is_usable_apply_url("https://sgiff.com/pitching-forum/"),
      "a stored call page is kept")

print("\n_extract_apply_url — an organiser homepage never wins")
check(apply_url(
    '<main><p>A residency in Finland. '
    '<a href="https://residency.hubfeenix.fi">Website</a></p></main>', LISTING) is None,
    "single outbound homepage → None (falls back to the listing page)")

check(apply_url(
    '<main><p><a href="https://buinho.pt">Buinho</a> runs this. '
    '<a href="https://buinho.pt/residency-2/">How to apply</a></p></main>', LISTING)
    == "https://buinho.pt/residency-2/",
    "apply-cued deep link beats the same organiser's homepage")

check(apply_url(
    '<main><p><a href="https://sila8opencall.com/apply">Apply</a> '
    '<a href="https://wa.me/4915259430884">WhatsApp us</a></p></main>', LISTING)
    == "https://sila8opencall.com/apply", "WhatsApp links are never the apply link")

check(apply_url(
    '<main><p>Details on the '
    '<a href="https://neimenster.lu/en/calls/residences-land-art/">official page</a>.'
    '</p></main>', LISTING) == "https://neimenster.lu/en/calls/residences-land-art/",
    "weak cue + call-shaped path is accepted")

check(apply_url(
    '<main><p>Supported by <a href="https://sponsor-bank.com/about">our partner</a>.'
    '</p></main>', LISTING) is None, "an unrelated sponsor link is not an apply link")

check(apply_url(
    '<main><p><a href="https://organiser.org/open-call-2027/">Open call 2027</a> '
    '<a href="https://organiser.org/contact">Contact</a></p></main>', LISTING)
    == "https://organiser.org/open-call-2027/", "picks the call page over site nav")

print("\nResArtis sitemap regexes")
XML = ('<?xml version="1.0"?><urlset>'
       '<url><loc>https://resartis.org/open-call/first-call/</loc></url>'
       '<url><loc>https://resartis.org/open-call/second-call/</loc></url></urlset>')
check(src._RESARTIS_LOC_RE.findall(XML) ==
      ["https://resartis.org/open-call/first-call/",
       "https://resartis.org/open-call/second-call/"], "open-call locs parsed")
INDEX = ('<sitemapindex><sitemap><loc>https://resartis.org/wp-sitemap-posts-open_call-1.xml'
         '</loc></sitemap><sitemap><loc>https://resartis.org/wp-sitemap-posts-open_call-2.xml'
         '</loc></sitemap></sitemapindex>')
check(src._RESARTIS_MAP_RE.findall(INDEX) == ["1", "2"], "sub-sitemap numbers parsed")

print("\nbot-wall detection")
check(src._looks_like_bot_wall(
    '<html><head><title>Just a moment...</title></head><body>sgcaptcha</body></html>'),
    "sgcaptcha / 'just a moment' page recognised")
check(not src._looks_like_bot_wall(XML), "real XML is not a bot wall")

print("\nfetch_funder — generic German funder fetching (stubbed network)")
FUNDER_HTML = """<html><head>
  <link rel="alternate" type="application/rss+xml" href="/feed.xml">
 </head><body>
 <nav><a href="/kontakt">Kontakt</a><a href="/impressum">Impressum</a></nav>
 <main>
  <div class="item"><a href="/foerderung/arbeitsstipendium-2027">
     Arbeitsstipendium Bildende Kunst 2027</a>
     <p>Bewerbungsschluss: 15. M\u00e4rz 2027. F\u00f6rdersumme 12.000 Euro.</p></div>
  <div class="item"><a href="/aktuelles/jahresbericht-2025">
     Jahresbericht 2025 erschienen</a>
     <p>Unser R\u00fcckblick auf das vergangene Jahr.</p></div>
  <div class="item"><a href="https://elsewhere.example/partner">Partnerseite</a></div>
 </main></body></html>"""
CFG = {"source": "Testfonds", "org": "Testfonds", "url": "https://testfonds.de/foerderung/",
       "region": "DE", "country": "Germany", "type": "Grant"}

_saved = (src._get, src._feed)
src._get = lambda u, tries=3: FUNDER_HTML
src._feed = lambda u, s_: (_ for _ in ()).throw(RuntimeError("feed empty"))
items = src.fetch_funder(CFG)
check(len(items) == 1, f"scrapes only the call, not the annual report or the partner link (got {len(items)})")
check(items[0]["url"] == "https://testfonds.de/foerderung/arbeitsstipendium-2027",
      "relative href resolved against the listing page")
check(items[0]["region"] == "DE" and items[0]["type"] == "Grant",
      "config defaults applied to the item")

from normalize import normalize as _norm
n = _norm(items[0])
check(n["deadline"] == "2027-03-15", f"German deadline parsed end-to-end (got {n['deadline']})")
check(n["funded"] == "likely", f"F\u00f6rdersumme reads as funded (got {n['funded']})")

class _E:
    def __init__(s2, t, l, d): s2.d = {"title": t, "link": l, "summary": d}
    def get(s2, k, default=""): return s2.d.get(k, default)
src._feed = lambda u, s_: [_E("Ausschreibung Recherchestipendium 2027",
                              "https://testfonds.de/a", "Antragsfrist 1. Juni 2027"),
                           _E("Neue Website online", "https://testfonds.de/b", "Relaunch")]
items = src.fetch_funder(CFG)
check(len(items) == 1 and items[0]["url"] == "https://testfonds.de/a",
      "feed path keeps the call and drops the news post")
src._get, src._feed = _saved

print("\nGerman-language extraction")
from normalize import extract_deadline, guess_funded, guess_type
for text, want in [("Bewerbungsschluss: 15. M\u00e4rz 2027", "2027-03-15"),
                   ("Einsendeschluss ist der 1. Oktober 2026", "2026-10-01"),
                   ("Frist: 15. Dez. 2026", "2026-12-15"),
                   ("Antragsfrist: 2. J\u00e4nner 2027", "2027-01-02"),
                   ("Deadline: March 15, 2027", "2027-03-15")]:
    got = extract_deadline(text)
    check(got == want, f"{text!r} \u2192 {want} (got {got})")
check(guess_type("Reisekostenzuschuss f\u00fcr Projekte") == "Mobility", "Reisekosten \u2192 Mobility")
check(guess_funded("Ausschreibung: Projektf\u00f6rderung 2027") == "likely",
      "F\u00f6rderung \u2192 funded likely")
check(guess_funded("Teilnahmegeb\u00fchr 25 Euro") == "fee-based", "Teilnahmegeb\u00fchr \u2192 fee-based")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
    sys.exit(1)
print("all checks passed")
