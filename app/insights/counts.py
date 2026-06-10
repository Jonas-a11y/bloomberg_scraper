"""Number of billionaires over time, optionally split.

``by`` parameter controls the breakdown:
* ``"total"``    — single series of (year, count)
* ``"country"``  — top-10 countries by total count, per-year series
* ``"industry"`` — top-10 industries by total count, per-year series
"""
from __future__ import annotations

from collections import defaultdict

from app.database import get_db
from .yearly import historical_or_bloomberg_per_year


def count_over_time(year_from: int, year_to: int, by: str):
    conn = get_db()
    rows = historical_or_bloomberg_per_year(conn, year_from, year_to)
    conn.close()

    if by == "total":
        counts = defaultdict(int)
        for r in rows:
            counts[r["year"]] += 1
        return {
            "by": "total",
            "series": [{"year": y, "count": counts[y]} for y in sorted(counts)],
        }

    if by == "industry":
        # Industry strings in the dataset are noisy — strip brackets
        # and quotes that the Kaggle source brought in.
        def _norm(ind):
            if not ind:
                return "Unknown"
            s = str(ind).strip("[]")
            s = s.strip("'\"").strip()
            return s.split(",")[0].strip("'\"") or "Unknown"

        nested = defaultdict(lambda: defaultdict(int))
        for r in rows:
            nested[r["year"]][_norm(r["industry"])] += 1
        ind_totals = defaultdict(int)
        for d in nested.values():
            for i, c in d.items():
                ind_totals[i] += c
        top_inds = sorted(ind_totals, key=lambda i: -ind_totals[i])[:10]
        years = sorted(nested.keys())
        series = {ind: [] for ind in top_inds}
        for ind in top_inds:
            for y in years:
                series[ind].append({"year": y, "count": nested[y].get(ind, 0)})
        return {"by": "industry", "years": years, "series": series}

    # by == "country" (default)
    nested = defaultdict(lambda: defaultdict(int))
    for r in rows:
        if not r["citizenship"]:
            continue
        nested[r["year"]][r["citizenship"]] += 1
    country_totals = defaultdict(int)
    for d in nested.values():
        for c, n in d.items():
            country_totals[c] += n
    top_countries = sorted(country_totals, key=lambda c: -country_totals[c])[:10]
    years = sorted(nested.keys())
    series = {c: [] for c in top_countries}
    for c in top_countries:
        for y in years:
            series[c].append({"year": y, "count": nested[y].get(c, 0)})
    return {"by": "country", "years": years, "series": series}
