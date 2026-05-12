import math

from fastapi import APIRouter, Query

import app.database
from app.database import get_db

router = APIRouter()


@router.get("/billionaires")
def list_billionaires(
    country: str | None = None,
    industry: str | None = None,
    gender: str | None = None,
    snapshot: str | None = None,
    sort: str = "rank",
    page: int = 1,
    q: str | None = None,
):
    conn = get_db()
    conditions = []
    params = []

    if snapshot:
        conditions.append("DATE(scraped_at) = ?")
        params.append(snapshot)
    else:
        conditions.append("scraped_at = (SELECT MAX(scraped_at) FROM billionaires)")

    if country:
        conditions.append("citizenship = ?")
        params.append(country)
    if industry:
        conditions.append("industry = ?")
        params.append(industry)
    if gender:
        conditions.append("gender = ?")
        params.append(gender)
    if q:
        conditions.append("common_name LIKE ?")
        params.append(f"%{q}%")

    where = " AND ".join(conditions)
    allowed_sorts = {"rank", "net_worth_usd", "last_change_usd", "ytd_change_usd", "age", "common_name"}
    sort_col = sort.lstrip("-")
    if sort_col not in allowed_sorts:
        sort_col = "rank"
    sort_dir = "DESC" if sort.startswith("-") else "ASC"

    count_sql = f"SELECT COUNT(*) FROM billionaires WHERE {where}"
    total = conn.execute(count_sql, params).fetchone()[0]

    per_page = 50
    pages = max(1, math.ceil(total / per_page))
    offset = (page - 1) * per_page

    data_sql = f"""
        SELECT person_id, rank, common_name, full_name, citizenship, age,
               birth_year, gender, gender_confidence, industry, sector,
               net_worth_usd, last_change_usd, last_change_pct,
               ytd_change_usd, ytd_change_pct
        FROM billionaires WHERE {where}
        ORDER BY {sort_col} {sort_dir}
        LIMIT ? OFFSET ?
    """
    cursor = conn.execute(data_sql, params + [per_page, offset])
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return {"data": rows, "total": total, "page": page, "pages": pages}


@router.get("/billionaires/{person_id}/history")
def person_history(person_id: int):
    conn = get_db()
    cursor = conn.execute("""
        SELECT scraped_at, rank, net_worth_usd, last_change_usd, ytd_change_usd
        FROM billionaires WHERE person_id = ?
        ORDER BY scraped_at
    """, (person_id,))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


@router.get("/search")
def search(q: str = Query(..., min_length=1)):
    conn = get_db()
    cursor = conn.execute("""
        SELECT DISTINCT person_id, common_name, net_worth_usd, rank
        FROM billionaires
        WHERE common_name LIKE ? AND scraped_at = (SELECT MAX(scraped_at) FROM billionaires)
        ORDER BY rank LIMIT 10
    """, (f"%{q}%",))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows
