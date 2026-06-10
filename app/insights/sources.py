"""Forbes vs Bloomberg per-person valuation gap.

Surfaces which billionaires the two sources most disagree about.
Default ``year`` = most recent Forbes-Kaggle year that's also <
current year (so we never report a partial-year mismatch).

Currently not surfaced in the UI but kept for the API and the
historical record — the data quality issues it exposes are useful
context for anyone reasoning about source reliability.
"""
from __future__ import annotations

from datetime import datetime as _dt

from app.database import get_db


def source_gap(year: int | None, limit: int):
    conn = get_db()
    if year is None:
        max_year_row = conn.execute(
            "SELECT MAX(year) AS y FROM historical_rankings WHERE source = 'forbes_kaggle'"
        ).fetchone()
        year = min(
            max_year_row["y"] or _dt.now().year - 1,
            _dt.now().year - 1,
        )
    rows = conn.execute(
        """
        WITH forbes AS (
            SELECT person_id, name, net_worth_usd AS forbes_worth
            FROM historical_rankings
            WHERE year = ? AND source = 'forbes_kaggle' AND person_id IS NOT NULL
        ),
        bloomberg AS (
            SELECT wh.person_id, wh.net_worth_usd AS bloomberg_worth
            FROM wealth_history wh
            WHERE wh.date = (
                SELECT MAX(date) FROM wealth_history wh2
                WHERE wh2.person_id = wh.person_id AND wh2.date <= ? || '-12-31'
            )
        )
        SELECT f.person_id, COALESCE(p.common_name, f.name) AS name,
               p.citizenship, p.industry,
               f.forbes_worth, b.bloomberg_worth
        FROM forbes f
        JOIN bloomberg b ON b.person_id = f.person_id
        LEFT JOIN persons p ON p.person_id = f.person_id
        WHERE f.forbes_worth > 0 AND b.bloomberg_worth > 0
        """,
        (year, str(year)),
    ).fetchall()
    conn.close()

    pairs = []
    for r in rows:
        d = dict(r)
        f, b = d["forbes_worth"], d["bloomberg_worth"]
        d["gap_abs"] = b - f
        d["gap_pct"] = ((b - f) / f * 100) if f else 0
        pairs.append(d)
    pairs.sort(key=lambda x: -abs(x["gap_pct"]))
    return {
        "year": year,
        "total_compared": len(pairs),
        "pairs": pairs[:limit],
    }
