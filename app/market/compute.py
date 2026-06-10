"""Public-market deep-dive compute helpers.

Two entry points:

* :func:`market_by_country` — top public companies HQ'd in `country`,
  Yahoo primary, Wikidata fallback when Yahoo has no coverage.
* :func:`market_by_industry` — top public companies in `industry`
  worldwide. Sweeps major markets, dedupes across regions, falls
  back to Wikidata when Yahoo's GICS sector mapping is empty.

Both raise ``RuntimeError`` when Yahoo throttles us mid-pagination
so the cache layer doesn't memoise a partial result.
"""
from __future__ import annotations

from datetime import datetime

from .constants import (
    COUNTRY_MAP,
    INDUSTRY_TO_WIKIDATA_QID,
    INDUSTRY_TO_YF_SECTOR,
    REGION_FC,
    YF_REGION_TO_COUNTRY,
    country_qid,
    yf_region,
)
from .screener import collapse_share_classes, enrich_with_sector, yf_screen
from .wikidata import wikidata_companies


def market_by_country(country: str, limit: int):
    """Top public companies headquartered in ``country``."""
    region = yf_region(country)
    qid = country_qid(country)

    # Pull a generous oversample. For non-US countries, only a small
    # fraction of region=XX listings are real local companies — the
    # top of the list is dominated by foreign mirrors. We paginate to
    # 2000 rows and lower the cap floor to $100M so DAX/MDAX names
    # surface; for the US case (where almost all results are real US
    # companies) we cap at 250 to keep things fast.
    if region == "us":
        yahoo, truncated = yf_screen(region=region, limit=250)
    elif region:
        yahoo, truncated = yf_screen(
            region=region, limit=2000, min_market_cap=100_000_000,
        )
    else:
        yahoo, truncated = [], False
    if truncated and len(yahoo) < 5:
        # Don't memoise rate-limited partials. Caller treats this
        # as "compute failed, retry later"; cache stays empty.
        raise RuntimeError(
            f"Yahoo screener truncated for region={region}: "
            f"only {len(yahoo)} rows before bailing. Not caching."
        )

    # Pass 1 (cheap): drop the obvious foreign mirrors via
    # ``financial_currency``. Real local companies report financials
    # in the local currency; foreign mirrors report in USD or their
    # HQ currency. For non-eurozone this is conclusive; for eurozone
    # it narrows ~750 → ~150, leaving the per-ticker ``info`` filter
    # to disambiguate Germany from France/Netherlands/Italy.
    accept_fc = REGION_FC.get(region)
    if accept_fc and yahoo:
        yahoo = [
            c for c in yahoo
            if not c.get("financial_currency")
            or c.get("financial_currency") in accept_fc
        ]

    # Pass 2 (accurate): enrich with HQ via ``info.country``, then keep
    # only those whose HQ matches this country. Catches eurozone
    # cross-listings the cheap filter couldn't (ASML.DE → Netherlands
    # not Germany).
    if region and yahoo:
        # The cheap pass has already cut foreign-currency mirrors;
        # the survivors are mostly real candidates. Cap at 300 to bound
        # enrichment cost — already sorted by cap so we keep megacaps.
        yahoo = yahoo[:300]
        yahoo = enrich_with_sector(yahoo)
        country_lc = country.lower()
        yahoo = [
            c for c in yahoo
            if (c.get("country") or "").lower() == country_lc
        ]
        yahoo = collapse_share_classes(yahoo)

    # Wikidata only when Yahoo found nothing — small markets like
    # Liechtenstein. When Yahoo had results, mixing in zero-cap
    # Wikidata rows muddies both the count and the treemap.
    if not yahoo and qid:
        companies = wikidata_companies(country_qid=qid, limit=limit)
        companies = enrich_with_sector(companies)
    else:
        # Already enriched + deduped above; just trim.
        companies = yahoo[:limit]

    if not companies:
        return {
            "country": country,
            "total_market_cap_usd": 0,
            "companies": [],
            "sectors": [],
            "note": (
                f"No data for '{country}' — Yahoo and Wikidata both came back "
                f"empty. This usually means the country isn't in our region "
                f"map (see app/market/constants.py:COUNTRY_MAP) or the SPARQL "
                f"query timed out."
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

    return {
        "country": country,
        "total_market_cap_usd": total,
        "companies": companies,
        "sectors": sectors,
        "sources": sources,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


def market_by_industry(industry: str, limit: int):
    """Top public companies in ``industry`` worldwide, by market cap."""
    sector = INDUSTRY_TO_YF_SECTOR.get(industry, industry)
    qid = INDUSTRY_TO_WIKIDATA_QID.get(industry)

    # Yahoo won't filter by sector globally — sweep major markets
    # individually. The same company appears in multiple region screens
    # as primary + foreign mirrors (NVDA on us, NVD.DE on de, ASML.AS
    # on nl, ASML.SW on ch). We pin each row to the region we screened
    # it from, then drop rows whose home-country doesn't match — that's
    # the cheapest reliable way to keep one listing per company without
    # a brittle name match.
    yahoo_combined = []
    if sector:
        per_region = max(20, limit // 2)
        # Track aggregate truncation. A single truncated region for
        # an industry sweep is OK (others cover for it), but if MOST
        # truncated, the dataset misrepresents the global picture.
        truncated_regions = 0
        total_regions = 0
        for region_code in ("us", "gb", "de", "fr", "jp", "cn", "in",
                            "ch", "nl", "ca", "kr", "tw"):
            total_regions += 1
            rows, region_truncated = yf_screen(
                region=region_code, sector=sector, limit=per_region,
            )
            if region_truncated and len(rows) < 3:
                truncated_regions += 1
            for r in rows:
                r["_screen_region"] = region_code
            yahoo_combined.extend(rows)
        if truncated_regions >= total_regions // 2:
            raise RuntimeError(
                f"Yahoo screener truncated for {truncated_regions}/{total_regions} "
                f"regions on industry={sector}. Not caching."
            )

        # Cheap pass: dedupe by ticker — the same listing sometimes
        # surfaces in two region screens.
        by_ticker = {}
        for c in yahoo_combined:
            t = c.get("ticker")
            if t and t not in by_ticker:
                by_ticker[t] = c
        yahoo_combined = list(by_ticker.values())

    # Enrich with sector + home country before the home-region filter.
    # ``enrich_with_sector`` fills ``country`` from yfinance's
    # per-ticker ``info`` payload (NVDA → "United States"; NVD.DE →
    # "United States" too — yfinance gives the company's HQ, not the
    # listing exchange). That's exactly what we need.
    yahoo_combined = enrich_with_sector(yahoo_combined)

    if sector and yahoo_combined:
        # Keep only rows whose company HQ matches the region we
        # screened them from. NVDA (HQ=US, screened on us) ✓.
        # NVD.DE (HQ=US, screened on de) ✗ — already represented by
        # NVDA. ASML.AS (HQ=NL, screened on nl) ✓; ASML.SW (HQ=NL,
        # screened on ch) ✗.
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
            # Yahoo's ``country`` can be a name or an ISO code — match either.
            return home.lower() == expected.lower() or home.lower() == r

        yahoo_combined = [c for c in yahoo_combined if _match_home(c)]
        # Collapse share classes (GOOGL+GOOG, BRK-A+BRK-B, etc.) AFTER
        # the home-country filter; otherwise we'd dedupe across regions
        # and risk dropping legitimately separate listings.
        yahoo_combined = collapse_share_classes(yahoo_combined)
        yahoo_combined.sort(key=lambda x: -(x.get("market_cap_usd") or 0))
        yahoo_combined = yahoo_combined[:limit]
        # Drop the internal field before returning.
        for c in yahoo_combined:
            c.pop("_screen_region", None)

    # Only fall back to Wikidata when Yahoo returned NOTHING. Mixing
    # zero-cap Wikidata rows with Yahoo rows pollutes the treemap
    # (zero-area tiles); when the treemap can't be built we want the
    # Wikidata list as the primary view, surfaced via the panel's
    # opt-in list toggle.
    companies = yahoo_combined
    if not companies and qid:
        companies = wikidata_companies(industry_qid=qid, limit=limit)
        companies = enrich_with_sector(companies)

    if not companies:
        return {
            "industry": industry,
            "yahoo_sector": sector,
            "total_market_cap_usd": 0,
            "companies": [],
            "countries": [],
            "note": (
                f"No data for '{industry}' — Yahoo's screener returned empty "
                f"and there's no Wikidata QID mapped (see "
                f"INDUSTRY_TO_WIKIDATA_QID in app/market/constants.py)."
            ),
            "sources": [],
        }

    total = sum(c.get("market_cap_usd") or 0 for c in companies)
    # Country code normalization: Yahoo returns either a country name
    # ("United States") or a region code ("us"). Collapse to the
    # canonical name so the breakdown doesn't double-count.
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

    return {
        "industry": industry,
        "yahoo_sector": sector,
        "total_market_cap_usd": total,
        "companies": companies,
        "countries": countries,
        "sources": sources,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }
