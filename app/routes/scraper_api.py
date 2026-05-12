# app/routes/scraper_api.py
import threading

from fastapi import APIRouter

from app.database import get_db
from app.scheduler import (
    get_schedule_config,
    save_schedule_config,
    apply_schedule,
    run_scrape,
    is_running,
    get_next_run,
)
from app.models import ScheduleUpdate

router = APIRouter()


@router.get("/scraper/status")
def scraper_status():
    conn = get_db()
    last_success = conn.execute(
        "SELECT finished_at FROM scrape_runs WHERE status = 'success' ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return {
        "status": "running" if is_running() else "idle",
        "next_run": get_next_run(),
        "last_success": last_success[0] if last_success else None,
    }


@router.get("/scraper/runs")
def scraper_runs():
    conn = get_db()
    cursor = conn.execute(
        "SELECT * FROM scrape_runs ORDER BY started_at DESC LIMIT 20"
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


@router.post("/scraper/run")
def trigger_scrape():
    if is_running():
        return {"status": "already_running"}
    thread = threading.Thread(target=run_scrape, daemon=True)
    thread.start()
    return {"status": "started"}


@router.get("/scraper/schedule")
def get_schedule():
    return get_schedule_config()


@router.put("/scraper/schedule")
def update_schedule(config: ScheduleUpdate):
    save_schedule_config(config.times, config.timezone, config.enabled)
    apply_schedule()
    return get_schedule_config()
