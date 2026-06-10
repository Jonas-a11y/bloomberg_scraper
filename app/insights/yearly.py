"""Per-year, per-person net worth observations.

The shared SQL helper several Insights endpoints depend on. Each
(person, year) gets at most one row, sourced from Bloomberg's
``wealth_history`` if any rows exist for that year, otherwise from
``historical_rankings`` (preferring ``forbes_kaggle`` over the
older Wikipedia scrape).

Performance note:
A naive correlated subquery per (person, year) is too slow on the
1.8M-row ``wealth_history``. We instead extract (person, year) ->
``max_date_in_year`` via a single ``GROUP BY``, then JOIN back to
grab the actual net worth. That brings a 25-year query from ~30s
down to ~1s on the production dataset.

``forbes_world`` rows (Wikipedia scrape) are dropped for any year
where ``forbes_kaggle`` has data — Kaggle is denser and pre-cleaned,
while the Wikipedia scrape sometimes captured vandalised table rows
with inflated values. ``forbes_world`` survives only as a fallback
for years past the Kaggle freeze.
"""
from __future__ import annotations

from .industries import normalize_industry


def historical_or_bloomberg_per_year(conn, year_from, year_to,
                                     country=None, industry=None):
    """Pull per-year per-person rows in [year_from, year_to].

    Returns a list of dicts with keys
    ``year``, ``person_id``, ``name``, ``net_worth_usd``,
    ``citizenship``, ``industry``, ``source``. Industries come back
    canonicalised. Filters apply after the SQL fetch.
    """
    rows = conn.execute(
        """
        WITH bloomberg_per_year AS (
            -- For each (person, year), the latest date in that year.
            -- We then look up the wealth on that date by joining back.
            SELECT person_id,
                   CAST(substr(date, 1, 4) AS INTEGER) AS year,
                   MAX(date) AS max_date
            FROM wealth_history
            WHERE date >= ? AND date <= ?
            GROUP BY person_id, CAST(substr(date, 1, 4) AS INTEGER)
        ),
        bloomberg AS (
            SELECT bpy.person_id, bpy.year, wh.net_worth_usd
            FROM bloomberg_per_year bpy
            JOIN wealth_history wh
              ON wh.person_id = bpy.person_id AND wh.date = bpy.max_date
        ),
        kaggle_years AS (
            SELECT DISTINCT year FROM historical_rankings
            WHERE source = 'forbes_kaggle' AND year BETWEEN ? AND ?
        ),
        forbes AS (
            SELECT hr.year, hr.person_id, hr.net_worth_usd,
                   hr.name, hr.citizenship, hr.industry, hr.source,
                   ROW_NUMBER() OVER (
                       PARTITION BY hr.year, COALESCE(hr.person_id, 'name:' || hr.name)
                       ORDER BY CASE hr.source
                           WHEN 'forbes_kaggle' THEN 1
                           WHEN 'forbes_world' THEN 2
                           ELSE 3 END
                   ) AS rn
            FROM historical_rankings hr
            WHERE hr.year BETWEEN ? AND ?
              -- Drop forbes_world for years we have Kaggle data for.
              AND NOT (hr.source = 'forbes_world'
                       AND hr.year IN (SELECT year FROM kaggle_years))
        )
        SELECT b.year, b.person_id,
               p.common_name AS name,
               p.citizenship, p.industry,
               b.net_worth_usd,
               'bloomberg' AS source
        FROM bloomberg b
        JOIN persons p ON p.person_id = b.person_id
        UNION ALL
        SELECT f.year, f.person_id,
               COALESCE(p.common_name, f.name) AS name,
               COALESCE(p.citizenship, f.citizenship) AS citizenship,
               COALESCE(p.industry, f.industry) AS industry,
               f.net_worth_usd, f.source
        FROM forbes f
        LEFT JOIN persons p ON p.person_id = f.person_id
        WHERE f.rn = 1
          AND NOT EXISTS (
              SELECT 1 FROM bloomberg b2
              WHERE b2.year = f.year AND b2.person_id = f.person_id
          )
        """,
        (f"{year_from}-01-01", f"{year_to}-12-31",
         year_from, year_to,
         year_from, year_to),
    ).fetchall()

    out = []
    for r in rows:
        if country and r["citizenship"] != country:
            continue
        if industry and r["industry"] != industry:
            continue
        d = dict(r)
        d["industry"] = normalize_industry(d.get("industry"))
        out.append(d)
    return out
