# app/scheduler.py
import json
import logging
import time
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from app.database import get_db, insert_scrape_data, insert_wealth_history
from app.scraper import scrape_billionaires, fetch_person_history

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()
_is_running = False
_backfill_state = {"running": False, "done": 0, "total": 0, "errors": 0, "started_at": None, "finished_at": None}

# Bloomberg occasionally rate-limits or 5xx's the scrape; rather than wait for
# the next scheduled run (could be 24h away), automatically retry with growing
# backoff. Three attempts total — if all three fail, something is structurally
# broken and another retry just hammers their endpoint.
SCRAPE_RETRY_DELAYS_SEC = [600, 1800]  # 10 min after first failure, 30 min after second


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


def run_scrape(attempt=1):
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
        next_attempt = attempt + 1
        delay_idx = attempt - 1
        retrying = delay_idx < len(SCRAPE_RETRY_DELAYS_SEC)
        status = "retrying" if retrying else "failed"
        error_text = f"attempt {attempt}: {e}"
        conn = get_db()
        conn.execute(
            "UPDATE scrape_runs SET finished_at = ?, status = ?, duration_ms = ?, error = ? WHERE id = ?",
            (datetime.now().isoformat(), status, duration_ms, error_text, run_id),
        )
        conn.commit()
        conn.close()
        if retrying:
            delay = SCRAPE_RETRY_DELAYS_SEC[delay_idx]
            run_at = datetime.now() + timedelta(seconds=delay)
            # Use a unique job id per attempt so a queued retry can't collide
            # with a later manual trigger reusing the same id.
            scheduler.add_job(
                run_scrape,
                "date",
                run_date=run_at,
                args=[next_attempt],
                id=f"scrape_retry_{run_id}_{next_attempt}",
                replace_existing=True,
            )
            logger.warning(
                f"Scrape failed (attempt {attempt}): {e}. "
                f"Retrying in {delay // 60} min (attempt {next_attempt})."
            )
            return
        logger.error(f"Scrape failed (attempt {attempt}, no more retries): {e}")
    finally:
        _is_running = False


def apply_schedule():
    scheduler.remove_all_jobs()
    config = get_schedule_config()
    if not config["enabled"]:
        return
    for time_str in config["times"]:
        try:
            hour, minute = time_str.split(":")
            scheduler.add_job(
                run_scrape,
                "cron",
                hour=int(hour),
                minute=int(minute),
                timezone=config["timezone"],
                id=f"scrape_{time_str}",
            )
        except Exception as e:
            logger.warning(f"Failed to schedule {time_str}: {e}")


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


def run_history_backfill(delay_sec=1.5):
    """Iterate every known person, fetch profile history, write to wealth_history."""
    if _backfill_state["running"]:
        return
    conn = get_db()
    rows = conn.execute(
        "SELECT person_id, slug, common_name FROM persons WHERE slug IS NOT NULL"
    ).fetchall()
    conn.close()

    _backfill_state.update({
        "running": True, "done": 0, "total": len(rows), "errors": 0,
        "started_at": datetime.now().isoformat(), "finished_at": None,
    })
    logger.info(f"History backfill: {len(rows)} profiles to fetch")
    try:
        for person_id, slug, name in rows:
            try:
                stats = fetch_person_history(slug)
                insert_wealth_history(None, person_id, stats)
            except Exception as e:
                _backfill_state["errors"] += 1
                logger.warning(f"Backfill failed for {name} ({slug}): {e}")
            _backfill_state["done"] += 1
            time.sleep(delay_sec)
    finally:
        _backfill_state["running"] = False
        _backfill_state["finished_at"] = datetime.now().isoformat()
        logger.info(
            f"Backfill done: {_backfill_state['done']}/{_backfill_state['total']} "
            f"({_backfill_state['errors']} errors)"
        )


def get_backfill_state():
    return dict(_backfill_state)


def is_backfill_running():
    return _backfill_state["running"]
