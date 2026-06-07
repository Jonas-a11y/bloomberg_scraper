"""Stock-market deep-dive endpoint via Yahoo Screener (yfinance) + Wikidata.

Used by the Insights tab's country/industry deep-dive panel to show the
broader public-market view alongside the billionaire view.

Primary source: Yahoo Screener (`yf.EquityQuery` + `yf.screen()`). Yahoo
returns top public companies ranked by live market cap, filtered by
`region` (ISO country) or `sector` (GICS).

Two quirks of Yahoo's screener we work around:

  1. Per-quote rows from `screen()` carry `marketCap` and `currency` but
     not `sector`/`industry` — those only live on the per-ticker `info`
     endpoint. We enrich every row concurrently after screening
     (`_enrich_with_sector`, ~2s for 100 tickers) so the sector
     breakdown / treemap colors aren't 40% "Unknown".

  2. Non-US listings report `marketCap` in their LOCAL currency
     (e.g. SAP.DE → EUR, 7203.T → JPY) without converting to USD,
     even though the field has no currency suffix. Comparing them
     directly puts Toyota at $37T and Lasertec at $4T. We fetch live
     FX rates from yfinance (`EURUSD=X` etc.) and convert in-place;
     non-USD rows whose currency we can't price get dropped rather
     than left at a misleading number. The same FX call serves the
     by-country view (so a German company's EUR cap shows in USD)
     and the by-industry view (so the global ranking is comparable).

The `market` field on each quote (`us_market`, `de_market`,
`dr_market` for depository receipts) discriminates primary listings
from foreign shadows — cleaner than exchange-name string matching.

Secondary source (Wikidata SPARQL): used ONLY when Yahoo returns zero
results for a country/industry. Wikidata rows have no live market
cap, so they're shown without sizing — fine as a "we know these exist
but can't price them" fallback for tiny markets, but they shouldn't
pollute the dataset when Yahoo has good coverage.
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor
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


# Each region's primary listing tag in Yahoo's `market` field. Anything
# else (most importantly `dr_market`, depository receipts) is a foreign
# shadow whose marketCap is reported in local currency disguised as USD.
def _primary_market(region):
    return f"{region}_market" if region else None


# In-memory FX cache. Yahoo's screener reports `marketCap` in the
# listing's `currency` (EUR for SAP.DE, JPY for 7203.T, …) WITHOUT
# converting to USD even when downstream callers expect USD. We need
# live FX to compare a EUR-listed company against a USD-listed one.
_FX_TTL_SEC = 60 * 60  # refresh hourly; intraday FX moves don't matter
_fx_cache = {"ts": 0.0, "rates": {"USD": 1.0}}


def _fx_rates(currencies):
    """Return {ccy: usd_per_unit} for the requested currencies, fetched
    in one batched yfinance call. Cached for an hour. Anything we can't
    price returns no entry — caller decides whether to drop the row."""
    needed = {(c or "").upper() for c in currencies if c}
    needed.discard("USD")
    needed.discard("")
    if not needed:
        return _fx_cache["rates"]
    fresh = (time.time() - _fx_cache["ts"]) < _FX_TTL_SEC
    missing = needed - set(_fx_cache["rates"].keys())
    if fresh and not missing:
        return _fx_cache["rates"]

    try:
        import yfinance as yf
    except ImportError:
        return _fx_cache["rates"]

    syms = " ".join(f"{c}USD=X" for c in (needed | missing))
    try:
        bundle = yf.Tickers(syms)
        for c in (needed | missing):
            ticker = bundle.tickers.get(f"{c}USD=X")
            if not ticker:
                continue
            try:
                price = ticker.fast_info.get("lastPrice")
            except Exception:
                price = None
            if price and price > 0:
                _fx_cache["rates"][c] = float(price)
    except Exception as e:
        logger.warning(f"FX fetch failed: {e}")
    _fx_cache["ts"] = time.time()
    return _fx_cache["rates"]


def _to_usd(cap, currency, rates):
    """Convert a market cap to USD using the rate table from _fx_rates.
    Returns None if the currency is unknown — caller drops the row
    rather than reporting a misleading number."""
    if cap is None:
        return None
    ccy = (currency or "").upper()
    if not ccy or ccy == "USD":
        return cap
    rate = rates.get(ccy)
    if not rate:
        return None
    return cap * rate


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


def _enrich_with_sector(rows, max_workers=30):
    """Yahoo's `screen()` payload carries marketCap but not sector — those
    only live on the per-ticker `info` endpoint. Fanned out concurrently
    so 100 tickers complete in ~2s instead of ~30s sequentially.

    Per-ticker results are cached at the module level so subsequent
    panel opens are free."""
    try:
        import yfinance as yf
    except ImportError:
        return rows

    # Collect tickers that still need enrichment, deduped — same ticker
    # can appear twice across multi-region merges and we only want to
    # fetch each once per cache window.
    todo = []
    for row in rows:
        ticker = row.get("ticker")
        if not ticker or row.get("sector"):
            continue
        info_key = ("info", ticker)
        if _cached(info_key) is None:
            todo.append(ticker)
    todo = list(dict.fromkeys(todo))  # preserve order, dedupe

    def _fetch(t):
        try:
            info = yf.Ticker(t).info
            return t, {
                "sector": info.get("sector"),
                "industry": info.get("industry"),
                "country": info.get("country"),
            }
        except Exception:
            return t, {}

    if todo:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(todo))) as ex:
            for t, data in ex.map(_fetch, todo):
                _cache_put(("info", t), data)

    for row in rows:
        ticker = row.get("ticker")
        if not ticker:
            continue
        cached = _cached(("info", ticker)) or {}
        if cached:
            row["sector"] = cached.get("sector") or row.get("sector")
            row["industry"] = cached.get("industry") or row.get("industry")
            row["country"] = cached.get("country") or row.get("country")
    return rows


def _yf_screen(region=None, sector=None, limit=25, min_market_cap=1_000_000_000):
    """Query Yahoo Screener for equities matching the filters, ranked
    by market cap descending. Returns a list of normalized dicts with
    market caps converted to USD.

    Yahoo caps `size` at 250 per call, so for `limit > 250` we
    paginate via the `offset` argument. We bail out as soon as a page
    comes back short — that's the natural EOF signal."""
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed; market endpoints disabled")
        return []

    filters = [yf.EquityQuery("gt", ["intradaymarketcap", min_market_cap])]
    if region:
        filters.append(yf.EquityQuery("eq", ["region", region]))
    if sector:
        filters.append(yf.EquityQuery("eq", ["sector", sector]))

    if len(filters) == 1:
        query = filters[0]
    else:
        query = yf.EquityQuery("and", filters)

    PAGE = 250
    quotes = []
    remaining = limit
    offset = 0
    while remaining > 0:
        page_size = min(remaining, PAGE)
        try:
            result = yf.screen(
                query,
                sortField="intradaymarketcap",
                sortAsc=False,
                size=page_size,
                offset=offset,
            )
        except Exception as e:
            logger.warning(f"yfinance screener failed at offset={offset}: {e}")
            break
        page_quotes = result.get("quotes") if isinstance(result, dict) else []
        if not page_quotes:
            break
        quotes.extend(page_quotes)
        if len(page_quotes) < page_size:
            break  # natural end of results
        remaining -= page_size
        offset += page_size
    if not quotes:
        return []

    # When we asked for a specific region, filter out depository
    # receipts and other non-primary listings. Yahoo's `market` field
    # tags primary listings as `<region>_market` and DRs as `dr_market`.
    primary = _primary_market(region)
    if primary:
        quotes = [q for q in quotes if q.get("market") == primary]

    # Fetch FX rates once for every currency present in the response,
    # in a single batched call.
    rates = _fx_rates({q.get("currency") for q in quotes})

    out = []
    for q in quotes:
        cap_local = q.get("marketCap")
        currency = (q.get("currency") or "").upper()
        cap_usd = _to_usd(cap_local, currency, rates)
        if cap_usd is None or cap_usd <= 0:
            continue
        # Sanity ceiling — anything above $10T is almost certainly a
        # data glitch (Yahoo occasionally returns inflated caps for
        # ETFs and depository receipts that slipped through the
        # market-tag filter).
        if cap_usd > 10_000_000_000_000:
            continue
        out.append({
            "ticker": q.get("symbol"),
            "name": q.get("longName") or q.get("shortName") or q.get("symbol"),
            "sector": q.get("sector"),
            "industry": q.get("industry"),
            "country": q.get("region") or q.get("country"),
            "market_cap_usd": cap_usd,
            "price": q.get("regularMarketPrice"),
            "currency": currency or None,
            # `financialCurrency` is the company's reporting currency
            # — a cheap-but-decent home-country signal, since foreign
            # mirror listings (NVD.DE, APC.DE) report in USD while
            # actual local companies (SAP.DE, SIE.DE) report in EUR.
            # Used by the country deep-dive to pre-filter before the
            # expensive `info` enrichment.
            "financial_currency": (q.get("financialCurrency") or "").upper() or None,
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


@router.get("/market/by-country")
def market_by_country(country: str, limit: int = 25):
    """Top public companies headquartered in `country`. Yahoo Screener
    primary source; Wikidata used as a fallback only when Yahoo returned
    nothing (small markets like Liechtenstein)."""
    cache_key = ("by-country", country, limit)
    hit = _cached(cache_key)
    if hit is not None:
        return hit

    region = _yf_region(country)
    qid = _country_qid(country)
    # Pull a generous oversample. For non-US countries, only a small
    # fraction of region=XX listings are real local companies — the
    # top of the list is dominated by foreign mirrors. We paginate to
    # 2000 rows and lower the cap floor to $100M so DAX/MDAX names
    # surface; for the US case (where almost all results are real US
    # companies) we cap at 250 to keep things fast.
    if region == "us":
        yahoo = _yf_screen(region=region, limit=250)
    elif region:
        yahoo = _yf_screen(region=region, limit=2000, min_market_cap=100_000_000)
    else:
        yahoo = []

    # Pass 1 (cheap): drop the obvious foreign mirrors via
    # `financial_currency`. Real local companies report financials in
    # the local currency; foreign mirrors report in USD (or their HQ
    # currency). For non-eurozone this is conclusive; for eurozone it
    # narrows ~750 → ~150, leaving the per-ticker `info` filter to
    # disambiguate Germany from France/Netherlands/Italy.
    REGION_FC = {
        "us": {"USD"}, "gb": {"GBP", "GBp"}, "de": {"EUR"}, "fr": {"EUR"},
        "jp": {"JPY"}, "cn": {"CNY", "HKD"}, "in": {"INR"}, "ch": {"CHF"},
        "nl": {"EUR"}, "ca": {"CAD", "USD"}, "kr": {"KRW"}, "tw": {"TWD"},
        "it": {"EUR"}, "es": {"EUR"}, "br": {"BRL"}, "hk": {"HKD"},
        "sa": {"SAR"}, "mx": {"MXN"}, "au": {"AUD"}, "se": {"SEK"},
        "no": {"NOK"}, "dk": {"DKK"},
    }
    accept_fc = REGION_FC.get(region)
    if accept_fc and yahoo:
        yahoo = [
            c for c in yahoo
            if not c.get("financial_currency")
            or c.get("financial_currency") in accept_fc
        ]

    # Pass 2 (accurate): enrich with HQ via `info.country`, then keep
    # only those whose HQ matches this country. Catches the eurozone
    # cross-listings the cheap filter couldn't (ASML.DE → Netherlands
    # not Germany).
    if region and yahoo:
        # The cheap pass has already cut foreign-currency mirrors;
        # the survivors are mostly real candidates. Cap at 300 to
        # bound enrichment cost — already sorted by market cap, so
        # we keep the megacaps that matter.
        yahoo = yahoo[:300]
        yahoo = _enrich_with_sector(yahoo)
        country_lc = country.lower()
        yahoo = [
            c for c in yahoo
            if (c.get("country") or "").lower() == country_lc
        ]
    # Wikidata only when Yahoo found nothing — small markets like
    # Liechtenstein. When Yahoo had results, mixing in zero-cap
    # Wikidata rows muddies both the count and the treemap.
    if not yahoo and qid:
        companies = _wikidata_companies(country_qid=qid, limit=limit)
        companies = _enrich_with_sector(companies)
    else:
        # Already enriched above; just trim.
        companies = yahoo[:limit]

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

    # Yahoo won't filter by sector globally — sweep major markets
    # individually. The same company appears in multiple region
    # screens as primary + foreign mirrors (NVDA on us, NVD.DE on de,
    # ASML.AS on nl, ASML.SW on ch). We pin each row to the region we
    # screened it from, then after sector enrichment drops the rows
    # whose home-country doesn't match — that's the cheapest reliable
    # way to keep one listing per company without a brittle name match.
    yahoo_combined = []
    if sector:
        per_region = max(20, limit // 2)
        for region_code in ("us", "gb", "de", "fr", "jp", "cn", "in",
                            "ch", "nl", "ca", "kr", "tw"):
            rows = _yf_screen(region=region_code, sector=sector, limit=per_region)
            for r in rows:
                r["_screen_region"] = region_code
            yahoo_combined.extend(rows)

        # Cheap pass: dedupe by ticker — the same listing sometimes
        # surfaces in two region screens.
        by_ticker = {}
        for c in yahoo_combined:
            t = c.get("ticker")
            if t and t not in by_ticker:
                by_ticker[t] = c
        yahoo_combined = list(by_ticker.values())

    # Enrich with sector + home country before the home-region filter.
    # `_enrich_with_sector` fills `country` from yfinance's per-ticker
    # `info` payload (NVDA → "United States"; NVD.DE → "United States"
    # too — yfinance gives the company's HQ, not the listing
    # exchange). That's exactly what we need.
    yahoo_combined = _enrich_with_sector(yahoo_combined)

    if sector and yahoo_combined:
        # Keep only rows whose company HQ matches the region we
        # screened them from. NVDA (HQ=US, screened on us) ✓.
        # NVD.DE (HQ=US, screened on de) ✗ — already represented
        # by NVDA. ASML.AS (HQ=NL, screened on nl) ✓; ASML.SW
        # (HQ=NL, screened on ch) ✗.
        YF_REGION_TO_COUNTRY = {v[0]: k for k, v in COUNTRY_MAP.items()}
        def _match_home(row):
            r = row.get("_screen_region")
            home = (row.get("country") or "").strip()
            if not home:
                # No HQ info — fall back to the looser USD-currency
                # signal so we don't drop everything.
                return (row.get("currency") or "").upper() == "USD" and r == "us"
            expected = YF_REGION_TO_COUNTRY.get(r)
            if not expected:
                return False
            # Yahoo's `country` can be a name or an ISO code — match either.
            return home.lower() == expected.lower() or home.lower() == r

        yahoo_combined = [c for c in yahoo_combined if _match_home(c)]
        # Sort by USD market cap and trim
        yahoo_combined.sort(key=lambda x: -(x.get("market_cap_usd") or 0))
        yahoo_combined = yahoo_combined[:limit]
        # Drop the internal field before returning
        for c in yahoo_combined:
            c.pop("_screen_region", None)

    # Only fall back to Wikidata when Yahoo returned NOTHING. Mixing
    # zero-cap Wikidata rows with Yahoo rows pollutes the treemap
    # (zero-area tiles); when the treemap can't be built we want the
    # Wikidata list as the primary view, surfaced via the panel's
    # opt-in list toggle.
    companies = yahoo_combined
    if not companies and qid:
        companies = _wikidata_companies(industry_qid=qid, limit=limit)
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
