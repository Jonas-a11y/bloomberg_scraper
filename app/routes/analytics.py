# app/routes/analytics.py
from fastapi import APIRouter, Query

from app.database import get_db, get_snapshot_dates

router = APIRouter()


@router.get("/analytics/by-industry")
def by_industry():
    conn = get_db()
    cursor = conn.execute("""
        SELECT industry, SUM(net_worth_usd) as total_wealth, COUNT(*) as count
        FROM billionaires
        WHERE scraped_at = (SELECT MAX(scraped_at) FROM billionaires)
        GROUP BY industry ORDER BY total_wealth DESC
    """)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


@router.get("/analytics/by-country")
def by_country():
    conn = get_db()
    cursor = conn.execute("""
        SELECT citizenship as country, SUM(net_worth_usd) as total_wealth, COUNT(*) as count
        FROM billionaires
        WHERE scraped_at = (SELECT MAX(scraped_at) FROM billionaires)
        GROUP BY citizenship ORDER BY total_wealth DESC
        LIMIT 20
    """)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


@router.get("/analytics/demographics")
def demographics():
    conn = get_db()
    gender_cursor = conn.execute("""
        SELECT gender, COUNT(*) as count
        FROM billionaires
        WHERE scraped_at = (SELECT MAX(scraped_at) FROM billionaires)
        GROUP BY gender
    """)
    gender = [dict(row) for row in gender_cursor.fetchall()]

    age_cursor = conn.execute("""
        SELECT
            CASE
                WHEN age < 40 THEN '30-39'
                WHEN age < 50 THEN '40-49'
                WHEN age < 60 THEN '50-59'
                WHEN age < 70 THEN '60-69'
                WHEN age < 80 THEN '70-79'
                WHEN age < 90 THEN '80-89'
                ELSE '90+'
            END as bracket,
            COUNT(*) as count
        FROM billionaires
        WHERE scraped_at = (SELECT MAX(scraped_at) FROM billionaires)
        GROUP BY bracket ORDER BY bracket
    """)
    age_distribution = [dict(row) for row in age_cursor.fetchall()]
    conn.close()
    return {"gender": gender, "age_distribution": age_distribution}


@router.get("/snapshots")
def snapshots():
    return get_snapshot_dates()


@router.get("/snapshots/compare")
def compare_snapshots(from_date: str = Query(...), to_date: str = Query(...)):
    conn = get_db()
    from_cursor = conn.execute("""
        SELECT person_id, common_name, rank, net_worth_usd
        FROM billionaires WHERE DATE(scraped_at) = ?
    """, (from_date,))
    from_data = {row[0]: dict(row) for row in from_cursor.fetchall()}

    to_cursor = conn.execute("""
        SELECT person_id, common_name, rank, net_worth_usd
        FROM billionaires WHERE DATE(scraped_at) = ?
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
