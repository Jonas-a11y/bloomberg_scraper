from fastapi import APIRouter

import app.database
from app.database import get_db, get_dashboard_stats

router = APIRouter()

_dashboard_cache: dict = {}


@router.get("/dashboard")
def dashboard():
    conn = get_db()
    latest = conn.execute("SELECT MAX(scraped_at) FROM snapshots").fetchone()[0]
    conn.close()
    cached = _dashboard_cache.get(latest)
    if cached is not None:
        return cached
    data = get_dashboard_stats()
    _dashboard_cache.clear()
    _dashboard_cache[latest] = data
    return data
