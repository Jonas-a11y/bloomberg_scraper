from fastapi import APIRouter, Query

from app.database import get_db, get_snapshot_dates
from app.insights_cache import cached_or_compute

router = APIRouter()


@router.get("/analytics/by-industry")
def by_industry():
    conn = get_db()
    cursor = conn.execute("""
        SELECT p.industry, SUM(s.net_worth_usd) as total_wealth, COUNT(*) as count
        FROM snapshots s
        JOIN persons p ON s.person_id = p.person_id
        WHERE s.scraped_at = (SELECT MAX(scraped_at) FROM snapshots)
        GROUP BY p.industry ORDER BY total_wealth DESC
    """)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


@router.get("/analytics/by-country")
def by_country():
    conn = get_db()
    cursor = conn.execute("""
        SELECT p.citizenship as country, SUM(s.net_worth_usd) as total_wealth, COUNT(*) as count
        FROM snapshots s
        JOIN persons p ON s.person_id = p.person_id
        WHERE s.scraped_at = (SELECT MAX(scraped_at) FROM snapshots)
        GROUP BY p.citizenship ORDER BY total_wealth DESC
        LIMIT 20
    """)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


@router.get("/analytics/demographics")
def demographics():
    conn = get_db()
    gender_cursor = conn.execute("""
        SELECT p.gender, COUNT(*) as count
        FROM snapshots s
        JOIN persons p ON s.person_id = p.person_id
        WHERE s.scraped_at = (SELECT MAX(scraped_at) FROM snapshots)
        GROUP BY p.gender
    """)
    gender = [dict(row) for row in gender_cursor.fetchall()]

    age_cursor = conn.execute("""
        SELECT
            CASE
                WHEN p.age < 40 THEN '30-39'
                WHEN p.age < 50 THEN '40-49'
                WHEN p.age < 60 THEN '50-59'
                WHEN p.age < 70 THEN '60-69'
                WHEN p.age < 80 THEN '70-79'
                WHEN p.age < 90 THEN '80-89'
                ELSE '90+'
            END as bracket,
            COUNT(*) as count
        FROM snapshots s
        JOIN persons p ON s.person_id = p.person_id
        WHERE s.scraped_at = (SELECT MAX(scraped_at) FROM snapshots)
        GROUP BY bracket ORDER BY bracket
    """)
    age_distribution = [dict(row) for row in age_cursor.fetchall()]
    conn.close()
    return {"gender": gender, "age_distribution": age_distribution}


@router.get("/snapshots")
def snapshots():
    return get_snapshot_dates()


@router.get("/analytics/concentration")
def concentration(min_count: int = 100):
    """Daily share of total wealth held by top 1 / 10 / 100 within the cohort
    we have history for. Note: wealth_history is built from profile pages of
    persons known to the app at backfill time, so historical top-N skews toward
    today's survivors — anyone who dropped off the list before we started
    tracking them isn't represented. From now on, dropouts retain their history.

    Cached on disk via insights_cache. Cold compute is ~1.3s on the
    1.8M-row wealth_history table; cached lookup is single-digit ms
    and survives process restarts (the in-memory dict didn't)."""
    def _compute():
        conn = get_db()
        try:
            cursor = conn.execute("""
                WITH ranked AS (
                    SELECT date, net_worth_usd,
                           ROW_NUMBER() OVER (PARTITION BY date ORDER BY net_worth_usd DESC) AS rk
                    FROM wealth_history
                )
                SELECT date,
                       SUM(net_worth_usd)                                              AS total,
                       SUM(CASE WHEN rk = 1   THEN net_worth_usd ELSE 0 END)           AS top_1,
                       SUM(CASE WHEN rk <= 10 THEN net_worth_usd ELSE 0 END)           AS top_10,
                       SUM(CASE WHEN rk <= 100 THEN net_worth_usd ELSE 0 END)          AS top_100,
                       COUNT(*)                                                        AS count
                FROM ranked
                GROUP BY date
                HAVING count >= ?
                ORDER BY date
            """, (min_count,))
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    payload, _, _ = cached_or_compute(
        "/analytics/concentration", {"min_count": min_count}, _compute,
    )
    return payload


@router.get("/snapshots/compare")
def compare_snapshots(from_date: str = Query(...), to_date: str = Query(...)):
    conn = get_db()
    from_cursor = conn.execute("""
        SELECT s.person_id, p.common_name, s.rank, s.net_worth_usd
        FROM snapshots s
        JOIN persons p ON s.person_id = p.person_id
        WHERE DATE(s.scraped_at) = ?
    """, (from_date,))
    from_data = {row[0]: dict(row) for row in from_cursor.fetchall()}

    to_cursor = conn.execute("""
        SELECT s.person_id, p.common_name, s.rank, s.net_worth_usd
        FROM snapshots s
        JOIN persons p ON s.person_id = p.person_id
        WHERE DATE(s.scraped_at) = ?
    """, (to_date,))
    to_data = {row[0]: dict(row) for row in to_cursor.fetchall()}
    conn.close()

    changes = []
    for pid, to_row in to_data.items():
        if pid in from_data:
            rank_change = from_data[pid]["rank"] - to_row["rank"]
            wealth_change = to_row["net_worth_usd"] - from_data[pid]["net_worth_usd"]
            if rank_change != 0 or wealth_change != 0:
                changes.append({
                    "person_id": pid,
                    "common_name": to_row["common_name"],
                    "rank_change": rank_change,
                    "wealth_change": wealth_change,
                    "new_rank": to_row["rank"],
                })

    changes.sort(key=lambda x: abs(x["wealth_change"]), reverse=True)

    new_entries = [dict(to_data[pid]) for pid in to_data if pid not in from_data]
    dropped = [dict(from_data[pid]) for pid in from_data if pid not in to_data]

    return {"changes": changes[:20], "new_entries": new_entries, "dropped": dropped}
