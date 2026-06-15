"""On-disk cache for the slow Insights endpoints.

Why on-disk? Insights endpoints scan tens of thousands of rows
(top-over-time-series joins wealth_history × historical_rankings,
wealth-correlation does ~125k pair correlations on a 500-person
matrix, etc.). Computing on every request makes the dashboard feel
sluggish even for the second visitor of the day.

Strategy: precompute on a fixed schedule (every scrape + on startup),
serve from cache on every request, refresh in the background when a
cache entry exceeds its TTL.

Storage: a single `insights_cache` table in bloomberg.db, key =
(endpoint, params_signature). Value = compressed JSON payload. We
keep it in the main DB rather than a separate file so backups /
volume mounts pick it up automatically.
"""
from __future__ import annotations

import gzip
import json
import logging
import time
from typing import Callable

from app.database import get_db

logger = logging.getLogger(__name__)


# Default TTL — entries older than this are considered stale and will
# be refreshed in the background on the next request. Stale entries
# are still SERVED immediately so the user never waits on a recompute.
_DEFAULT_TTL_SEC = 6 * 3600


def _ensure_table(conn=None):
    """Create the cache table on first use. Idempotent. Cheap enough
    (CREATE TABLE IF NOT EXISTS = no-op when present) that we run it
    on every cache operation — handles the test scenario where the
    fixture rebuilds the DB after this module already imported."""
    own = conn is None
    if own:
        conn = get_db()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS insights_cache (
                key         TEXT PRIMARY KEY,
                computed_at REAL NOT NULL,
                duration_ms INTEGER,
                payload     BLOB NOT NULL
            )
            """
        )
        if own:
            conn.commit()
    finally:
        if own:
            conn.close()


def _key(endpoint: str, params: dict | None) -> str:
    """Stable string key for (endpoint, sorted-params). Empty/None
    values are dropped so `?n=12&days=` matches `?n=12`."""
    if not params:
        return endpoint
    parts = [
        f"{k}={v}"
        for k, v in sorted(params.items())
        if v is not None and v != ""
    ]
    return f"{endpoint}?" + "&".join(parts)


def get_cached(endpoint: str, params: dict | None = None):
    """Return (payload, age_sec) if a cache entry exists, else None."""
    key = _key(endpoint, params)
    conn = get_db()
    try:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT computed_at, payload FROM insights_cache WHERE key = ?",
            (key,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        payload = json.loads(gzip.decompress(row["payload"]))
    except Exception as e:
        logger.warning(f"insights_cache: corrupt entry for {key}: {e}")
        return None
    age = time.time() - float(row["computed_at"])
    return payload, age


def put(endpoint: str, params: dict | None, payload, duration_ms: int = 0):
    """Store a freshly-computed payload."""
    key = _key(endpoint, params)
    blob = gzip.compress(
        json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    )
    conn = get_db()
    try:
        _ensure_table(conn)
        conn.execute(
            """
            INSERT INTO insights_cache (key, computed_at, duration_ms, payload)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                computed_at = excluded.computed_at,
                duration_ms = excluded.duration_ms,
                payload     = excluded.payload
            """,
            (key, time.time(), duration_ms, blob),
        )
        conn.commit()
    finally:
        conn.close()


def cached_or_compute(
    endpoint: str,
    params: dict | None,
    compute: Callable,
    ttl_sec: int = _DEFAULT_TTL_SEC,
):
    """The serving primitive: return the cached payload if fresh,
    serve-stale-then-refresh-in-background if stale, otherwise
    compute + cache + return.

    `compute` is a zero-arg callable that returns the JSON-serialisable
    payload. It runs synchronously on cache miss / stale (background
    refresh logic lives one layer up — see precompute_in_background).
    """
    cached = get_cached(endpoint, params)
    if cached:
        payload, age = cached
        if age < ttl_sec:
            return payload, "fresh", int(age)
        # Stale — return it but schedule a background refresh
        precompute_in_background(endpoint, params, compute)
        return payload, "stale", int(age)

    # Cold cache — compute synchronously this one time
    started = time.time()
    payload = compute()
    duration_ms = int((time.time() - started) * 1000)
    put(endpoint, params, payload, duration_ms)
    return payload, "miss", 0


# ---------------------------------------------------------------------------
# Background precompute
# ---------------------------------------------------------------------------
import threading

_refresh_lock = threading.Lock()
_refresh_inflight: set[str] = set()


def precompute_in_background(
    endpoint: str, params: dict | None, compute: Callable,
):
    """Recompute (endpoint, params) on a background thread.

    Dedupes by key — if a refresh for the same key is already running
    we don't queue a second one. Used by `cached_or_compute` for the
    stale path and by the post-scrape warmer to populate cold caches.
    """
    key = _key(endpoint, params)
    with _refresh_lock:
        if key in _refresh_inflight:
            return
        _refresh_inflight.add(key)

    def _run():
        try:
            started = time.time()
            payload = compute()
            duration_ms = int((time.time() - started) * 1000)
            put(endpoint, params, payload, duration_ms)
            logger.info(
                f"insights_cache: refreshed {key} in {duration_ms}ms"
            )
        except Exception:
            logger.exception(f"insights_cache: refresh failed for {key}")
        finally:
            with _refresh_lock:
                _refresh_inflight.discard(key)

    threading.Thread(target=_run, daemon=True).start()


# ---------------------------------------------------------------------------
# Warm the cache on startup + after each scrape.
# ---------------------------------------------------------------------------

# (endpoint, params, compute_fn) — populated by warm_all() lazily so
# we don't pay an import-time cost on cold tests / migrations.
def _build_warmup_specs() -> list[tuple[str, dict, Callable]]:
    """The list of (endpoint, params, compute_fn) tuples we proactively
    refresh after every scrape. Picked to match what the UI fetches on
    Insights tab load + the deep-dive panel's defaults."""
    from datetime import datetime
    from app import insights as ix

    year = datetime.now().year
    # Warmup specs call the compute helpers in app.insights directly
    # (NOT the wrapped routes in app.routes.insights). The route
    # handlers go through cached_or_compute themselves; if a spec
    # called the route handler, warm_all → cached_or_compute →
    # spec.fn() → cached_or_compute would recurse on the same key
    # and a partial inner result could overwrite the freshly-computed
    # outer one.
    return [
        # Demographics + leaderboards used on Insights tab open
        (
            "/insights/inequality",
            {"year_from": 2001, "year_to": year},
            lambda: ix.inequality(2001, year, None, None),
        ),
        (
            "/insights/count-over-time",
            {"year_from": 2001, "year_to": year, "by": "country"},
            lambda: ix.count_over_time(2001, year, "country"),
        ),
        (
            "/insights/top-over-time",
            {"n": 12, "year_from": 2001, "year_to": year},
            lambda: ix.top_over_time(12, 2001, year, None, None),
        ),
        (
            "/insights/top-over-time-series",
            {"n": 12, "year_from": 2001, "year_to": year},
            lambda: ix.top_over_time_series(12, 2001, year, None, None),
        ),
        # source-gap intentionally omitted — UI no longer surfaces it
        (
            "/insights/cohort-survival",
            {"year": 2001, "top": 100},
            lambda: ix.cohort_survival(2001, 100),
        ),
        (
            "/insights/geo-migration",
            None,
            lambda: ix.geo_migration(),
        ),
        # Concentration: ~1.3s cold on the 1.8M-row wealth_history table
        # — the biggest single response-time win once it's prewarmed.
        (
            "/analytics/concentration",
            {"min_count": 100},
            _concentration_compute,
        ),
        # data-range: cheap, but called on every page load. Keep it warm.
        (
            "/billionaires/data-range",
            None,
            _data_range_compute,
        ),
        # Wealth correlation at the four UI presets — these are the
        # slowest endpoints, biggest win from caching.
        *[
            (
                "/insights/wealth-correlation",
                {"n": n, "days": 365, "threshold": 0.7},
                lambda n=n: ix.wealth_correlation(n, 365, 0.7, None),
            )
            for n in (30, 100, 250, 500)
        ],
        # Public-market deep-dive: the country / industry buckets the
        # user is most likely to click. Yahoo Screener is slow (5-10s
        # cold), so a daily warm means the deep-dive panel never blocks
        # on first open. The full coverage is opt-in via the
        # /api/scraper/insights-cache/warm endpoint.
        *_market_warmup_specs(),
    ]


