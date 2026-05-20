import csv
import io
import json

from fastapi import APIRouter
from fastapi.responses import FileResponse
from starlette.responses import Response

from app.database import get_db, DB_PATH

router = APIRouter()


def _query_export(scope, from_date, to_date, country, industry, top, fields=None):
    selected = _parse_fields(fields)
    columns = ", ".join(f"{AVAILABLE_FIELDS[f]} AS {f}" for f in selected)
    conn = get_db()
    conditions = []
    params = []

    if scope == "latest":
        conditions.append("s.scraped_at = (SELECT MAX(scraped_at) FROM snapshots)")
    elif scope == "range" and from_date and to_date:
        conditions.append("DATE(s.scraped_at) BETWEEN ? AND ?")
        params.extend([from_date, to_date])

    if country:
        conditions.append("p.citizenship = ?")
        params.append(country)
    if industry:
        conditions.append("p.industry = ?")
        params.append(industry)

    where = " AND ".join(conditions) if conditions else "1=1"
    limit = f"LIMIT {int(top)}" if top else ""

    sql = f"""
        SELECT {columns}
        FROM snapshots s
        JOIN persons p ON s.person_id = p.person_id
        WHERE {where}
        ORDER BY s.scraped_at DESC, s.rank
        {limit}
    """
    cursor = conn.execute(sql, params)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def _csv_response(rows, filename):
    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    content = output.getvalue().encode("utf-8")
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "application/octet-stream",
        },
    )


def _json_response(rows, filename):
    content = json.dumps(rows, indent=2).encode("utf-8")
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "application/octet-stream",
        },
    )


@router.get("/export/bloomberg_billionaires.csv")
def export_csv(
    scope: str = "latest",
    from_date: str | None = None,
    to_date: str | None = None,
    country: str | None = None,
    industry: str | None = None,
    top: int | None = None,
    fields: str | None = None,
):
    rows = _query_export(scope, from_date, to_date, country, industry, top, fields)
    return _csv_response(rows, "bloomberg_billionaires.csv")


@router.get("/export/bloomberg_billionaires.json")
def export_json(
    scope: str = "latest",
    from_date: str | None = None,
    to_date: str | None = None,
    country: str | None = None,
    industry: str | None = None,
    top: int | None = None,
    fields: str | None = None,
):
    rows = _query_export(scope, from_date, to_date, country, industry, top, fields)
    return _json_response(rows, "bloomberg_billionaires.json")


@router.get("/export")
def export_data(
    format: str = "csv",
    scope: str = "latest",
    from_date: str | None = None,
    to_date: str | None = None,
    country: str | None = None,
    industry: str | None = None,
    top: int | None = None,
    fields: str | None = None,
):
    rows = _query_export(scope, from_date, to_date, country, industry, top, fields)
    if format == "json":
        return _json_response(rows, "bloomberg_billionaires.json")
    return _csv_response(rows, "bloomberg_billionaires.csv")


@router.get("/export/bloomberg.db")
def export_db():
    return FileResponse(
        path=str(DB_PATH),
        media_type="application/octet-stream",
        filename="bloomberg.db",
    )


def _query_wealth_history(from_date, to_date, person_id):
    conn = get_db()
    conditions = []
    params = []
    if from_date:
        conditions.append("h.date >= ?")
        params.append(from_date)
    if to_date:
        conditions.append("h.date <= ?")
        params.append(to_date)
    if person_id:
        conditions.append("h.person_id = ?")
        params.append(person_id)
    where = " AND ".join(conditions) if conditions else "1=1"
    sql = f"""
        SELECT h.person_id, p.common_name, p.slug, h.date, h.net_worth_usd
        FROM wealth_history h
        JOIN persons p ON h.person_id = p.person_id
        WHERE {where}
        ORDER BY h.person_id, h.date
    """
    cursor = conn.execute(sql, params)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


@router.get("/export/wealth_history.csv")
def export_wealth_history_csv(
    from_date: str | None = None,
    to_date: str | None = None,
    person_id: int | None = None,
):
    rows = _query_wealth_history(from_date, to_date, person_id)
    return _csv_response(rows, "wealth_history.csv")


@router.get("/export/wealth_history.json")
def export_wealth_history_json(
    from_date: str | None = None,
    to_date: str | None = None,
    person_id: int | None = None,
):
    rows = _query_wealth_history(from_date, to_date, person_id)
    return _json_response(rows, "wealth_history.json")


AVAILABLE_FIELDS = {
    "scraped_at": "s.scraped_at",
    "person_id": "p.person_id",
    "rank": "s.rank",
    "common_name": "p.common_name",
    "full_name": "p.full_name",
    "first_name": "p.first_name",
    "last_name": "p.last_name",
    "middle_name": "p.middle_name",
    "citizenship": "p.citizenship",
    "age": "p.age",
    "birth_year": "p.birth_year",
    "gender": "p.gender",
    "gender_confidence": "p.gender_confidence",
    "industry": "p.industry",
    "biography": "p.biography",
    "overview": "p.overview",
    "net_worth_summary": "p.net_worth_summary",
    "schools_json": "p.schools_json",
    "facts_json": "p.facts_json",
    "milestones_json": "p.milestones_json",
    "slug": "p.slug",
    "confidence": "p.confidence",
    "net_worth_usd": "s.net_worth_usd",
    "last_change_usd": "s.last_change_usd",
    "last_change_pct": "s.last_change_pct",
    "ytd_change_usd": "s.ytd_change_usd",
    "ytd_change_pct": "s.ytd_change_pct",
    "public_assets_total": "s.public_assets_total",
    "private_assets_total": "s.private_assets_total",
    "cash_assets_total": "s.cash_assets_total",
    "public_assets_json": "s.public_assets_json",
    "private_assets_json": "s.private_assets_json",
    "cash_asset_value": "s.cash_asset_value",
    "liabilities_value": "s.liabilities_value",
    "liabilities_note": "s.liabilities_note",
}

DEFAULT_FIELDS = [
    "scraped_at", "rank", "person_id", "common_name", "full_name",
    "citizenship", "age", "gender", "industry",
    "net_worth_usd", "last_change_usd", "last_change_pct",
    "ytd_change_usd", "ytd_change_pct",
    "public_assets_total", "private_assets_total", "cash_assets_total",
]


@router.get("/export/fields")
def list_fields():
    return {"fields": list(AVAILABLE_FIELDS.keys()), "defaults": DEFAULT_FIELDS}


@router.get("/export/bloomberg_billionaires_master.csv")
def export_master(fields: str | None = None, format: str = "csv"):
    rows = _query_export("all", None, None, None, None, None, fields)
    if format == "json":
        return _json_response(rows, "bloomberg_billionaires_master.json")
    return _csv_response(rows, "bloomberg_billionaires_master.csv")


def _parse_fields(fields_param):
    if not fields_param:
        return DEFAULT_FIELDS
    requested = [f.strip() for f in fields_param.split(",") if f.strip()]
    valid = [f for f in requested if f in AVAILABLE_FIELDS]
    return valid or DEFAULT_FIELDS
