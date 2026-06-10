"""Wealth-inequality metrics: Gini + Lorenz curve per year.

Computed across the top 500 (or filtered subset) for each year.
The Lorenz curve is sampled at 11 evenly-spaced points so the chart
stays light over 25 years × 11 points.
"""
from __future__ import annotations

from collections import defaultdict

from app.database import get_db
from .yearly import historical_or_bloomberg_per_year


def inequality(year_from: int, year_to: int,
               country: str | None, industry: str | None):
    conn = get_db()
    rows = historical_or_bloomberg_per_year(
        conn, year_from, year_to, country, industry,
    )
    conn.close()

    by_year = defaultdict(list)
    for r in rows:
        if r["net_worth_usd"]:
            by_year[r["year"]].append(r["net_worth_usd"])

    series = []
    for year in sorted(by_year.keys()):
        values = sorted(by_year[year])
        n = len(values)
        if n < 2:
            continue
        total = sum(values)
        # Gini via the cumulative formula:
        #   G = (2 * sum(i * x_i) / (n * sum(x_i))) - (n + 1)/n
        cum = sum(i * v for i, v in enumerate(values, start=1))
        gini = (2 * cum) / (n * total) - (n + 1) / n
        # Lorenz curve sampled at ~11 evenly-spaced points.
        lorenz = []
        cum_sum = 0
        for i, v in enumerate(values, start=1):
            cum_sum += v
            if i % max(1, n // 10) == 0 or i == n:
                lorenz.append({
                    "x": round(i / n, 3),
                    "y": round(cum_sum / total, 3),
                })
        # Top 10's share of total billionaire wealth — easier to grasp
        # than Gini, surfaced alongside it.
        top10 = sum(values[-10:]) if n >= 10 else sum(values)
        top10_share = top10 / total
        series.append({
            "year": year,
            "n": n,
            "total_usd": total,
            "gini": round(gini, 4),
            "top10_share": round(top10_share, 4),
            "lorenz": lorenz,
        })
    return {"years": [s["year"] for s in series], "series": series}