def _concentration_compute():
    """Standalone compute for /analytics/concentration so the warmup
    spec doesn't import the route module (which would create a cycle:
    analytics.py → insights_cache → analytics.py)."""
    from app.database import get_db
    conn = get_db()
    try:
        cursor = conn.execute("""
            WITH ranked AS (
                SELECT date, net_worth_usd,
                       ROW_NUMBER() OVER (PARTITION BY date ORDER BY net_worth_usd DESC) AS rk
                FROM wealth_history
            )
            SELECT date,
                   SUM(net_worth_usd)                                     AS total,
                   SUM(CASE WHEN rk = 1   THEN net_worth_usd ELSE 0 END)  AS top_1,
                   SUM(CASE WHEN rk <= 10 THEN net_worth_usd ELSE 0 END)  AS top_10,
                   SUM(CASE WHEN rk <= 100 THEN net_worth_usd ELSE 0 END) AS top_100,
                   COUNT(*)                                               AS count
            FROM ranked
            GROUP BY date
            HAVING count >= ?
            ORDER BY date
        """, (100,))
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def _data_range_compute():
    """Standalone compute for /billionaires/data-range — see comment on
    _concentration_compute for why we don't import the route module."""
    from app.database import get_db
    conn = get_db()
    try:
        bloom = conn.execute(
            "SELECT MIN(date) AS min_date, MAX(date) AS max_date FROM wealth_history"
        ).fetchone()
        forbes = conn.execute(
            "SELECT MIN(year) AS min_y, MAX(year) AS max_y FROM historical_rankings"
        ).fetchone()
    finally:
        conn.close()
    candidates_min, candidates_max = [], []
    if bloom and bloom["min_date"]:
        candidates_min.append(bloom["min_date"])
        candidates_max.append(bloom["max_date"])
    if forbes and forbes["min_y"]:
        candidates_min.append(f"{forbes['min_y']}-01-01")
        candidates_max.append(f"{forbes['max_y']}-12-31")
    if not candidates_min:
        return {"min_date": None, "max_date": None}
    return {
        "min_date": min(candidates_min),
        "max_date": max(candidates_max),
        "bloomberg_start": bloom["min_date"] if bloom else None,
        "forbes_years": [forbes["min_y"], forbes["max_y"]] if forbes and forbes["min_y"] else None,
    }


