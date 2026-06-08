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
_news_refresh_state = {"running": False, "done": 0, "total": 0, "errors": 0, "saved": 0, "started_at": None, "finished_at": None}

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
        # Pull recent news for the top-ranked billionaires so the profile
        # chart has fresh annotations. Runs last so it doesn't fight the
        # other catch-up jobs for sockets.
        scheduler.add_job(
            run_news_refresh,
            "date",
            run_date=datetime.now() + timedelta(seconds=20),
            id=f"news_refresh_{run_id}",
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
            # Try common_name first ("Henry Kravis"), fall back to full_name
            # ("Henry Roberts Kravis"). Wikidata labels usually match the
            # short form.
            common = row["common_name"]
            full = full_names.get(row["person_id"])
            qid = None
            for candidate in (common, full):
                if not candidate:
                    continue
                qid = resolve_qid(candidate)
                if qid:
                    break
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


# Persons whose news_fetched.fetched_at is within this many hours are skipped
# on the next run. Daily refresh + 20h threshold gives a small safety margin.
NEWS_REFRESH_MIN_AGE_HOURS = 20

# Per-person rate limit between GDELT calls. Their docs ask for one request
# every 5 seconds — we use 6s as a polite buffer; they 429 when traffic spikes.
NEWS_REFRESH_DELAY_SEC = 6.0


def run_news_refresh(force=False, person_ids=None):
    """Fetch recent news for billionaires and store in news_articles.

    Default scope: every person in the latest snapshot, skipping anyone
    fetched within the last 20h. The 20h skip means a daily run only hits
    persons that haven't been refreshed yet — across days the queue rotates
    through the full 500.

    force=True ignores the freshness check (e.g. manual refresh trigger).
    person_ids constrains the run to specific people.
    """
    if _news_refresh_state["running"]:
        return
    from app.news import fetch_news_for_person

    conn = get_db()
    if person_ids:
        placeholders = ",".join("?" * len(person_ids))
        rows = conn.execute(
            f"""
            SELECT p.person_id, p.full_name, p.common_name
            FROM persons p
            WHERE p.person_id IN ({placeholders})
            """,
            list(person_ids),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT p.person_id, p.full_name, p.common_name
            FROM persons p
            JOIN snapshots s ON s.person_id = p.person_id
            WHERE s.scraped_at = (SELECT MAX(scraped_at) FROM snapshots)
            ORDER BY s.rank ASC
            """,
        ).fetchall()

    if not force:
        cutoff = (datetime.now() - timedelta(hours=NEWS_REFRESH_MIN_AGE_HOURS)).isoformat()
        recent = {
            r[0] for r in conn.execute(
                "SELECT person_id FROM news_fetched WHERE fetched_at > ?",
                (cutoff,),
            ).fetchall()
        }
        rows = [r for r in rows if r[0] not in recent]
    conn.close()

    if not rows:
        return

    _news_refresh_state.update({
        "running": True, "done": 0, "total": len(rows), "errors": 0, "saved": 0,
        "started_at": datetime.now().isoformat(), "finished_at": None,
    })
    logger.info(f"News refresh: {len(rows)} persons to fetch")
    try:
        for person_id, full_name, common_name in rows:
            name = full_name or common_name
            try:
                articles = fetch_news_for_person(name) if name else []
                if articles:
                    conn = get_db()
                    now = datetime.now().isoformat()
                    cur = conn.executemany(
                        """
                        INSERT OR IGNORE INTO news_articles
                            (person_id, article_date, date_precision, title, url, source, importance, fetched_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (person_id, a["article_date"], a.get("date_precision", "day"),
                             a["title"], a["url"], a.get("source"), a["importance"], now)
                            for a in articles
                        ],
                    )
                    _news_refresh_state["saved"] += cur.rowcount
                    conn.execute(
                        "INSERT OR REPLACE INTO news_fetched (person_id, fetched_at) VALUES (?, ?)",
                        (person_id, now),
                    )
                    conn.commit()
                    conn.close()
                else:
                    conn = get_db()
                    conn.execute(
                        "INSERT OR REPLACE INTO news_fetched (person_id, fetched_at) VALUES (?, ?)",
                        (person_id, datetime.now().isoformat()),
                    )
                    conn.commit()
                    conn.close()
            except Exception as e:
                _news_refresh_state["errors"] += 1
                logger.warning(f"News fetch failed for {name}: {e}")
            _news_refresh_state["done"] += 1
            time.sleep(NEWS_REFRESH_DELAY_SEC)
    finally:
        _news_refresh_state["running"] = False
        _news_refresh_state["finished_at"] = datetime.now().isoformat()
        logger.info(
            f"News refresh done: {_news_refresh_state['done']}/{_news_refresh_state['total']} "
            f"({_news_refresh_state['errors']} errors, {_news_refresh_state['saved']} saved)"
        )


def get_news_refresh_state():
    return dict(_news_refresh_state)


# GDELT 2.0 indexes news from Feb 2015 onwards. Backfilling this whole window
# year by year gives every billionaire a multi-year news timeline rather than
# just the past 30 days.
NEWS_BACKFILL_START_YEAR = 2015
NEWS_BACKFILL_LIMIT = 250  # max records GDELT returns per query

_news_backfill_state = {
    "running": False, "done": 0, "total": 0, "errors": 0, "saved": 0,
    "started_at": None, "finished_at": None, "current": None,
}


def run_news_backfill(person_ids=None, only_new=True):
    """Pull each person's full news timeline from their Wikipedia page.

    Wikipedia citations give us a curated, dated, decade-spanning timeline
    without rate limit pain — every cited news article in someone's bio is
    already a "significant event" by editorial selection.

    only_new=True (default) skips persons whose news_fetched.backfilled is
    already 1.
    """
    if _news_backfill_state["running"]:
        return
    from app.wiki_news import fetch_wikipedia_news

    # Pull Wikipedia URLs from network.db (populated by Wikidata enrichment)
    net = get_network_db()
    if person_ids:
        placeholders = ",".join("?" * len(person_ids))
        wiki_rows = net.execute(
            f"""SELECT person_id, wikidata_metadata FROM persons_index
                WHERE person_id IN ({placeholders}) AND wikidata_metadata IS NOT NULL""",
            list(person_ids),
        ).fetchall()
    else:
        wiki_rows = net.execute(
            "SELECT person_id, wikidata_metadata FROM persons_index "
            "WHERE wikidata_metadata IS NOT NULL"
        ).fetchall()
    net.close()

    wiki_url_by_pid = {}
    for r in wiki_rows:
        try:
            blob = json.loads(r["wikidata_metadata"])
            url = blob.get("wikipedia_url")
            if url:
                wiki_url_by_pid[r["person_id"]] = url
        except (ValueError, TypeError):
            pass

    main = get_db()
    if person_ids:
        placeholders = ",".join("?" * len(person_ids))
        rows = main.execute(
            f"""SELECT p.person_id, p.full_name, p.common_name
                FROM persons p WHERE p.person_id IN ({placeholders})""",
            list(person_ids),
        ).fetchall()
    else:
        # Include EVERYONE we have a Wikipedia URL for — current top-500 plus
        # anyone who's ever appeared in a snapshot (dropouts). Their
        # historical news is still relevant context.
        rows = main.execute(
            """
            SELECT DISTINCT p.person_id, p.full_name, p.common_name,
                COALESCE(MIN(s.rank), 999999) AS best_rank
            FROM persons p
            LEFT JOIN snapshots s ON s.person_id = p.person_id
            GROUP BY p.person_id
            ORDER BY best_rank ASC
            """,
        ).fetchall()
    rows = [(r[0], r[1], r[2]) for r in rows if r[0] in wiki_url_by_pid]

    if only_new:
        backfilled = {
            r[0] for r in main.execute(
                "SELECT person_id FROM news_fetched WHERE backfilled = 1"
            ).fetchall()
        }
        rows = [r for r in rows if r[0] not in backfilled]
    main.close()

    # Diagnostics: how big is the eligible pool, and what cuts shrink
    # it? Surface this in the state so the user can see WHY the
    # backfill might cover only a fraction of total billionaires
    # (most common cause: Wikidata enrichment has only resolved a
    # subset, so most persons don't have a wikipedia_url stored).
    rows_with_wiki = len(rows)
    if not rows:
        _news_backfill_state.update({
            "total": 0, "diagnostics": {
                "persons_with_wikipedia_url": len(wiki_url_by_pid),
                "eligible_after_only_new_filter": 0,
            },
        })
        return

    _news_backfill_state.update({
        "running": True, "done": 0, "total": len(rows), "errors": 0, "saved": 0,
        "started_at": datetime.now().isoformat(), "finished_at": None,
        "current": None,
        "diagnostics": {
            "persons_with_wikipedia_url": len(wiki_url_by_pid),
            "eligible_after_only_new_filter": rows_with_wiki,
        },
    })
    logger.info(
        f"News backfill (Wikipedia): {len(rows)} persons "
        f"(of {len(wiki_url_by_pid)} with Wikipedia URL stored)"
    )
    try:
        for person_id, full_name, common_name in rows:
            name = full_name or common_name
            _news_backfill_state["current"] = name
            wiki_url = wiki_url_by_pid.get(person_id)
            try:
                articles = fetch_wikipedia_news(wiki_url, limit=200)
                if articles:
                    conn = get_db()
                    now = datetime.now().isoformat()
                    cur = conn.executemany(
                        """
                        INSERT OR IGNORE INTO news_articles
                            (person_id, article_date, date_precision, title, url, source, importance, fetched_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (person_id, a["article_date"], a.get("date_precision", "day"),
                             a["title"], a["url"], a.get("source"), a["importance"], now)
                            for a in articles
                        ],
                    )
                    _news_backfill_state["saved"] += cur.rowcount
                    conn.commit()
                    conn.close()
            except Exception as e:
                _news_backfill_state["errors"] += 1
                logger.warning(f"Backfill {name}: {e}")
            # Mark person backfilled even when no articles came back — saves
            # us re-fetching pages with no citations.
            conn = get_db()
            conn.execute(
                "INSERT OR REPLACE INTO news_fetched (person_id, fetched_at, backfilled) "
                "VALUES (?, ?, 1)",
                (person_id, datetime.now().isoformat()),
            )
            conn.commit()
            conn.close()
            _news_backfill_state["done"] += 1
            # Wikipedia rate-limits IPs — 1 req/2s keeps us comfortably below
            # their threshold even when the backfill walks 200+ persons.
            time.sleep(2.0)
    finally:
        _news_backfill_state["running"] = False
        _news_backfill_state["current"] = None
        _news_backfill_state["finished_at"] = datetime.now().isoformat()
        logger.info(
            f"News backfill done: {_news_backfill_state['done']}/{_news_backfill_state['total']} "
            f"({_news_backfill_state['errors']} errors, {_news_backfill_state['saved']} saved)"
        )
        # Re-rank shared URLs once the batch is in. Cheap (O(N URLs)) and
        # idempotent so we can call it any time. Skipped silently on error
        # — the score table just falls back to per-article keyword scoring.
        try:
            changed = rescore_news_by_co_occurrence()
            if changed:
                logger.info(f"Co-occurrence rescore: bumped {changed} URLs")
        except Exception as e:
            logger.warning(f"Co-occurrence rescore failed: {e}")


def get_news_backfill_state():
    return dict(_news_backfill_state)


# =============================================================================
# Bootstrap pipeline — "run everything once" for an empty deployment.
# =============================================================================

# Each step runs one of the existing background jobs in sequence.
# The order is intentional: Forbes Kaggle first (gives us decades of
# history from a CC0 dataset, fast), then the slower Wikipedia / network
# / news jobs that depend on persons already being in the DB.
# Bloomberg LIVE scrape is intentionally NOT in here — that's the
# regular cadence job and the user controls it via the schedule.
BOOTSTRAP_STEPS = [
    {
        "key": "forbes_kaggle",
        "label": "Forbes Kaggle (history 2001–2024)",
        "run": "_bootstrap_forbes_kaggle",
        "state": "_backfill_state",
    },
    {
        "key": "forbes_wiki",
        "label": "Forbes Wikipedia (gap-fill)",
        "run": "_bootstrap_forbes_wiki",
        "state": "_backfill_state",
    },
    {
        "key": "network",
        "label": "Wikidata + family network refresh",
        "run": "_bootstrap_network",
        "state": "family_refresh",
    },
    {
        "key": "news_refresh",
        "label": "GDELT news refresh (latest)",
        "run": "_bootstrap_news_refresh",
        "state": "_news_refresh_state",
    },
    {
        "key": "news_backfill",
        "label": "Wikipedia citations news backfill",
        "run": "_bootstrap_news_backfill",
        "state": "_news_backfill_state",
    },
    {
        "key": "sync_history",
        "label": "Sync snapshot history",
        "run": "_bootstrap_sync_history",
        "state": None,
    },
]

_bootstrap_state = {
    "running": False,
    "step": None,        # current step key ("forbes_kaggle", …)
    "step_index": 0,     # 0-based
    "step_total": len(BOOTSTRAP_STEPS),
    "started_at": None,
    "finished_at": None,
    "step_results": [],  # list of {key, label, status, error?, started_at, finished_at}
    "error": None,
}


def is_bootstrap_running():
    return _bootstrap_state["running"]


def get_bootstrap_state():
    return dict(_bootstrap_state)


def _bootstrap_forbes_kaggle():
    """Run the Kaggle dataset import. Doesn't need to wait — the call
    is synchronous in this thread, no separate background job."""
    from app.forbes_kaggle import run as run_kaggle
    return run_kaggle(force_download=False)


def _bootstrap_forbes_wiki():
    """Forbes Wikipedia history scrape — runs synchronously here."""
    return run_history_backfill(only_new=True)


def _bootstrap_network():
    """Full Wikidata QID resolve + family network refresh. Blocks
    until done. Already-resolved persons are skipped."""
    from app.family.refresh import run_refresh
    return run_refresh()


def _bootstrap_news_refresh():
    """Single GDELT pull — fast, populates today's coverage."""
    return run_news_refresh(force=False)


def _bootstrap_news_backfill():
    """Wikipedia-citation news backfill. Skips persons already done."""
    return run_news_backfill(only_new=True)


def _bootstrap_sync_history():
    """Promote any orphan snapshot rows into wealth_history."""
    from app.database import sync_history_from_snapshots
    return sync_history_from_snapshots()


def run_bootstrap():
    """Sequentially run every data-loading job needed to bring an
    empty deployment up to a fully populated state. NOT idempotent in
    a destructive way — each step skips work that's already done — so
    re-running is safe and just fills any gaps.

    Excludes the live Bloomberg scrape (the user controls that via
    the schedule)."""
    if _bootstrap_state["running"]:
        logger.info("Bootstrap requested but already running")
        return
    _bootstrap_state.update({
        "running": True,
        "step": None,
        "step_index": 0,
        "step_total": len(BOOTSTRAP_STEPS),
        "started_at": datetime.now().isoformat(),
        "finished_at": None,
        "step_results": [],
        "error": None,
    })
    logger.info(f"Bootstrap start ({len(BOOTSTRAP_STEPS)} steps)")
    try:
        for i, step in enumerate(BOOTSTRAP_STEPS):
            _bootstrap_state["step"] = step["key"]
            _bootstrap_state["step_index"] = i
            started = datetime.now().isoformat()
            logger.info(f"Bootstrap step {i+1}/{len(BOOTSTRAP_STEPS)}: {step['label']}")
            result = {
                "key": step["key"],
                "label": step["label"],
                "started_at": started,
                "finished_at": None,
                "status": "running",
            }
            _bootstrap_state["step_results"].append(result)
            try:
                func = globals()[step["run"]]
                func()
                result["status"] = "ok"
            except Exception as e:
                # Don't abort the pipeline on one bad step — log it and
                # move on. The user gets a per-step status table so
                # they can re-run individual ones.
                logger.exception(f"Bootstrap step '{step['key']}' failed")
                result["status"] = "error"
                result["error"] = str(e)
            finally:
                result["finished_at"] = datetime.now().isoformat()
        ok = sum(1 for r in _bootstrap_state["step_results"] if r["status"] == "ok")
        logger.info(
            f"Bootstrap done: {ok}/{len(BOOTSTRAP_STEPS)} steps OK"
        )
    finally:
        _bootstrap_state["running"] = False
        _bootstrap_state["step"] = None
        _bootstrap_state["finished_at"] = datetime.now().isoformat()


def rescore_news_by_co_occurrence():
    """Boost importance for URLs cited across multiple billionaires.

    A URL that appears in N≥2 different persons' news_articles is reporting
    on a shared event (Bezos v Sánchez divorce, joint acquisition, etc.) —
    bump its importance by `2 × (N - 1)` capped at +12.

    Idempotent: tracks applied bonuses in news_co_occurrence so re-running
    only updates rows whose share-count has changed since last time.
    Returns the number of URLs whose score was changed."""
    conn = get_db()
    # Make sure the bookkeeping table exists. Idempotent CREATE.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS news_co_occurrence (
            url        TEXT PRIMARY KEY,
            shared_n   INTEGER NOT NULL,
            applied_at DATETIME NOT NULL
        )
        """
    )
    rows = conn.execute(
        """
        SELECT url, COUNT(DISTINCT person_id) AS shared, MIN(title) AS title
        FROM news_articles
        GROUP BY url
        HAVING COUNT(DISTINCT person_id) > 1
        """,
    ).fetchall()
    if not rows:
        conn.close()
        return 0

    prior = {
        r[0]: r[1] for r in conn.execute(
            "SELECT url, shared_n FROM news_co_occurrence",
        ).fetchall()
    }
    # Down-weight bulk listing pages — they're cited on dozens of profiles
    # but they're not events, just snapshots of "the rich list". Detect with
    # a few cheap markers.
    LIST_MARKERS = (
        "richest", "rich list", "billionaires index",
        " 100 ", " 200 ", " 50 ", " 500 ",
        "world's billion", "top billion",
    )
    changed = 0
    now = datetime.now().isoformat()
    for r in rows:
        url = r["url"]
        shared = r["shared"]
        title = (r["title"] or "").lower()
        is_listicle = any(m in title for m in LIST_MARKERS)
        prev = prior.get(url, 0)
        if shared == prev:
            continue
        # Real shared events: up to +12. Listicles: cap at +2 (recognition,
        # not significance) so they don't dominate the per-year top-N.
        cap = 2 if is_listicle else 12
        new_bonus = min(cap, 2 * (shared - 1))
        prev_cap = 2 if is_listicle else 12
        old_bonus = min(prev_cap, 2 * (prev - 1)) if prev else 0
        delta = new_bonus - old_bonus
        if delta == 0:
            conn.execute(
                "INSERT OR REPLACE INTO news_co_occurrence (url, shared_n, applied_at) "
                "VALUES (?, ?, ?)",
                (url, shared, now),
            )
            continue
        conn.execute(
            "UPDATE news_articles SET importance = importance + ? WHERE url = ?",
            (delta, url),
        )
        conn.execute(
            "INSERT OR REPLACE INTO news_co_occurrence (url, shared_n, applied_at) "
            "VALUES (?, ?, ?)",
            (url, shared, now),
        )
        changed += 1
    conn.commit()
    conn.close()
    return changed
