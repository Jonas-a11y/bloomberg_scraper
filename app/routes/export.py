# app/routes/export.py
import csv
import io
import json

from fastapi import APIRouter, Query
from fastapi.responses import Response, FileResponse

from app.database import get_db, DB_PATH

router = APIRouter()


@router.get("/export")
def export_data(
    format: str = "csv",
    scope: str = "latest",
    from_date: str | None = None,
    to_date: str | None = None,
    country: str | None = None,
    industry: str | None = None,
    top: int | None = None,
):
    conn = get_db()
    conditions = []
    params = []

    if scope == "latest":
        conditions.append("scraped_at = (SELECT MAX(scraped_at) FROM billionaires)")
    elif scope == "range" and from_date and to_date:
        conditions.append("DATE(scraped_at) BETWEEN ? AND ?")
        params.extend([from_date, to_date])

    if country:
        conditions.append("citizenship = ?")
        params.append(country)
    if industry:
        conditions.append("industry = ?")
        params.append(industry)

    where = " AND ".join(conditions) if conditions else "1=1"
    limit = f"LIMIT {top}" if top else ""

    sql = f"SELECT * FROM billionaires WHERE {where} ORDER BY scraped_at DESC, rank {limit}"
    cursor = conn.execute(sql, params)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    if format == "json":
        content = json.dumps(rows, indent=2).encode()
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": "attachment; filename=\"bloomberg_billionaires.json\""},
        )

    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    content = output.getvalue().encode()
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": "attachment; filename=\"bloomberg_billionaires.csv\""},
    )


@router.get("/export/bloomberg.db")
def export_db():
    db_path = str(DB_PATH)
    return FileResponse(
        db_path,
        media_type="application/octet-stream",
        filename="bloomberg.db",
    )


@router.get("/export/bloomberg_billionaires_master.csv")
def export_master():
    conn = get_db()
    cursor = conn.execute("""
        SELECT * FROM billionaires ORDER BY scraped_at, rank
    """)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    content = output.getvalue().encode()
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": "attachment; filename=\"bloomberg_billionaires_master.csv\""},
    )
