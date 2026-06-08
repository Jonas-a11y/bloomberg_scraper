# app/routes/scraper_api.py
import threading

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from app.auth import (
    check_password, clear_cookie, is_authed, is_auth_required,
    issue_cookie, require_scraper_auth,
)
from app.database import get_db, get_history_coverage, sync_history_from_snapshots
from app.scheduler import (
    get_schedule_config,
    save_schedule_config,
    apply_schedule,
    run_scrape,
    is_running,
    get_next_run,
    run_history_backfill,
    is_backfill_running,
    get_backfill_state,
    run_news_refresh,
    get_news_refresh_state,
    run_news_backfill,
    get_news_backfill_state,
    run_bootstrap,
    is_bootstrap_running,
    get_bootstrap_state,
)
from app.models import ScheduleUpdate

router = APIRouter()


# =============================================================================
# Auth gate. POSTs to job triggers go through `require_scraper_auth`. The
# UI hits /scraper/auth to log in; the cookie set there satisfies the
# Depends() on every protected endpoint below.
# =============================================================================

class _AuthIn(BaseModel):
    password: str = ""


@router.get("/scraper/auth")
def auth_status(request: Request):
    """Whether auth is required and whether the caller currently has it.
    The UI uses this on tab open to decide whether to prompt."""
    return {
        "required": is_auth_required(),
        "authed": is_authed(request),
    }


@router.post("/scraper/auth")
def auth_login(payload: _AuthIn, response: Response):
    """Verify the password and set the session cookie. When auth is
    disabled (no SCRAPER_PASSWORD env var) every login succeeds — keeps
    the UI happy without forcing an env var on local dev."""
    if not check_password(payload.password):
        # Constant-time check already; just signal failure.
        return Response(
            content='{"ok": false}',
            media_type="application/json",
            status_code=401,
        )
    issue_cookie(response)
    return {"ok": True}


@router.delete("/scraper/auth")
def auth_logout(response: Response):
    clear_cookie(response)
    return {"ok": True}


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
def trigger_scrape(_: None = Depends(require_scraper_auth)):
    if is_running():
        return {"status": "already_running"}
    thread = threading.Thread(target=run_scrape, daemon=True)
    thread.start()
    return {"status": "started"}


@router.get("/scraper/schedule")
def get_schedule():
    return get_schedule_config()


@router.put("/scraper/schedule")
def update_schedule(config: ScheduleUpdate, _: None = Depends(require_scraper_auth)):
    save_schedule_config(config.times, config.timezone, config.enabled)
    apply_schedule()
    return get_schedule_config()


@router.post("/scraper/backfill-history")
def trigger_backfill(_: None = Depends(require_scraper_auth)):
    if is_backfill_running():
        return {"status": "already_running", **get_backfill_state()}
    thread = threading.Thread(target=run_history_backfill, daemon=True)
    thread.start()
    return {"status": "started"}


@router.get("/scraper/backfill-history")
def backfill_status():
    return {**get_backfill_state(), "coverage": get_history_coverage()}


@router.post("/scraper/sync-history")
def sync_history(_: None = Depends(require_scraper_auth)):
    added = sync_history_from_snapshots()
    return {"added": added, "coverage": get_history_coverage()}


@router.post("/scraper/refresh-news")
def trigger_news_refresh(force: bool = False, _: None = Depends(require_scraper_auth)):
    state = get_news_refresh_state()
    if state.get("running"):
        return {"status": "already_running", **state}
    thread = threading.Thread(
        target=run_news_refresh, kwargs={"force": force}, daemon=True
    )
    thread.start()
    return {"status": "started"}


@router.get("/scraper/refresh-news")
def news_refresh_status():
    return get_news_refresh_state()


@router.post("/scraper/forbes-backfill")
def trigger_forbes_backfill(
    start: int = 2002, end: int = 2019,
    _: None = Depends(require_scraper_auth),
):
    """Pull Forbes World's Billionaires lists from Wikipedia for each year
    in the range. Runs in a background thread; takes ~1 minute total at the
    polite 2s/year delay."""
    import threading
    from app.forbes_history import backfill_all

    def _run():
        try:
            backfill_all(start=start, end=end)
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started", "start": start, "end": end}


@router.post("/scraper/forbes-kaggle")
def trigger_forbes_kaggle(
    force_download: bool = False,
    _: None = Depends(require_scraper_auth),
):
    """Pull the Kaggle Forbes 1997-2023 dataset and import to
    historical_rankings. The CSV is cached at data/forbes_kaggle/ — pass
    force_download=true to refetch.

    Synchronous on purpose: the import is fast (<10s once the CSV is
    cached) and we want the response to surface auth errors clearly
    instead of swallowing them in a background thread."""
    from app.forbes_kaggle import run as run_kaggle
    try:
        result = run_kaggle(force_download=force_download)
        return {"status": "ok", **result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.post("/scraper/backfill-news")
def trigger_news_backfill(
    only_new: bool = True,
    _: None = Depends(require_scraper_auth),
):
    state = get_news_backfill_state()
    if state.get("running"):
        return {"status": "already_running", **state}
    thread = threading.Thread(
        target=run_news_backfill, kwargs={"only_new": only_new}, daemon=True
    )
    thread.start()
    return {"status": "started"}


@router.get("/scraper/backfill-news")
def news_backfill_status():
    return get_news_backfill_state()


@router.post("/scraper/bootstrap")
def trigger_bootstrap(_: None = Depends(require_scraper_auth)):
    """One-click 'load everything' for an empty deployment.

    Runs the full data-load pipeline in sequence: Forbes Kaggle →
    Forbes Wikipedia → Wikidata + family network → GDELT news refresh →
    Wikipedia citations news backfill → snapshot history sync.

    Bloomberg LIVE scrape is intentionally NOT included — that's the
    cadenced job under user control via the schedule.

    Each step is idempotent (skips already-done work), so re-running
    is safe and simply gap-fills."""
    if is_bootstrap_running():
        return {"status": "already_running", **get_bootstrap_state()}
    thread = threading.Thread(target=run_bootstrap, daemon=True)
    thread.start()
    return {"status": "started"}


@router.get("/scraper/bootstrap")
def bootstrap_status():
    return get_bootstrap_state()
