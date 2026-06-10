"""Public-market deep-dive package.

Routes live in ``app.routes.market`` and go through
``app.insights_cache`` for persistence; the heavy lifting (Yahoo
screener, FX, Wikidata fallback, sector enrichment, share-class
dedup) lives in this package, one named module per concern:

* ``constants``  — country/industry/region maps
* ``fx``         — FX rate cache + USD conversion helper
* ``screener``   — Yahoo screener pagination, sector enrichment, dedup
* ``wikidata``   — SPARQL fallback for tiny markets
* ``compute``    — the two endpoint compute helpers
"""
from .compute import market_by_country, market_by_industry

__all__ = ["market_by_country", "market_by_industry"]
