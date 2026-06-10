"""Yahoo Screener wrapper + sector enrichment + share-class dedup.

These three together give the market endpoint its data pipeline:

1. :func:`yf_screen` paginates through Yahoo's screener with retry
   on rate-limit and FX-converts each row's market cap to USD.
2. :func:`enrich_with_sector` fans out per-ticker ``info`` calls to
   fill in sector / industry / HQ country (the screener payload
   doesn't carry them).
3. :func:`collapse_share_classes` folds Yahoo's
   voting/non-voting/preferred entries (GOOGL+GOOG, BRK-A+BRK-B,
   Samsung common+preferred) into one row per company.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor

from .constants import primary_market
from .fx import fx_rates, to_usd

logger = logging.getLogger(__name__)


# Per-ticker ``info`` cache (sector/industry/country lookups). Same
# ticker can appear twice across multi-region merges and we only want
# to fetch each once per cache window. Hour-scoped.
_INFO_TTL_SEC = 6 * 3600
_info_cache = {}  # {ticker: (timestamp, dict)}


def _cached_info(ticker):
    hit = _info_cache.get(ticker)
    if hit and (time.time() - hit[0]) < _INFO_TTL_SEC:
        return hit[1]
    return None


def _put_info(ticker, data):
    _info_cache[ticker] = (time.time(), data)


def yf_screen(region=None, sector=None, limit=25, min_market_cap=1_000_000_000):
    """Query Yahoo Screener for equities matching the filters, ranked
    by market cap descending. Returns ``(rows, truncated)`` where
    ``truncated=True`` means we hit a rate-limit / error mid-pagination
    and the result is incomplete — callers that cache should refuse
    to memoise truncated results.

    Yahoo caps ``size`` at 250 per call, so for ``limit > 250`` we
    paginate via ``offset``. We bail as soon as a page comes back short
    (natural EOF) or we exhaust our retries on a rate-limit response.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed; market endpoints disabled")
        return [], False

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
    truncated = False
    while remaining > 0:
        page_size = min(remaining, PAGE)
        # Retry-with-backoff on rate-limit. Yahoo's "Too Many Requests"
        # is transient; a few short pauses usually unsticks us. We cap
        # retries so a sustained throttle bubbles up truncated=True
        # instead of hanging the caller forever.
        result = None
        for attempt in range(3):
            try:
                result = yf.screen(
                    query,
                    sortField="intradaymarketcap",
                    sortAsc=False,
                    size=page_size,
                    offset=offset,
                )
                break
            except Exception as e:
                msg = str(e).lower()
                rate_limited = "too many requests" in msg or "429" in msg
                if rate_limited and attempt < 2:
                    delay = (attempt + 1) * 5  # 5s, 10s
                    logger.info(
                        f"yfinance screener rate-limited at offset={offset}, "
                        f"retry in {delay}s (attempt {attempt+1}/3)"
                    )
                    time.sleep(delay)
                    continue
                logger.warning(
                    f"yfinance screener failed at offset={offset}: {e}"
                )
                break
        if result is None:
            truncated = True
            break
        page_quotes = result.get("quotes") if isinstance(result, dict) else []
        if not page_quotes:
            # Empty page is a natural EOF (no truncation).
            break
        quotes.extend(page_quotes)
        if len(page_quotes) < page_size:
            break  # natural end of results
        remaining -= page_size
        offset += page_size
    if not quotes:
        return [], truncated

    # When we asked for a specific region, filter out depository
    # receipts and other non-primary listings. Yahoo's `market` field
    # tags primary listings as `<region>_market` and DRs as `dr_market`.
    primary = primary_market(region)
    if primary:
        quotes = [q for q in quotes if q.get("market") == primary]

    # Fetch FX rates once for every currency present in the response,
    # in a single batched call.
    rates = fx_rates({q.get("currency") for q in quotes})

    out = []
    for q in quotes:
        cap_local = q.get("marketCap")
        currency = (q.get("currency") or "").upper()
        cap_usd = to_usd(cap_local, currency, rates)
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
    return out, truncated


