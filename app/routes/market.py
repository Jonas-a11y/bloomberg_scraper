"""Public-market deep-dive endpoints — thin route layer.

Heavy lifting lives in ``app.market.*`` (constants, FX, screener,
Wikidata, compute). The route handlers here go through
``app.insights_cache`` so the answer survives a server restart —
Yahoo Screener can take 5-10s on a cold request and we don't want
every fresh process to pay that cost again.
"""
from __future__ import annotations

from fastapi import APIRouter

from app import insights_cache, market as mk

router = APIRouter()


@router.get("/market/by-country")
def market_by_country(country: str, limit: int = 25):
    """Top public companies headquartered in ``country``.

    Yahoo Screener is the primary source; Wikidata's SPARQL takes
    over only when Yahoo returns nothing (small markets like
    Liechtenstein). Stale cache entries (past TTL) are SERVED
    IMMEDIATELY and refreshed in the background — the user never
    blocks on a cold compute after the first one.
    """
    payload, _state, _age = insights_cache.cached_or_compute(
        "/market/by-country",
        {"country": country, "limit": limit},
        lambda: mk.market_by_country(country, limit),
    )
    return payload


@router.get("/market/by-industry")
def market_by_industry(industry: str, limit: int = 25):
    """Top public companies in ``industry`` worldwide, by market cap.

    Same caching strategy as ``/by-country``. Yahoo Screener primary,
    Wikidata fallback for industries Yahoo's GICS sector map doesn't
    cover well.
    """
    payload, _state, _age = insights_cache.cached_or_compute(
        "/market/by-industry",
        {"industry": industry, "limit": limit},
        lambda: mk.market_by_industry(industry, limit),
    )
    return payload
