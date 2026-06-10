"""Wikidata SPARQL fallback for the public-market endpoints.

Used when Yahoo's screener returns nothing for the requested
country (small markets like Liechtenstein, Cyprus). Wikidata gives us
a name + headquarters country + ticker (where set) + inception year.
We don't get live market caps from Wikidata — those rows surface as
zero-cap entries the UI shows in the opt-in list view but skips on
the treemap.
"""
from __future__ import annotations

import logging

from curl_cffi import requests as cffi_requests

logger = logging.getLogger(__name__)


def wikidata_companies(country_qid=None, industry_qid=None, limit=25):
    """SPARQL-fetch public companies from Wikidata. Returns a list of
    normalized dicts with the same shape as the Yahoo path so callers
    can mix the two seamlessly."""
    where_parts = [
        # Instance of "publicly traded company" (Q891723) keeps the
        # query focused on stock-listed firms.
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
