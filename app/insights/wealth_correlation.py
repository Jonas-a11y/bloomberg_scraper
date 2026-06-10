"""Pairwise wealth correlation across the top-N billionaires.

Loads wealth_history rows for the top-N persons in the chosen
window and dispatches to ``correlation.correlation_numpy`` (fast
path, O(N²) computed via masked dot products) or
``correlation.correlation_python`` (fallback for environments
without numpy).
"""
from __future__ import annotations

from datetime import date, timedelta

from app.database import get_db
from .correlation import correlation_numpy, correlation_python


def wealth_correlation(n: int, days: int, threshold: float,
                       end_date: str | None):
    # Hard cap to keep the endpoint bounded. 500 is plenty for the
    # full Bloomberg list and means a 500×500 = 250k-cell payload —
    # still under 5 MB JSON when each cell is a 5-char float string.
    n = max(2, min(int(n), 500))
    days = max(30, min(int(days), 3650))

    if end_date:
        try:
            end = date.fromisoformat(end_date[:10])
        except ValueError:
            end = date.today()
    else:
        end = date.today()
    start = end - timedelta(days=days)

    conn = get_db()
    top = conn.execute(
        """
        SELECT p.person_id, p.common_name
        FROM persons p
        JOIN snapshots s ON s.person_id = p.person_id
        WHERE s.scraped_at = (SELECT MAX(scraped_at) FROM snapshots)
        ORDER BY s.rank ASC
        LIMIT ?
        """,
        (n,),
    ).fetchall()
    pids = [r["person_id"] for r in top]
    name_by_pid = {r["person_id"]: r["common_name"] for r in top}
    if len(pids) < 2:
        conn.close()
        return {"persons": [], "matrix": [], "pairs": []}

    placeholders = ",".join("?" * len(pids))
    rows = conn.execute(
        f"""
        SELECT person_id, date, net_worth_usd
        FROM wealth_history
        WHERE person_id IN ({placeholders})
          AND date >= ? AND date <= ?
        ORDER BY person_id, date
        """,
        (*pids, start.isoformat(), end.isoformat()),
    ).fetchall()
    conn.close()

    # Vectorised numpy path — orders of magnitude faster at large N.
    # Pure-python fallback retained for environments where numpy
    # isn't importable (it should always be — pandas pulls it).
    try:
        return correlation_numpy(pids, name_by_pid, rows, days, threshold)
    except ImportError:
        return correlation_python(pids, name_by_pid, rows, days, threshold)
