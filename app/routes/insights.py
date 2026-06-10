"""Insights tab endpoints — thin route layer.

Every endpoint here is a 3-line wrapper over ``app.insights.*``:

* parse query params (with ``year_to`` defaulting to current year)
* hand off to the compute helper
* go through ``app.insights_cache`` so the slow ones are precomputed

The compute logic itself lives in the ``app.insights`` package —
one module per product surface. This file used to be 1,546 lines
and contained both routing AND compute; the split keeps each layer
focused.
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter

from app import insights_cache, insights as ix

logger = logging.getLogger(__name__)
router = APIRouter()


def _cached(endpoint, params, compute):
    """Run ``compute()`` through the persistent cache; return its
    payload. Tiny indirection so each route below is one line."""
    payload, _state, _age = insights_cache.cached_or_compute(
        endpoint, params, compute,
    )
    return payload


# ──────────────────────────────────────────────────────────────────────────
# Bar-chart race + continuous-timeline data
# ──────────────────────────────────────────────────────────────────────────

@router.get("/insights/top-over-time")
def top_over_time(
    n: int = 10,
    year_from: int = 2001,
    year_to: int | None = None,
    country: str | None = None,
    industry: str | None = None,
):
    """Top-N net worth at each year-end for the bar-chart race.

    Returns ``{years, frames: {year: [...]}, n}``. Each frame is sorted
    descending. UI animates between frames.
    """
    if year_to is None:
        year_to = datetime.now().year
    return _cached(
        "/insights/top-over-time",
        {"n": n, "year_from": year_from, "year_to": year_to,
         "country": country, "industry": industry},
        lambda: ix.top_over_time(n, year_from, year_to, country, industry),
    )


@router.get("/insights/top-over-time-series")
def top_over_time_series(
    n: int = 12,
    year_from: int = 2001,
    year_to: int | None = None,
    country: str | None = None,
    industry: str | None = None,
):
    """Continuous-timeline data for the bar-chart race.

    Differs from ``/top-over-time`` (per-year frames). Returns one
    series per person across the full range so the frontend can
    interpolate smoothly between any two points.

    Bloomberg ``wealth_history`` provides monthly observations;
    Forbes annual rows fill the gap as December anchors. Only
    ``forbes_kaggle`` is used (the legacy ``forbes_world`` Wikipedia
    scrape has known vandalism bugs that wreck interpolation).
    """
    if year_to is None:
        year_to = datetime.now().year
    return _cached(
        "/insights/top-over-time-series",
        {"n": n, "year_from": year_from, "year_to": year_to,
         "country": country, "industry": industry},
        lambda: ix.top_over_time_series(n, year_from, year_to, country, industry),
    )


# ──────────────────────────────────────────────────────────────────────────
# Cohort & source comparison
# ──────────────────────────────────────────────────────────────────────────

@router.get("/insights/cohort-survival")
def cohort_survival(year: int = 2001, top: int = 100):
    """Where the top-N at ``year`` is now: ``still_listed`` /
    ``dropped`` / ``died`` / ``never_tracked``.

    Used by the cohort donut + member-list panel.
    """
    return _cached(
        "/insights/cohort-survival", {"year": year, "top": top},
        lambda: ix.cohort_survival(year, top),
    )


@router.get("/insights/source-gap")
def source_gap(year: int | None = None, limit: int = 30):
    """Per-person valuation gap: Forbes annual vs Bloomberg's snapshot
    closest to that year.

    Default ``year`` = most recent year that has Forbes-Kaggle data
    AND is < current year (so we never report a partial year).
    Currently not surfaced in the UI; kept for the API.
    """
    return _cached(
        "/insights/source-gap", {"year": year, "limit": limit},
        lambda: ix.source_gap(year, limit),
    )


# ──────────────────────────────────────────────────────────────────────────
# Aggregate metrics
# ──────────────────────────────────────────────────────────────────────────

@router.get("/insights/inequality")
def inequality(year_from: int = 2001, year_to: int | None = None,
               country: str | None = None, industry: str | None = None):
    """Gini coefficient + Lorenz curve points per year, computed
    across the top 500 (or filtered subset) for that year."""
    if year_to is None:
        year_to = datetime.now().year
    return _cached(
        "/insights/inequality",
        {"year_from": year_from, "year_to": year_to,
         "country": country, "industry": industry},
        lambda: ix.inequality(year_from, year_to, country, industry),
    )


@router.get("/insights/count-over-time")
def count_over_time(year_from: int = 2001, year_to: int | None = None,
                    by: str = "country"):
    """Number of billionaires over time, optionally split by country
    or industry. ``by`` ∈ {``total``, ``country``, ``industry``}."""
    if year_to is None:
        year_to = datetime.now().year
    return _cached(
        "/insights/count-over-time",
        {"year_from": year_from, "year_to": year_to, "by": by},
        lambda: ix.count_over_time(year_from, year_to, by),
    )


# ──────────────────────────────────────────────────────────────────────────
# Pairwise correlation discoveries
# ──────────────────────────────────────────────────────────────────────────

@router.get("/insights/wealth-correlation")
def wealth_correlation(
    n: int = 30,
    days: int = 365,
    threshold: float = 0.85,
    end_date: str | None = None,
):
    """Pairwise daily-log-return correlation between the top N
    billionaires over the past ``days`` days.

    Strong correlations (|r| ≥ threshold) often signal hidden links:
    same company, co-founders, or holders of the same stock. Returns
    both the full matrix (for the heatmap) and the strongest pairs
    (for the discoveries list).

    Scaling: at N=500 we compute ~125k pair correlations in under a
    second via numpy. Cached so the second visitor is near-instant.
    """
    return _cached(
        "/insights/wealth-correlation",
        {"n": n, "days": days, "threshold": threshold, "end_date": end_date},
        lambda: ix.wealth_correlation(n, days, threshold, end_date),
    )


@router.get("/insights/compare-pair")
def compare_pair(a: int, b: int, days: int = 365):
    """Side-by-side data for two billionaires + their pair correlation.

    Used by the heatmap-cell click handler — single round-trip with
    everything the comparison modal needs.
    """
    days = max(30, min(int(days), 3650))
    return _cached(
        "/insights/compare-pair",
        {"a": a, "b": b, "days": days},
        lambda: ix.compare_pair(a, b, days),
    )


# ──────────────────────────────────────────────────────────────────────────
# Geography
# ──────────────────────────────────────────────────────────────────────────

@router.get("/insights/geo-migration")
def geo_migration():
    """Birth-country → residence-country flows from Wikidata metadata.

    Returns ``{flows: [...], nodes: [...]}``. Self-flows are kept in
    the response so the UI can choose to suppress them on the Sankey
    while still rolling them into ``nodes``.
    """
    return _cached(
        "/insights/geo-migration", None,
        ix.geo_migration,
    )
