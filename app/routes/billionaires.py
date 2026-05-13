from fastapi import APIRouter, Query

from app.database import get_db

router = APIRouter()


@router.get("/billionaires")
def list_billionaires(
    country: str | None = None,
    industry: str | None = None,
    gender: str | None = None,
    snapshot: str | None = None,
    sort: str = "rank",
    q: str | None = None,
):
    conn = get_db()
    conditions = []
    params = []

    if snapshot:
        conditions.append("DATE(s.scraped_at) = ?")
        params.append(snapshot)
    else:
        conditions.append("s.scraped_at = (SELECT MAX(scraped_at) FROM snapshots)")

    if country:
        conditions.append("p.citizenship = ?")
        params.append(country)
    if industry:
        conditions.append("p.industry = ?")
        params.append(industry)
    if gender:
        conditions.append("p.gender = ?")
        params.append(gender)
    if q:
        conditions.append("p.common_name LIKE ?")
        params.append(f"%{q}%")

    where = " AND ".join(conditions)
    allowed_sorts = {"rank", "net_worth_usd", "last_change_usd", "ytd_change_usd", "age", "common_name"}
    sort_col = sort.lstrip("-")
    if sort_col not in allowed_sorts:
        sort_col = "rank"
    sort_dir = "DESC" if sort.startswith("-") else "ASC"
    qualified_sort = f"s.{sort_col}" if sort_col in ("rank", "net_worth_usd", "last_change_usd", "ytd_change_usd") else f"p.{sort_col}"

    data_sql = f"""
        SELECT p.person_id, s.rank, p.common_name, p.full_name, p.citizenship, p.age,
               p.birth_year, p.gender, p.gender_confidence, p.industry,
               s.net_worth_usd, s.last_change_usd, s.last_change_pct,
               s.ytd_change_usd, s.ytd_change_pct
        FROM snapshots s
        JOIN persons p ON s.person_id = p.person_id
        WHERE {where}
        ORDER BY {qualified_sort} {sort_dir}
    """
    cursor = conn.execute(data_sql, params)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return {"data": rows, "total": len(rows)}


@router.get("/billionaires/{person_id}/history")
def person_history(person_id: int):
    conn = get_db()
    cursor = conn.execute("""
        SELECT scraped_at, rank, net_worth_usd, last_change_usd, ytd_change_usd
        FROM snapshots WHERE person_id = ?
        ORDER BY scraped_at
    """, (person_id,))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


@router.get("/billionaires/{person_id}")
def person_detail(person_id: int):
    conn = get_db()
    cursor = conn.execute("""
        SELECT p.*, s.rank, s.net_worth_usd, s.last_change_usd, s.last_change_pct,
               s.ytd_change_usd, s.ytd_change_pct,
               s.public_assets_total, s.private_assets_total, s.cash_assets_total,
               s.public_assets_json, s.private_assets_json,
               s.cash_asset_value, s.liabilities_value, s.liabilities_note
        FROM persons p
        JOIN snapshots s ON p.person_id = s.person_id
        WHERE p.person_id = ?
          AND s.scraped_at = (SELECT MAX(scraped_at) FROM snapshots WHERE person_id = ?)
    """, (person_id, person_id))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return {"error": "not found"}
    return dict(row)


@router.get("/search")
def search(q: str = Query(..., min_length=1)):
    conn = get_db()
    cursor = conn.execute("""
        SELECT p.person_id, p.common_name, s.net_worth_usd, s.rank
        FROM snapshots s
        JOIN persons p ON s.person_id = p.person_id
        WHERE p.common_name LIKE ?
          AND s.scraped_at = (SELECT MAX(scraped_at) FROM snapshots)
        ORDER BY s.rank LIMIT 10
    """, (f"%{q}%",))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows
