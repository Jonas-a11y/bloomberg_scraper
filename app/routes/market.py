"""Stock-market deep-dive endpoint via Yahoo Screener (yfinance) + Wikidata.

Used by the Insights tab's country/industry deep-dive panel to show the
broader public-market view alongside the billionaire view.

Primary source: Yahoo Screener (`yf.EquityQuery` + `yf.screen()`). Yahoo
returns top public companies ranked by live market cap, filtered by
`region` (ISO country) or `sector` (GICS).

Secondary source: Wikidata SPARQL. When Yahoo's coverage is thin (small
markets, recently IPO'd companies, region codes Yahoo doesn't map well
for), we top up the result with publicly-listed Wikidata entities for
the same country/industry — name, headquarters, optional ticker, and
inception year. Wikidata-sourced rows have no live market cap; they're
shown after the Yahoo results, marked with their source.
"""
import logging
import time
from datetime import datetime

from curl_cffi import requests as cffi_requests
from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter()

# Map our country labels (as stored in persons.citizenship) to Yahoo's
# `region` codes AND Wikidata QIDs. Single map keeps the two sources in
# sync.
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


# Each region's primary stock exchange. We only keep listings on these
# exchanges to avoid Yahoo's foreign-shadow tickers whose marketCap is
# reported in the local currency disguised as USD.
PRIMARY_EXCHANGES = {
    "us": {"NasdaqGS", "NasdaqGM", "NasdaqCM", "NYSE", "NYSEAmerican", "NYSE Arca"},
    "gb": {"LSE", "London"},
    "de": {"XETRA", "Frankfurt"},
    "fr": {"Paris", "Euronext Paris"},
    "jp": {"Tokyo", "Osaka"},
    "cn": {"Shanghai", "Shenzhen", "ShangHai"},
    "in": {"NSE", "BSE", "Bombay"},
    "ch": {"Swiss Exchange", "SIX"},
    "nl": {"Amsterdam", "Euronext Amsterdam"},
    "ca": {"Toronto", "TSX"},
    "au": {"ASX", "Sydney"},
    "it": {"Milan", "MIL"},
    "es": {"Madrid", "MCE"},
    "br": {"Sao Paulo", "B3"},
    "hk": {"HKSE"},
    "sa": {"Saudi"},
    "mx": {"Mexico"},
    "kr": {"KSE", "KOSDAQ"},
    "tw": {"TPE", "Taiwan"},
    "sg": {"Singapore", "SGX"},
}


def _yf_region(country):
    pair = COUNTRY_MAP.get(country)
    return pair[0] if pair else None


def _country_qid(country):
    pair = COUNTRY_MAP.get(country)
    return pair[1] if pair else None


# Map our canonical industry labels (produced by _normalize_industry in
# insights.py) to Yahoo's GICS sector strings. None means we don't try to
# filter by sector for that label — too broad to map cleanly.
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
# companies from Wikidata. Same logic: None = don't filter by industry.
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

# In-memory cache: {(scope_kind, scope_value, limit): (timestamp, payload)}.
_cache = {}
_CACHE_TTL_SEC = 6 * 3600  # 6 hours


def _cached(key, ttl_sec=_CACHE_TTL_SEC):
    hit = _cache.get(key)
    if hit and (time.time() - hit[0]) < ttl_sec:
        return hit[1]
    return None


def _cache_put(key, payload):
    _cache[key] = (time.time(), payload)


def _enrich_with_sector(rows, max_lookups=30):
    """Yahoo's screen API returns market caps but no sector/industry —
    those live on the per-ticker `info` endpoint. Fetch in batch for the
    top N rows so the sector breakdown chart has data without paying the
    cost for every row.

    Per-ticker results are cached at the module level so subsequent
    panel opens are free. We only enrich the top `max_lookups` so the
    first response stays under ~3 seconds even on a cold cache."""
    try:
        import yfinance as yf
    except ImportError:
        return rows

    for row in rows[:max_lookups]:
        ticker = row.get("ticker")
        if not ticker or row.get("sector"):
            continue
        info_key = ("info", ticker)
        cached = _cached(info_key, ttl_sec=_CACHE_TTL_SEC)
        if cached is None:
            try:
                info = yf.Ticker(ticker).info
                cached = {
                    "sector": info.get("sector"),
                    "industry": info.get("industry"),
                    "country": info.get("country"),
                }
                _cache_put(info_key, cached)
            except Exception:
                cached = {}
                _cache_put(info_key, cached)
        if cached:
            row["sector"] = cached.get("sector") or row.get("sector")
            row["industry"] = cached.get("industry") or row.get("industry")
            row["country"] = cached.get("country") or row.get("country")
    return rows


def _yf_screen(region=None, sector=None, limit=25):
    """Query Yahoo Screener for equities matching the filters, ranked
    by market cap descending. Returns a list of normalized dicts."""
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed; market endpoints disabled")
        return []

    filters = [yf.EquityQuery("gt", ["intradaymarketcap", 1_000_000_000])]
    if region:
        filters.append(yf.EquityQuery("eq", ["region", region]))
    if sector:
        filters.append(yf.EquityQuery("eq", ["sector", sector]))

    if len(filters) == 1:
        query = filters[0]
    else:
        query = yf.EquityQuery("and", filters)

    try:
        result = yf.screen(
            query,
            sortField="intradaymarketcap",
            sortAsc=False,
            size=min(limit, 250),
        )
    except Exception as e:
        logger.warning(f"yfinance screener failed: {e}")
        return []

    quotes = result.get("quotes") if isinstance(result, dict) else []

    def _normalize_name(s):
        if not s:
            return ""
        n = s.upper()
        for suffix in (" CORPORATION", " CORP.", " CORP", " INC.", " INC",
                       " LIMITED", " LTD.", " LTD", " HOLDINGS", " HOLDING",
                       " GROUP", " PLC", " AG", " S.A.", " SA", " SE",
                       " SPA", " S.P.A.", " N.V.", " NV", " AB", " CO."):
            if n.endswith(suffix):
                n = n[: -len(suffix)].strip()
        return n.strip(",").strip()

    # Two-pass: first collect all candidates per normalized name, then
    # pick the best representative per company. Best = USD-currency
    # listing (avoids foreign-listed shadows whose marketCap field is
    # quoted in local currency disguised as USD), with smallest plausible
    # market cap among USD options (deduplicates ADR vs primary).
    by_norm = {}
    for q in (quotes or []):
        cap = q.get("marketCap")
        if not cap or cap > 20_000_000_000_000:
            continue
        raw_name = q.get("longName") or q.get("shortName") or q.get("symbol") or ""
        norm = _normalize_name(raw_name)
        if not norm:
            continue
        currency = (q.get("currency") or "").upper()
        prev = by_norm.get(norm)
        if prev is None:
            by_norm[norm] = q
            continue
        # Prefer USD listing
        prev_currency = (prev.get("currency") or "").upper()
        if currency == "USD" and prev_currency != "USD":
            by_norm[norm] = q
            continue
        if prev_currency == "USD" and currency != "USD":
            continue
        # Both USD or both non-USD: prefer the larger ticker-suffix-free
        # symbol (e.g. NVDA over NVDA.MX). Heuristic but works for most.
        prev_sym = prev.get("symbol", "")
        new_sym = q.get("symbol", "")
        if "." in prev_sym and "." not in new_sym:
            by_norm[norm] = q

    out = []
    primary_xchs = PRIMARY_EXCHANGES.get(region, set()) if region else None
    for q in by_norm.values():
        cap = q.get("marketCap")
        currency = (q.get("currency") or "").upper()
        exchange = q.get("fullExchangeName", "")
        # If we asked for a specific region, only trust listings on that
        # region's primary exchanges. This filters out the foreign-listed
        # shadow tickers (NVDA on Mexico, Apple on London, etc.) whose
        # marketCap is reported in the local currency mislabeled as USD.
        if primary_xchs and exchange and exchange not in primary_xchs:
            # Best-effort substring match for variants Yahoo names
            if not any(p.lower() in exchange.lower() for p in primary_xchs):
                continue
        # Final sanity: a non-USD listing's marketCap is usually wrong;
        # filter ones > $5T entirely.
        if currency and currency != "USD" and cap > 5_000_000_000_000:
            continue
        out.append({
            "ticker": q.get("symbol"),
            "name": q.get("longName") or q.get("shortName") or q.get("symbol"),
            "sector": q.get("sector"),
            "industry": q.get("industry"),
            "country": q.get("region") or q.get("country"),
            "market_cap_usd": cap,
            "price": q.get("regularMarketPrice"),
            "currency": q.get("currency"),
            "exchange": q.get("fullExchangeName"),
            "source": "yahoo",
        })
    out.sort(key=lambda x: -(x.get("market_cap_usd") or 0))
    return out


def _wikidata_companies(country_qid=None, industry_qid=None, limit=25):
    """SPARQL-fetch public companies from Wikidata. Returns name +
    headquarters country + ticker (where set) + inception year. No live
    market cap (Wikidata's data isn't current enough for that)."""
    # Build the WHERE clause incrementally
    where_parts = [
        # P31 instance of P31766 stock exchange? No — instance of "public
        # company" (Q891723) OR enterprise (Q6881511). Use "publicly
        # traded company" Q891723 to keep it focused on stock-listed firms.
        "?co wdt:P31/wdt:P279* wd:Q891723 .",
        "?co rdfs:label ?name . FILTER(LANG(?name) = 'en')",
    ]
    if country_qid:
        where_parts.append(f"?co wdt:P17 wd:{country_qid} .")
    if industry_qid:
        where_parts.append(f"?co wdt:P452 wd:{industry_qid} .")
    where_parts.append("OPTIONAL { ?co wdt:P249 ?ticker . }")
    where_parts.append("OPTIONAL { ?co wdt:P571 ?inception . }")

    sparql = f"""
    SELECT DISTINCT ?co ?coLabel ?name ?ticker ?inception WHERE {{
        {chr(10).join(where_parts)}
        SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" . }}
    }}
    LIMIT {limit}
    """
    try:
        r = cffi_requests.get(
            "https://query.wikidata.org/sparql",
            params={"query": sparql, "format": "json"},
            headers={
                "User-Agent": "BloombergScraper/1.0 (educational; github.com/jonas-giessler/bloomberg_scraper)",
                "Accept": "application/sparql-results+json",
            },
            timeout=20,
            impersonate="chrome",
        )
    except Exception as e:
        logger.warning(f"Wikidata SPARQL failed: {e}")
        return []
    if r.status_code != 200:
        logger.info(f"Wikidata SPARQL HTTP {r.status_code}")
        return []

    try:
        data = r.json()
    except Exception:
        return []
    out = []
    seen_qids = set()
    for row in data.get("results", {}).get("bindings", []):
        qid = row.get("co", {}).get("value", "").split("/")[-1]
        if not qid or qid in seen_qids:
            continue
        seen_qids.add(qid)
        name = row.get("coLabel", {}).get("value") or row.get("name", {}).get("value")
        if not name or name == qid:
            continue
        ticker = row.get("ticker", {}).get("value")
        inception = row.get("inception", {}).get("value", "")[:4]
        out.append({
            "ticker": ticker,
            "name": name,
            "sector": None,
            "industry": None,
            "country": None,
            "market_cap_usd": None,  # Wikidata doesn't have live caps
            "price": None,
            "currency": None,
            "exchange": None,
            "inception_year": int(inception) if inception.isdigit() else None,
            "wikidata_qid": qid,
            "source": "wikidata",
        })
    return out


