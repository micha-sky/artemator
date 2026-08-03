"""
prefs.py — the active personalization profile.

Which sources run, which region-groups and disciplines count as "mine", and how
"fit" is scored are all per-person. Rather than editing code for each user, that
tuning lives in profiles/<name>.json and is selected at runtime with the
AGG_PROFILE environment variable:

    AGG_PROFILE=sash  python aggregator.py update      # tailored to OM/SYMBIONT
    AGG_PROFILE=kasti python aggregator.py update      # painting/SE-Asia focus
    python aggregator.py update                        # 'default' — neutral superset

Everything downstream (geo, normalize, sources, aggregator, the exported
dashboard payload) reads the module-level constants below, so the same codebase
serves everyone. `default` scores no fit keywords, so fit is a no-op unless a
tailored profile is selected — the tool stays universal out of the box.
"""
import json
import os

_DIR = os.path.join(os.path.dirname(__file__), "profiles")


def available():
    """Profile names shippable in profiles/ (sans .json), sorted."""
    try:
        return sorted(f[:-5] for f in os.listdir(_DIR) if f.endswith(".json"))
    except OSError:
        return []


NAME = (os.environ.get("AGG_PROFILE") or "default").strip() or "default"

try:
    with open(os.path.join(_DIR, NAME + ".json"), encoding="utf-8") as _f:
        CONFIG = json.load(_f)
except FileNotFoundError:
    raise SystemExit(
        f"profile '{NAME}' not found in {_DIR} — "
        f"set AGG_PROFILE to one of: {', '.join(available()) or '(none found)'}")

LABEL = CONFIG.get("label", NAME)
# Region-groups surfaced as the "★ my regions" preset and the map/chip focus.
PREFERRED_GROUPS = tuple(CONFIG.get("preferred_groups", []))
# Disciplines that are "mine": drive the "★ my disciplines" filter and a small
# fit boost. Empty ⇒ the feature is inert (neutral profile).
MY_DISCIPLINES = list(CONFIG.get("my_disciplines", []))
# Coarse home-region nudges for fit, keyed by the DE/EU/Intl bucket. Empty ⇒ off.
REGION_BOOST = {str(k): float(v) for k, v in CONFIG.get("region_boost", {}).items()}
# Which source fetchers to run. None/absent ⇒ every registered source.
ENABLED_SOURCES = CONFIG.get("enabled_sources")
# Weighted keyword groups for fit scoring: [(weight, [phrase, …]), …].
# Empty ⇒ fit is 0 for every call (default/neutral).
FIT_KEYWORDS = [(int(g["weight"]), list(g["phrases"]))
                for g in CONFIG.get("fit_keywords", [])]


def as_dashboard():
    """The slice of the profile the static dashboard needs, baked into export."""
    return {
        "name": NAME,
        "label": LABEL,
        "preferred_groups": list(PREFERRED_GROUPS),
        "my_disciplines": MY_DISCIPLINES,
        # whether fit scoring is meaningful for this profile — the dashboard hides
        # the fit preset / best-fit sort when it isn't (neutral 'default').
        "has_fit": bool(FIT_KEYWORDS),
    }
