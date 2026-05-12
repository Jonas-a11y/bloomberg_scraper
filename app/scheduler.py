# app/scheduler.py
import json
import logging
import time
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from app.database import get_db, insert_scrape_data
from app.scraper import scrape_billionaires

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()
_is_running = False


def get_schedule_config():
    conn = get_db()
    row = conn.execute("SELECT times, timezone, enabled FROM schedule_config WHERE id = 1").fetchone()
    conn.close()
    if row:
        return {"times": json.loads(row[0]), "timezone": row[1], "enabled": bool(row[2])}
    return {"times": ["08:00"], "timezone": "UTC", "enabled": True}


def save_schedule_config(times, timezone, enabled):
    conn = get_db()
    conn.execute(
        "UPDATE schedule_config SET times = ?, timezone = ?, enabled = ? WHERE id = 1",
        (json.dumps(times), timezone, 1 if enabled else 0),
    )
    conn.commit()
    conn.close()


def run_scrape():
    global _is_running
    if _is_running:
        return
    _is_running = True
    conn = get_db()
    conn.execute(
        "INSERT INTO scrape_runs (started_at, status) VALUES (?, 'running')",
        (datetime.now().isoformat(),),
    )
    conn.commit()
    run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    start = time.time()
    try:
        rows = scrape_billionaires()
        insert_scrape_data(None, rows)
        duration_ms = int((time.time() - start) * 1000)
        conn = get_db()
        conn.execute(
            "UPDATE scrape_runs SET finished_at = ?, status = 'success', record_count = ?, duration_ms = ? WHERE id = ?",
            (datetime.now().isoformat(), len(rows), duration_ms, run_id),
        )
        conn.commit()
        conn.close()
        logger.info(f"Scrape complete: {len(rows)} records in {duration_ms}ms")
    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        conn = get_db()
        conn.execute(
            "UPDATE scrape_runs SET finished_at = ?, status = 'failed', duration_ms = ?, error = ? WHERE id = ?",
            (datetime.now().isoformat(), duration_ms, str(e), run_id),
        )
        conn.commit()
        conn.close()
        logger.error(f"Scrape failed: {e}")
    finally:
        _is_running = False


def apply_schedule():
    scheduler.remove_all_jobs()
    config = get_schedule_config()
    if not config["enabled"]:
        return
    for time_str in config["times"]:
        hour, minute = time_str.split(":")
        scheduler.add_job(
            run_scrape,
            "cron",
            hour=int(hour),
            minute=int(minute),
            timezone=config["timezone"],
            id=f"scrape_{time_str}",
        )


def start_scheduler():
    apply_schedule()
    if not scheduler.running:
        scheduler.start()


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)


def is_running():
    return _is_running


def get_next_run():
    jobs = scheduler.get_jobs()
    if not jobs:
        return None
    next_times = [job.next_run_time for job in jobs if job.next_run_time]
    if not next_times:
        return None
    return min(next_times).isoformat()