def enrich_with_sector(rows, max_workers=30):
    """Yahoo's ``screen()`` payload carries marketCap but not sector
    — those only live on the per-ticker ``info`` endpoint. Fanned out
    concurrently so 100 tickers complete in ~2s instead of ~30s
    sequentially.

    Per-ticker results are cached at the module level so subsequent
    panel opens are free."""
    try:
        import yfinance as yf
    except ImportError:
        return rows

    # Collect tickers that still need enrichment, deduped — same
    # ticker can appear twice across multi-region merges and we only
    # want to fetch each once per cache window.
    todo = []
    for row in rows:
        ticker = row.get("ticker")
        if not ticker or row.get("sector"):
            continue
        if _cached_info(ticker) is None:
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
                _put_info(t, data)

    for row in rows:
        ticker = row.get("ticker")
        if not ticker:
            continue
        cached = _cached_info(ticker) or {}
        if cached:
            row["sector"] = cached.get("sector") or row.get("sector")
            row["industry"] = cached.get("industry") or row.get("industry")
            row["country"] = cached.get("country") or row.get("country")
    return rows


def collapse_share_classes(rows, cap_tolerance=0.85):
    """Merge same-company share-class duplicates.

    Yahoo's screener returns voting/non-voting share classes as
    separate rows even though they're the same underlying company:
        GOOGL ($4442B) + GOOG ($4418B)        → both "Alphabet Inc." (~0.5% apart)
        BRK-A ($1049B) + BRK-B ($1052B)       → both "Berkshire Hathaway Inc." (~0.3%)
        005930.KS ($1307B) + 005935.KS ($831B) → both "Samsung Electronics Co., Ltd." (~63%)
    The treemap and sector breakdown then double-count those companies.

    Rule: rows whose ``name`` matches exactly (after upper-casing and
    trimming common suffixes) AND whose market caps are within
    ``cap_tolerance`` of each other are treated as the same company.
    Keep the row with the largest market cap (typically the more
    liquid voting class) and discard the rest.

    Tolerance is generous (85%) by design — Samsung's preferred trades
    at a substantial discount to the common, but they're still the
    same underlying company. Two genuinely separate companies almost
    never share an exact registered ``longName``, so a permissive cap
    rule is the right trade-off.

    Levenshtein-based fuzzy matching was considered and rejected:
    real share-class duplicates have IDENTICAL names (distance 0),
    while legitimately separate companies whose names happen to look
    alike at distance 1-3 (e.g. "Comcast Corp" vs "Comcast
    Communications") would get incorrectly collapsed.
    """
    if not rows:
        return rows

    def _norm(name):
        n = (name or "").upper()
        for suffix in (" CORPORATION", " CORP.", " CORP", " INC.", " INC",
                       " LIMITED", " LTD.", " LTD", " HOLDINGS", " HOLDING",
                       " GROUP", " PLC", " AG", " S.A.", " SA", " SE",
                       " CO.", " CO", " N.V.", " NV"):
            if n.endswith(suffix):
                n = n[: -len(suffix)].strip()
        return n.strip(".,").strip()

    # Bucket rows by normalized name. Within each bucket, group by cap
    # proximity: a row joins an existing group if its cap is within
    # ``cap_tolerance`` of the group's representative cap.
    by_name = {}
    for r in rows:
        key = _norm(r.get("name"))
        if not key:
            # Without a usable name we can't dedupe — keep as-is.
            by_name.setdefault(("__no_name__", id(r)), []).append(r)
            continue
        by_name.setdefault(key, []).append(r)

    out = []
    for key, group in by_name.items():
        if len(group) == 1:
            out.append(group[0])
            continue
        # Sort the group by cap descending; walk it merging cap-close
        # rows onto the current representative.
        group.sort(key=lambda r: -(r.get("market_cap_usd") or 0))
        kept = []
        for r in group:
            cap = r.get("market_cap_usd") or 0
            collapsed = False
            for k in kept:
                k_cap = k.get("market_cap_usd") or 0
                if k_cap and cap and abs(cap - k_cap) / max(cap, k_cap) <= cap_tolerance:
                    # Same share class — drop this row, the bigger
                    # one stays. (Group is cap-sorted, so `k` is
                    # always the bigger one.)
                    collapsed = True
                    break
            if not collapsed:
                kept.append(r)
        out.extend(kept)
    return out