def _merge_sources(yahoo_rows, wikidata_rows, limit):
    """Yahoo rows come first (they have market cap, ranked). Wikidata
    rows fill remaining slots, deduped by ticker / name."""
    seen_tickers = {r["ticker"] for r in yahoo_rows if r.get("ticker")}
    seen_names_lower = {(r["name"] or "").lower() for r in yahoo_rows}
    out = list(yahoo_rows)
    for r in wikidata_rows:
        if r.get("ticker") and r["ticker"] in seen_tickers:
            continue
        if (r.get("name") or "").lower() in seen_names_lower:
            continue
        out.append(r)
        if len(out) >= limit:
            break
    return out[:limit]


@router.get("/market/by-country")
def market_by_country(country: str, limit: int = 25):
    """Top public companies headquartered in `country`. Yahoo Screener
    primary source; Wikidata fills gaps when Yahoo's coverage is thin
    (small markets) or returns no results."""
    cache_key = ("by-country", country, limit)
    hit = _cached(cache_key)
    if hit is not None:
        return hit

    region = _yf_region(country)
    qid = _country_qid(country)
    yahoo = _yf_screen(region=region, limit=max(limit, 50)) if region else []
    wikidata = []
    if len(yahoo) < limit:
        # Top up from Wikidata
        wikidata = _wikidata_companies(country_qid=qid, limit=max(limit * 2, 50)) if qid else []
    companies = _merge_sources(yahoo, wikidata, limit)
    # Enrich top results with sector/industry from Yahoo's per-ticker
    # info endpoint so the sector breakdown chart isn't all "Unknown".
    companies = _enrich_with_sector(companies)

    if not companies:
        return {
            "country": country,
            "total_market_cap_usd": 0,
            "companies": [],
            "sectors": [],
            "note": (
                f"No data for '{country}' — Yahoo and Wikidata both came back empty. "
                f"This usually means the country isn't in our region map "
                f"(see app/routes/market.py:COUNTRY_MAP) or the SPARQL query timed out."
            ),
            "sources": [],
        }

    total = sum(c.get("market_cap_usd") or 0 for c in companies)
    sector_totals = {}
    for c in companies:
        s = c.get("sector") or "Unknown"
        sector_totals[s] = sector_totals.get(s, 0) + (c.get("market_cap_usd") or 0)
    sectors = [
        {"name": s, "market_cap_usd": v, "share": (v / total) if total else 0}
        for s, v in sorted(sector_totals.items(), key=lambda kv: -kv[1])
        if s != "Unknown" or v > 0
    ]
    sources = sorted({c.get("source") for c in companies if c.get("source")})

    payload = {
        "country": country,
        "total_market_cap_usd": total,
        "companies": companies,
        "sectors": sectors,
        "sources": sources,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }
    _cache_put(cache_key, payload)
    return payload


