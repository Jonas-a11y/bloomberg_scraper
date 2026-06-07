"""GDELT 2.0 Doc API fetcher for billionaire news.

The GDELT 2.0 Doc API indexes worldwide news from Feb 2015 onwards. It's free,
keyless, and returns structured article metadata (title, URL, source, date,
language) for a given query.

We score articles by keyword importance so the chart can surface the few
genuinely interesting events instead of every passing mention. Earnings,
lawsuits, deaths, divorces, IPOs, acquisitions all bump the score.
"""
import logging
import time
from datetime import datetime, timedelta
from urllib.parse import quote_plus

from curl_cffi import requests

logger = logging.getLogger(__name__)

GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"

# GDELT throttles below 5s between requests and returns 429 with a plaintext
# body asking to slow down. On a 429 we sleep then retry once.
GDELT_RETRY_AFTER_SEC = 12

# Keywords that mark an article as more newsworthy than a routine mention.
# Score is summed across matches, capped via min(...) at the call site.
# Avoid generic terms like "billionaire" / "wealth" / "ceo" / "founder" —
# they match every article and drown out genuine event keywords.
IMPORTANCE_KEYWORDS = {
    # Major life events
    "dies": 10, "died": 10, "death": 8, "obituary": 10,
    "divorce": 8, "divorces": 8, "settlement": 5, "settles": 4,
    "marries": 6, "engaged": 4,
    # Legal / regulatory
    "lawsuit": 7, "sued": 6, "indicted": 9, "charged": 6,
    "fraud": 8, "convicted": 9, "sentenced": 9, "fined": 5,
    "investigation": 5, "subpoena": 6, "settles lawsuit": 8,
    # Business / corporate
    "ipo": 7, "acquires": 6, "acquisition": 6, "merger": 6,
    "stake": 5, "buys": 4, "sells": 4, "divest": 5,
    "earnings": 4, "quarterly": 3, "revenue": 3,
    "resigns": 7, "steps down": 7,
    # Wealth-specific events
    "richest": 5, "donates": 5, "philanthropy": 4,
    # Political / scandal
    "scandal": 7, "controversy": 5, "accused": 6, "allegations": 6,
}

# Sources we consider authoritative — bumps the score by a fixed amount.
TRUSTED_SOURCES = {
    "reuters.com", "bloomberg.com", "wsj.com", "ft.com",
    "nytimes.com", "forbes.com", "cnbc.com", "bbc.com",
    "apnews.com", "theguardian.com", "washingtonpost.com",
    "economist.com",
}


# Patterns that mark an article as a "rich list" / ranking page rather than
# an event. These get capped scoring — they're cited everywhere but they're
# not what users want to see as profile annotations.
LISTICLE_MARKERS = (
    "richest", "rich list", "billionaires index",
    " 100 ", " 200 ", " 50 ", " 500 ", " 40 ",
    "world's billion", "top billion", "wealthiest",
)


def _is_listicle(title):
    if not title:
        return False
    t = title.lower()
    return any(m in t for m in LISTICLE_MARKERS)


def score_importance(title, source_url):
    """Heuristic 0-N importance score. Higher = more chart-worthy.

    Title keywords drive most of the score; trusted-source bonus tips the
    tie-breaker between two otherwise-equal stories. Rich-list pages return
    0 — they're not events, just ranking snapshots.
    """
    title_lower = (title or "").lower()
    if _is_listicle(title_lower):
        return 0
    score = 0
    for kw, weight in IMPORTANCE_KEYWORDS.items():
        if kw in title_lower:
            score += weight
    if source_url:
        for src in TRUSTED_SOURCES:
            if src in source_url.lower():
                score += 3
                break
    return score


def _domain_of(url):
    if not url:
        return None
    try:
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
        return host.removeprefix("www.")
    except Exception:
        return None


def fetch_news_for_person(name, since_date=None, until_date=None, limit=50, timeout=20):
    """Query GDELT for articles mentioning a person. Returns list of dicts.

    Each dict: {article_date, title, url, source, importance}.
    Dates are ISO YYYY-MM-DD strings.

    GDELT requires YYYYMMDDHHMMSS for startdatetime/enddatetime. If since_date
    is None we default to 30 days ago — the daily refresh window.
    """
    if not name:
        return []
    if since_date is None:
        since_date = (datetime.now() - timedelta(days=30)).date().isoformat()
    if until_date is None:
        until_date = datetime.now().date().isoformat()

    start_dt = since_date.replace("-", "") + "000000"
    end_dt = until_date.replace("-", "") + "235959"

    # GDELT's query syntax: a quoted phrase forces exact match. English-only
    # to avoid drowning in non-Latin transliterations.
    query = f'"{name}" sourcelang:eng'
    params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": str(min(limit, 250)),
        "sort": "DateDesc",
        "startdatetime": start_dt,
        "enddatetime": end_dt,
    }

    try:
        r = requests.get(
            GDELT_DOC_API,
            params=params,
            timeout=timeout,
            impersonate="chrome",
        )
    except Exception as e:
        logger.warning(f"GDELT request failed for {name!r}: {e}")
        return []

    # GDELT throttles with 429 + plaintext body. Wait and retry once before
    # giving up — keeps the backfill walking when their queue gets busy.
    if r.status_code == 429 or (
        r.status_code == 200 and "limit requests" in r.text[:200].lower()
    ):
        logger.info(f"GDELT throttled, sleeping {GDELT_RETRY_AFTER_SEC}s then retrying {name!r}")
        time.sleep(GDELT_RETRY_AFTER_SEC)
        try:
            r = requests.get(
                GDELT_DOC_API,
                params=params,
                timeout=timeout,
                impersonate="chrome",
            )
        except Exception as e:
            logger.warning(f"GDELT retry failed for {name!r}: {e}")
            return []

    if r.status_code != 200:
        logger.warning(f"GDELT {r.status_code} for {name!r}")
        return []

    try:
        data = r.json()
    except Exception:
        # GDELT occasionally returns HTML errors with a 200 status
        return []

    articles = data.get("articles", [])
    out = []
    for art in articles:
        title = art.get("title")
        url = art.get("url")
        seendate = art.get("seendate")  # YYYYMMDDTHHMMSSZ
        if not title or not url or not seendate:
            continue
        try:
            article_date = (
                f"{seendate[0:4]}-{seendate[4:6]}-{seendate[6:8]}"
            )
        except Exception:
            continue
        source = art.get("domain") or _domain_of(url)
        out.append({
            "article_date": article_date,
            # GDELT timestamps include hh:mm:ss, so we always have day-level
            # precision — these can ride the chart curve safely.
            "date_precision": "day",
            "title": title.strip(),
            "url": url,
            "source": source,
            "importance": score_importance(title, url),
        })
    return out
