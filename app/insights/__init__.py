"""Insights compute layer.

Each public function returns the JSON-shaped payload the route
handler returns directly. Routes live in ``app.routes.insights``
and are thin wrappers that go through ``app.insights_cache`` for
the slow ones.

This package was extracted from a 1500-line ``app/routes/insights.py``
to keep the route file focused on routing and let the compute logic
sit in named modules that map to product surface (cohort, sources,
migration, …).
"""

from .industries import normalize_industry
from .yearly import historical_or_bloomberg_per_year
from .cohort import cohort_survival
from .counts import count_over_time
from .inequality import inequality
from .sources import source_gap
from .series import top_over_time, top_over_time_series
from .wealth_correlation import wealth_correlation
from .comparison import compare_pair
from .migration import geo_migration

__all__ = [
    "normalize_industry",
    "historical_or_bloomberg_per_year",
    "cohort_survival",
    "count_over_time",
    "inequality",
    "source_gap",
    "top_over_time",
    "top_over_time_series",
    "wealth_correlation",
    "compare_pair",
    "geo_migration",
]
