"""Industry-name normalisation.

Different sources spell industries differently — Bloomberg returns plain
strings ("Technology"), Kaggle stores them as Python-list literals like
`['Technology                    ']` with stray whitespace. We normalise
to a single canonical label so the UI's color palette can do a stable
lookup. We also collapse a few near-synonyms ("Finance & Investments"
vs "Finance") so a person isn't a different color year-over-year.
"""
from __future__ import annotations


_CANONICAL = {
    "finance": "Finance & Investments",
    "finance & investments": "Finance & Investments",
    "investments": "Finance & Investments",
    "fashion": "Fashion & Retail",
    "retail": "Fashion & Retail",
    "fashion & retail": "Fashion & Retail",
    "tech": "Technology",
    "technology": "Technology",
    "media": "Media & Entertainment",
    "media & entertainment": "Media & Entertainment",
    "telecom": "Telecom",
    "telecommunications": "Telecom",
    "construction": "Construction & Engineering",
    "construction & engineering": "Construction & Engineering",
    "metals": "Metals & Mining",
    "metals & mining": "Metals & Mining",
    "food": "Food & Beverage",
    "food & beverage": "Food & Beverage",
    "diversified": "Diversified",
    "energy": "Energy",
    "real estate": "Real Estate",
    "healthcare": "Healthcare",
    "manufacturing": "Manufacturing",
    "automotive": "Automotive",
    "logistics": "Logistics",
    "service": "Service",
    "sports": "Sports",
    "gambling & casinos": "Gambling & Casinos",
}


def normalize_industry(raw):
    """Return a clean canonical industry label, or None.

    Accepts the raw shapes we see in storage:
      - ``"Technology"`` (Bloomberg)
      - ``"['Technology    ']"`` (Kaggle)
      - ``None`` / empty
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # Strip Python-list-literal wrappers from Kaggle data
    s = s.strip("[]")
    # Take the first comma-separated chunk (usually single-element)
    s = s.split(",")[0].strip()
    # Strip leftover quote characters
    s = s.strip("'\"").strip()
    if not s:
        return None
    return _CANONICAL.get(s.lower(), s)
