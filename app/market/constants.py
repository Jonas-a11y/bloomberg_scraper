"""Constants for the public-market deep-dive endpoints.

* ``COUNTRY_MAP``           — country name → (Yahoo region code, Wikidata QID)
* ``REGION_FC``             — Yahoo region → set of acceptable
                              ``financialCurrency`` values; used as a cheap
                              foreign-mirror filter before the expensive
                              per-ticker ``info`` enrichment
* ``INDUSTRY_TO_YF_SECTOR`` — our canonical industry labels → Yahoo's
                              GICS sector strings
* ``INDUSTRY_TO_WIKIDATA_QID`` — same map, but to Wikidata QIDs for the
                              SPARQL fallback
"""
from __future__ import annotations


# Country name → (Yahoo region code, Wikidata QID). Single map keeps
# the two sources in sync.
COUNTRY_MAP = {
    # name              yahoo  wikidata QID
    "United States":     ("us", "Q30"),
    "China":             ("cn", "Q148"),
    "Germany":           ("de", "Q183"),
    "France":            ("fr", "Q142"),
    "United Kingdom":    ("gb", "Q145"),
    "Japan":             ("jp", "Q17"),
    "India":             ("in", "Q668"),
    "Switzerland":       ("ch", "Q39"),
    "Netherlands":       ("nl", "Q55"),
    "Canada":            ("ca", "Q16"),
    "Australia":         ("au", "Q408"),
    "Italy":             ("it", "Q38"),
    "Spain":             ("es", "Q29"),
    "Brazil":            ("br", "Q155"),
    "Hong Kong":         ("hk", "Q8646"),
    "Saudi Arabia":      ("sa", "Q851"),
    "Russia":            ("ru", "Q159"),
    "Russian Federation": ("ru", "Q159"),
    "Mexico":            ("mx", "Q96"),
    "Sweden":            ("se", "Q34"),
    "South Korea":       ("kr", "Q884"),
    "Taiwan":            ("tw", "Q865"),
    "Singapore":         ("sg", "Q334"),
    "Indonesia":         ("id", "Q252"),
    "Thailand":          ("th", "Q869"),
    "Norway":            ("no", "Q20"),
    "Denmark":           ("dk", "Q35"),
    "Finland":           ("fi", "Q33"),
    "Belgium":           ("be", "Q31"),
    "Austria":           ("at", "Q40"),
    "Ireland":           ("ie", "Q27"),
    "Greece":            ("gr", "Q41"),
    "Portugal":          ("pt", "Q45"),
    "Israel":            ("il", "Q801"),
    "South Africa":      ("za", "Q258"),
    "Turkey":            ("tr", "Q43"),
    "Argentina":         ("ar", "Q414"),
    "New Zealand":       ("nz", "Q664"),
    "Cyprus":            ("cy", "Q229"),
    "Monaco":            ("mc", "Q235"),
    "Luxembourg":        ("lu", "Q32"),
    "United Arab Emirates": ("ae", "Q878"),
    "Egypt":             ("eg", "Q79"),
    "Nigeria":           ("ng", "Q1033"),
    "Kenya":             ("ke", "Q114"),
    "Ukraine":           ("ua", "Q212"),
    "Poland":            ("pl", "Q36"),
    "Czech Republic":    ("cz", "Q213"),
    "Chile":             ("cl", "Q298"),
    "Colombia":          ("co", "Q739"),
    "Peru":              ("pe", "Q419"),
    "Venezuela":         ("ve", "Q717"),
    "Lebanon":           ("lb", "Q822"),
    "Liechtenstein":     ("li", "Q347"),
    "Vietnam":           ("vn", "Q881"),
    "Malaysia":          ("my", "Q833"),
    "Philippines":       ("ph", "Q928"),
}


def yf_region(country):
    pair = COUNTRY_MAP.get(country)
    return pair[0] if pair else None


def country_qid(country):
    pair = COUNTRY_MAP.get(country)
    return pair[1] if pair else None


# Reverse mapping for breaking down industry rows by country label.
YF_REGION_TO_COUNTRY = {pair[0]: name for name, pair in COUNTRY_MAP.items()}


def primary_market(region):
    """Each region's primary listing tag in Yahoo's `market` field.
    Anything else (most importantly ``dr_market`` for depository
    receipts) is a foreign shadow whose ``marketCap`` is reported in
    local currency disguised as USD."""
    return f"{region}_market" if region else None


# Per-region acceptable ``financialCurrency`` values. Real local
# companies report financials in the local currency; foreign mirror
# listings (NVD.DE, APC.DE) report in USD or their HQ currency. For
# non-eurozone this is conclusive; for eurozone it narrows ~750 → ~150,
# leaving the per-ticker ``info`` filter to disambiguate Germany from
# France/Netherlands/Italy.
REGION_FC = {
    "us": {"USD"}, "gb": {"GBP", "GBp"}, "de": {"EUR"}, "fr": {"EUR"},
    "jp": {"JPY"}, "cn": {"CNY", "HKD"}, "in": {"INR"}, "ch": {"CHF"},
    "nl": {"EUR"}, "ca": {"CAD", "USD"}, "kr": {"KRW"}, "tw": {"TWD"},
    "it": {"EUR"}, "es": {"EUR"}, "br": {"BRL"}, "hk": {"HKD"},
    "sa": {"SAR"}, "mx": {"MXN"}, "au": {"AUD"}, "se": {"SEK"},
    "no": {"NOK"}, "dk": {"DKK"},
}


# Map our canonical industry labels (produced by `normalize_industry`
# in app.insights.industries) to Yahoo's GICS sector strings.
# ``None`` means we don't try to filter by sector for that label —
# too broad to map cleanly.
INDUSTRY_TO_YF_SECTOR = {
    "Technology": "Technology",
    "Finance & Investments": "Financial Services",
    "Healthcare": "Healthcare",
    "Pharmaceuticals": "Healthcare",
    "Energy": "Energy",
    "Real Estate": "Real Estate",
    "Telecom": "Communication Services",
    "Media & Entertainment": "Communication Services",
    "Food & Beverage": "Consumer Defensive",
    "Metals & Mining": "Basic Materials",
    "Manufacturing": "Industrials",
    "Industrial": "Industrials",
    "Construction & Engineering": "Industrials",
    "Logistics": "Industrials",
    "Automotive": "Consumer Cyclical",
    "Fashion & Retail": "Consumer Cyclical",
    "Consumer": "Consumer Cyclical",
    "Sports": "Consumer Cyclical",
    "Diversified": None,
    "Other": None,
}

# Wikidata QIDs for our industry labels. Used when fetching public
# companies from Wikidata. ``None`` = don't filter by industry.
INDUSTRY_TO_WIKIDATA_QID = {
    "Technology": "Q11661",            # information technology
    "Finance & Investments": "Q43015", # financial services
    "Healthcare": "Q31218",            # health care industry
    "Pharmaceuticals": "Q507443",      # pharmaceutical industry
    "Energy": "Q644023",               # energy industry
    "Real Estate": "Q170282",          # real estate
    "Telecom": "Q43229",               # telecommunication
    "Media & Entertainment": "Q201658", # mass media
    "Food & Beverage": "Q39495",       # food industry
    "Metals & Mining": "Q113489728",   # metals and mining industry
    "Manufacturing": "Q187939",        # manufacturing
    "Industrial": "Q187939",           # manufacturing
    "Automotive": "Q190117",           # automotive industry
    "Fashion & Retail": "Q126793",     # retail (fashion is too narrow)
    "Consumer": "Q126793",
    "Sports": "Q31629",                # sports
    "Diversified": None,
    "Other": None,
}
