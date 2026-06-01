# app/scheduler.py
import json
import logging
import time
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from app.database import get_db, get_network_db, insert_scrape_data, insert_wealth_history
from app.scraper import scrape_billionaires, fetch_person_history

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()
_is_running = False
_backfill_state = {"running": False, "done": 0, "total": 0, "errors": 0, "started_at": None, "finished_at": None}
_newcomer_wikidata_state = {"running": False, "done": 0, "total": 0, "started_at": None, "finished_at": None}

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
        # Pick up history for any newcomers in the background. apscheduler runs
        # this on its own thread so the scrape thread can exit cleanly.
        scheduler.add_job(
            run_history_backfill,
            "date",
            run_date=datetime.now() + timedelta(seconds=5),
            kwargs={"only_new": True},
            id=f"newcomer_backfill_{run_id}",
            replace_existing=True,
        )
        # Also resolve Wikidata QIDs and pull authoritative gender/metadata for
        # newcomers so a fresh billionaire doesn't sit on heuristic gender
        # until the next manual network refresh. Runs a few seconds later so
        # the two background jobs don't fight for the same connections.
        scheduler.add_job(
            run_newcomer_wikidata_catchup,
            "date",
            run_date=datetime.now() + timedelta(seconds=10),
            id=f"newcomer_wikidata_{run_id}",
            replace_existing=True,
        )
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


def run_history_backfill(delay_sec=1.5, only_new=False):
    """Fetch profile history for persons and write to wealth_history.

    only_new=True restricts to persons not yet in history_backfilled, which is
    what runs automatically after a scrape picks up newcomers.

    Otherwise, fetches every person currently in the latest snapshot. Dropouts
    are deliberately skipped so a stale/empty Bloomberg profile page can't
    overwrite the history we already captured for them."""
    if _backfill_state["running"]:
        return
    conn = get_db()
    if only_new:
        rows = conn.execute("""
            SELECT p.person_id, p.slug, p.common_name
            FROM persons p
            LEFT JOIN history_backfilled h ON h.person_id = p.person_id
            WHERE p.slug IS NOT NULL AND h.person_id IS NULL
        """).fetchall()
    else:
        rows = conn.execute("""
            SELECT p.person_id, p.slug, p.common_name
            FROM persons p
            JOIN snapshots s ON s.person_id = p.person_id
            WHERE p.slug IS NOT NULL
              AND s.scraped_at = (SELECT MAX(scraped_at) FROM snapshots)
            GROUP BY p.person_id
        """).fetchall()
    conn.close()

    if not rows:
        return

    _backfill_state.update({
        "running": True, "done": 0, "total": len(rows), "errors": 0,
        "started_at": datetime.now().isoformat(), "finished_at": None,
    })
    logger.info(f"History backfill ({'new only' if only_new else 'all'}): {len(rows)} profiles to fetch")
    try:
        for person_id, slug, name in rows:
            try:
                stats = fetch_person_history(slug)
                insert_wealth_history(None, person_id, stats)
                conn = get_db()
                conn.execute(
                    "INSERT OR REPLACE INTO history_backfilled (person_id, backfilled_at) VALUES (?, ?)",
                    (person_id, datetime.now().isoformat()),
                )
                conn.commit()
                conn.close()
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


def run_newcomer_wikidata_catchup():
    """Resolve Wikidata QIDs and pull authoritative gender for newcomers.

    Runs after each successful scrape so a brand-new billionaire gets their
    correct Wikidata-derived gender (and any other metadata we already cache)
    written into persons.gender / persons_index without waiting for the next
    manual network refresh.

    Scope is intentionally limited to persons whose persons_index row still
    has wikidata_qid IS NULL — i.e. genuine newcomers. We also skip if a full
    network refresh is already running to avoid concurrent SPARQL traffic."""
    if _newcomer_wikidata_state["running"]:
        return
    from app.family.refresh import is_running as is_refresh_running
    if is_refresh_running():
        logger.info("Newcomer Wikidata catch-up skipped: full refresh already running")
        return

    from app.family.resolver import resolve_qid, sync_persons_index
    from app.family.wikidata import fetch_person_metadata, fetch_wikipedia_thumbnail

    sync_persons_index()
    net = get_network_db()
    main = get_db()
    pending = net.execute(
        "SELECT person_id, common_name FROM persons_index WHERE wikidata_qid IS NULL"
    ).fetchall()
    full_names = {
        row["person_id"]: row["full_name"]
        for row in main.execute("SELECT person_id, full_name FROM persons").fetchall()
    }
    main.close()
    net.close()

    if not pending:
        return

    _newcomer_wikidata_state.update({
        "running": True, "done": 0, "total": len(pending),
        "started_at": datetime.now().isoformat(), "finished_at": None,
    })
    logger.info(f"Newcomer Wikidata catch-up: resolving {len(pending)} persons")
    try:
        resolved = []
        for row in pending:
            name = full_names.get(row["person_id"]) or row["common_name"]
            qid = resolve_qid(name) if name else None
            if qid:
                net = get_network_db()
                net.execute(
                    "UPDATE persons_index SET wikidata_qid = ? WHERE person_id = ?",
                    (qid, row["person_id"]),
                )
                net.commit()
                net.close()
                resolved.append((row["person_id"], qid))
            _newcomer_wikidata_state["done"] += 1
            time.sleep(0.2)

        if not resolved:
            return

        qids = [q for _, q in resolved]
        meta = fetch_person_metadata(qids)
        if not meta:
            return

        for qid, info in meta.items():
            if not info.get("image_filename") and info.get("wikipedia_url"):
                thumb = fetch_wikipedia_thumbnail(info["wikipedia_url"])
                if thumb:
                    info["image_url"] = thumb

        from urllib.parse import quote
        net = get_network_db()
        updates = []
        for qid, info in meta.items():
            image_url = info.get("image_url")
            if not image_url and info.get("image_filename"):
                image_url = (
                    "https://commons.wikimedia.org/wiki/Special:FilePath/"
                    + quote(info["image_filename"]) + "?width=320"
                )
            blob = {k: v for k, v in info.items() if k not in (
                "image_filename", "image_url", "signature_filename"
            )}
            updates.append((
                image_url,
                info.get("signature_filename"),
                json.dumps(blob, ensure_ascii=False) if blob else None,
                qid,
            ))
        net.executemany(
            "UPDATE persons_index SET image_url = ?, signature_filename = ?, "
            "wikidata_metadata = ? WHERE wikidata_qid = ?",
            updates,
        )
        net.commit()

        qid_to_pid = dict(resolved)  # person_id -> qid
        pid_by_qid = {q: pid for pid, q in qid_to_pid.items()}
        gender_updates = [
            (info["gender"], pid_by_qid[qid])
            for qid, info in meta.items()
            if info.get("gender") and qid in pid_by_qid
        ]
        net.close()
        if gender_updates:
            main = get_db()
            main.executemany(
                "UPDATE persons SET gender = ?, gender_confidence = 1.0 WHERE person_id = ?",
                gender_updates,
            )
            main.commit()
            main.close()
        logger.info(
            f"Newcomer Wikidata catch-up: resolved {len(resolved)} QIDs, "
            f"updated {len(gender_updates)} genders"
        )
    except Exception as e:
        logger.exception(f"Newcomer Wikidata catch-up failed: {e}")
    finally:
        _newcomer_wikidata_state["running"] = False
        _newcomer_wikidata_state["finished_at"] = datetime.now().isoformat()


def get_newcomer_wikidata_state():
    return dict(_newcomer_wikidata_state)
