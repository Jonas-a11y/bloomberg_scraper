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
    from app.routes import insights as ix

    year = datetime.now().year
    return [
        # Demographics + leaderboards used on Insights tab open
        (
            "/insights/inequality",
            {"year_from": 2001, "year_to": year},
            lambda: ix.inequality(year_from=2001, year_to=year),
        ),
        (
            "/insights/count-over-time",
            {"year_from": 2001, "year_to": year, "by": "country"},
            lambda: ix.count_over_time(
                year_from=2001, year_to=year, by="country",
            ),
        ),
        (
            "/insights/top-over-time",
            {"n": 12, "year_from": 2001, "year_to": year},
            lambda: ix.top_over_time(n=12, year_from=2001, year_to=year),
        ),
        (
            "/insights/top-over-time-series",
            {"n": 12, "year_from": 2001, "year_to": year},
            lambda: ix.top_over_time_series(
                n=12, year_from=2001, year_to=year,
            ),
        ),
        (
            "/insights/source-gap",
            {"limit": 20},
            lambda: ix.source_gap(limit=20),
        ),
        (
            "/insights/cohort-survival",
            {"year": 2001, "top": 100},
            lambda: ix.cohort_survival(year=2001, top=100),
        ),
        (
            "/insights/geo-migration",
            None,
            lambda: ix.geo_migration(),
        ),
        # Wealth correlation at the four UI presets — these are the
        # slowest endpoints, biggest win from caching.
        *[
            (
                "/insights/wealth-correlation",
                {"n": n, "days": 365, "threshold": 0.7},
                lambda n=n: ix.wealth_correlation(
                    n=n, days=365, threshold=0.7,
                ),
            )
            for n in (30, 100, 250, 500)
        ],
    ]


def warm_all(force: bool = False):
    """Proactively populate every entry in the warmup spec.

    Runs in the calling thread — caller decides whether to spawn a
    worker. We expose two scheduled callers: startup (so a fresh
    process has answers immediately) and post-scrape (so the new
    snapshot's worth shows up in derived charts).
    """
    specs = _build_warmup_specs()
    refreshed = 0
    for endpoint, params, compute in specs:
        if not force:
            cached = get_cached(endpoint, params)
            if cached and (time.time() - float(cached[1])) < _DEFAULT_TTL_SEC:
                # Already fresh; skip
                continue
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