def _market_warmup_specs():
    """Build (endpoint, params, fn) tuples for the public-market
    endpoints. Pulled out into a helper because the country/industry
    list is long enough to be its own thing.

    We import locally to avoid a circular import at module-load time
    (market.py → insights_cache via the wrapped endpoints). Crucially
    we call the compute helpers in ``app.market`` directly, NOT the
    wrapped routes — otherwise the warmup invokes cached_or_compute
    → spec.fn → cached_or_compute again on the same key, and a partial
    inner result can overwrite the freshly-computed outer one."""
    from app import market as mk

    # The 12 countries with the deepest billionaire coverage in our
    # dataset — opening the deep-dive on any of these is the primary
    # use case. Smaller countries fall back to on-demand caching.
    COUNTRIES = [
        "United States", "China", "Germany", "France", "United Kingdom",
        "Japan", "India", "Switzerland", "Netherlands", "Canada",
        "South Korea", "Taiwan",
    ]
    # Industries that appear most often in the billionaire dataset.
    INDUSTRIES = [
        "Technology", "Finance & Investments", "Healthcare", "Energy",
        "Real Estate", "Fashion & Retail", "Food & Beverage",
        "Manufacturing", "Automotive", "Media & Entertainment",
    ]
    out = []
    for c in COUNTRIES:
        out.append((
            "/market/by-country",
            {"country": c, "limit": 100},
            lambda c=c: mk.market_by_country(c, 100),
        ))
    for ind in INDUSTRIES:
        out.append((
            "/market/by-industry",
            {"industry": ind, "limit": 100},
            lambda ind=ind: mk.market_by_industry(ind, 100),
        ))
    return out


def warm_all(force: bool = False):
    """Proactively populate every entry in the warmup spec.

    Runs in the calling thread — caller decides whether to spawn a
    worker. We expose two scheduled callers: startup (so a fresh
    process has answers immediately) and post-scrape (so the new
    snapshot's worth shows up in derived charts).

    Pacing: between specs that hit Yahoo (any /market/* entry) we
    sleep briefly to stay under Yahoo's screener rate limit.
    Without this, warming all 22 market entries back-to-back trips
    'Too Many Requests' partway through and half the cache stays
    poisoned with empty results.
    """
    specs = _build_warmup_specs()
    refreshed = 0
    last_was_market = False
    for endpoint, params, compute in specs:
        if not force:
            cached = get_cached(endpoint, params)
            if cached and (time.time() - float(cached[1])) < _DEFAULT_TTL_SEC:
                # Already fresh; skip
                continue
        is_market = endpoint.startswith("/market/")
        if is_market and last_was_market:
            time.sleep(2)  # polite pacing between Yahoo-bound specs
        try:
            started = time.time()
            payload = compute()
            duration_ms = int((time.time() - started) * 1000)
            put(endpoint, params, payload, duration_ms)
            refreshed += 1
            logger.info(
                f"insights_cache: warmed {_key(endpoint, params)} "
                f"in {duration_ms}ms"
            )
        except Exception:
            logger.exception(
                f"insights_cache: warm failed for {_key(endpoint, params)}"
            )
        last_was_market = is_market
    logger.info(f"insights_cache.warm_all: refreshed {refreshed}/{len(specs)}")
    return refreshed


def warm_in_background(force: bool = False):
    """Spawn warm_all on a daemon thread. Safe to call from request
    handlers / scheduler hooks without blocking."""
    threading.Thread(
        target=warm_all, kwargs={"force": force}, daemon=True
    ).start()


def stats() -> dict:
    """Cache contents — exposed for debugging / a future status panel."""
    conn = get_db()
    try:
        _ensure_table(conn)
        rows = conn.execute(
            "SELECT key, computed_at, duration_ms, "
            "LENGTH(payload) AS bytes FROM insights_cache "
            "ORDER BY computed_at DESC"
        ).fetchall()
    finally:
        conn.close()
    now = time.time()
    return {
        "entries": [
            {
                "key": r["key"],
                "age_sec": int(now - float(r["computed_at"])),
                "duration_ms": r["duration_ms"],
                "bytes": r["bytes"],
            }
            for r in rows
        ],
        "total": len(rows),
    }