@router.get("/market/by-industry")
def market_by_industry(industry: str, limit: int = 25):
    """Top public companies in `industry` worldwide, by market cap.
    Yahoo primary, Wikidata fallback for industries Yahoo's GICS sector
    map doesn't cover well."""
    cache_key = ("by-industry", industry, limit)
    hit = _cached(cache_key)
    if hit is not None:
        return hit

    sector = INDUSTRY_TO_YF_SECTOR.get(industry, industry)
    qid = INDUSTRY_TO_WIKIDATA_QID.get(industry)

    # Querying Yahoo Screener with sector but NO region returns broken
    # foreign-listed shadow tickers whose marketCap is reported in the
    # local currency disguised as USD ($19T NVDA on Colombian exchange).
    # We screen each major market separately, keep only the listings on
    # that region's primary exchanges, and merge by company name —
    # preferring USD-quoted entries (which Yahoo's screener handles
    # correctly) when the same name appears in multiple regions.
    yahoo_combined = []
    if sector:
        for region_code in ("us", "gb", "de", "fr", "jp", "cn", "in",
                            "ch", "nl", "ca", "kr", "tw"):
            partial = _yf_screen(
                region=region_code, sector=sector, limit=15,
            )
            yahoo_combined.extend(partial)

        # Cross-region dedupe by normalized name. Tie-breakers:
        #  1. USD-quoted listing wins (the only currency Yahoo handles
        #     correctly for marketCap).
        #  2. No `.` in the symbol (primary US listing wins over ADRs).
        def _norm(name):
            n = (name or "").upper()
            for suffix in (" CORPORATION", " CORP.", " CORP", " INC.", " INC",
                           " LIMITED", " LTD.", " LTD", " HOLDINGS", " GROUP",
                           " PLC", " AG", " S.A.", " SA", " SE", " CO."):
                if n.endswith(suffix):
                    n = n[: -len(suffix)].strip()
            return n.strip(",").strip()

        best = {}
        for c in yahoo_combined:
            key = _norm(c.get("name"))
            if not key:
                continue
            prev = best.get(key)
            if prev is None:
                best[key] = c
                continue
            new_usd = (c.get("currency") or "").upper() == "USD"
            prev_usd = (prev.get("currency") or "").upper() == "USD"
            if new_usd and not prev_usd:
                best[key] = c
                continue
            if prev_usd and not new_usd:
                continue
            new_clean = "." not in (c.get("ticker") or "")
            prev_clean = "." not in (prev.get("ticker") or "")
            if new_clean and not prev_clean:
                best[key] = c
                continue
        yahoo_combined = sorted(
            best.values(),
            key=lambda x: -(x.get("market_cap_usd") or 0),
        )[:max(limit, 50)]
        # Non-USD listings have their marketCap in local currency
        # disguised as USD. Without an FX conversion step (slow,
        # rate-limited) they'd inflate the rankings — Lasertec at
        # JPY 3,800B = $24B, not $3,800B. Drop them from this view;
        # the country deep-dive shows local primary listings on
        # their own.
        yahoo_combined = [
            c for c in yahoo_combined
            if (c.get("currency") or "").upper() == "USD"
        ]

    wikidata = []
    if len(yahoo_combined) < limit:
        wikidata = _wikidata_companies(industry_qid=qid, limit=max(limit * 2, 50)) if qid else []
    companies = _merge_sources(yahoo_combined, wikidata, limit)
    companies = _enrich_with_sector(companies)

    if not companies:
        return {
            "industry": industry,
            "yahoo_sector": sector,
            "total_market_cap_usd": 0,
            "companies": [],
            "countries": [],
            "note": (
                f"No data for '{industry}' — Yahoo's screener returned empty and "
                f"there's no Wikidata QID mapped (see "
                f"INDUSTRY_TO_WIKIDATA_QID in app/routes/market.py)."
            ),
            "sources": [],
        }

    total = sum(c.get("market_cap_usd") or 0 for c in companies)
    # Country code normalization: Yahoo returns either a country name
    # ("United States") or a region code ("us"). Collapse to the canonical
    # name so the breakdown doesn't double-count.
    YF_REGION_TO_COUNTRY = {
        v[0]: k for k, v in COUNTRY_MAP.items()
    }
    def _country_label(c):
        if not c:
            return "Unknown"
        return YF_REGION_TO_COUNTRY.get(c.lower(), c)
    country_totals = {}
    for c in companies:
        co = _country_label(c.get("country"))
        country_totals[co] = country_totals.get(co, 0) + (c.get("market_cap_usd") or 0)
    countries = [
        {"name": c, "market_cap_usd": v, "share": (v / total) if total else 0}
        for c, v in sorted(country_totals.items(), key=lambda kv: -kv[1])
        if c != "Unknown" or v > 0
    ]
    sources = sorted({c.get("source") for c in companies if c.get("source")})

    payload = {
        "industry": industry,
        "yahoo_sector": sector,
        "total_market_cap_usd": total,
        "companies": companies,
        "countries": countries,
        "sources": sources,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }
    _cache_put(cache_key, payload)
    return payload
