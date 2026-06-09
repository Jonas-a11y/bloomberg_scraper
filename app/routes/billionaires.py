import threading

from fastapi import APIRouter, Query

from app.database import get_db
from app.family.queries import get_person_profile

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


@router.get("/billionaires/as-of")
def billionaires_as_of(
    date: str,
    limit: int = 500,
    country: str | None = None,
    industry: str | None = None,
    gender: str | None = None,
    q: str | None = None,
    sort: str = "rank",
):
    """Reconstruct the top-N ranking on any date we have data for.

    Two data layers are unioned:
    - **Bloomberg** (`wealth_history`) — daily, ~2012-03 onward, only for
      persons currently or recently in Bloomberg's top 500.
    - **Forbes historical** (`historical_rankings`) — annual snapshots from
      the Kaggle dataset (2001-2024) and a Wikipedia scraper. Includes
      historical billionaires Bloomberg never tracked or has dropped
      (deceased, fell off the list, etc.).

    Per-person preference: Bloomberg first (more precise), then Forbes for
    the most recent year ≤ target. Forbes rows without a Bloomberg link
    are kept and surfaced — they're real billionaires whose data widens
    the ranking. Their `person_id` will be NULL, signalling to the UI
    that no profile page exists.
    """
    if not date or len(date) < 10:
        return {"error": "date required, format YYYY-MM-DD"}
    target = date[:10]
    target_year = int(target[:4])

    conn = get_db()

    # Step 1: per-person Bloomberg observations at-or-before target.
    # For each person we pull THREE values:
    #   - wealth at-or-before target (the as-of value)
    #   - wealth at the day before that as-of (drives `last_change_usd`)
    #   - wealth at the start of target's year (drives `ytd_change_usd`)
    # The original endpoint only returned the first; we now compute
    # all three so historical rows have populated Daily / YTD columns
    # instead of "—" placeholders.
    target_year_start = f"{target_year}-01-01"
    bloomberg = conn.execute(
        """
        SELECT wh.person_id,
               (SELECT date FROM wealth_history
                WHERE person_id = wh.person_id AND date <= ?
                ORDER BY date DESC LIMIT 1) AS as_of_date,
               (SELECT net_worth_usd FROM wealth_history
                WHERE person_id = wh.person_id AND date <= ?
                ORDER BY date DESC LIMIT 1) AS net_worth_usd,
               -- One observation BEFORE the as-of value (skip 1)
               (SELECT net_worth_usd FROM wealth_history
                WHERE person_id = wh.person_id AND date <= ?
                ORDER BY date DESC LIMIT 1 OFFSET 1) AS prev_net_worth_usd,
               -- First observation of the target's year (or the closest
               -- before it if year started before our coverage)
               (SELECT net_worth_usd FROM wealth_history
                WHERE person_id = wh.person_id AND date <= ?
                ORDER BY date DESC LIMIT 1) AS year_start_net_worth_usd
        FROM wealth_history wh
        WHERE wh.date <= ?
        GROUP BY wh.person_id
        """,
        (target, target, target, target_year_start, target),
    ).fetchall()
    bloomberg_pids = {r["person_id"] for r in bloomberg}

    # Step 2: per-person Forbes observations at-or-before target_year.
    # We pull the most recent year ≤ target for each (person_id, name)
    # combination, picking forbes_kaggle over forbes_world.
    # We split into two queries: linked (have person_id) and unlinked
    # (NULL person_id, deduped by name).
    forbes_linked = conn.execute(
        """
        WITH ranked AS (
            SELECT *,
                ROW_NUMBER() OVER (
                    PARTITION BY person_id
                    ORDER BY year DESC,
                             CASE source
                                 WHEN 'forbes_kaggle' THEN 1
                                 WHEN 'forbes_world' THEN 2
                                 ELSE 3 END
                ) AS rn
            FROM historical_rankings
            WHERE year <= ? AND person_id IS NOT NULL
        )
        SELECT person_id, year, net_worth_usd, citizenship, age,
               industry, name, source
        FROM ranked WHERE rn = 1
        """,
        (target_year,),
    ).fetchall()

    forbes_unlinked = conn.execute(
        """
        WITH ranked AS (
            SELECT *,
                ROW_NUMBER() OVER (
                    PARTITION BY name
                    ORDER BY year DESC,
                             CASE source
                                 WHEN 'forbes_kaggle' THEN 1
                                 WHEN 'forbes_world' THEN 2
                                 ELSE 3 END
                ) AS rn
            FROM historical_rankings
            WHERE year <= ? AND person_id IS NULL
              -- Drop forbes_world rows for years where forbes_kaggle has any
              -- data — Kaggle is denser & pre-cleaned. forbes_world is
              -- kept only as a fallback for years past the Kaggle freeze.
              AND NOT (
                  source = 'forbes_world'
                  AND year IN (SELECT DISTINCT year FROM historical_rankings WHERE source = 'forbes_kaggle')
              )
        )
        SELECT NULL AS person_id, year, net_worth_usd, citizenship, age,
               industry, name, source
        FROM ranked WHERE rn = 1
        """,
        (target_year,),
    ).fetchall()

    # Step 3: persons table for metadata on Bloomberg-linked rows
    persons_meta = {}
    if bloomberg_pids or any(r["person_id"] for r in forbes_linked):
        all_pids = bloomberg_pids | {r["person_id"] for r in forbes_linked}
        placeholders = ",".join("?" * len(all_pids))
        for r in conn.execute(
            f"""SELECT person_id, common_name, full_name, citizenship, age,
                       birth_year, gender, gender_confidence, industry
                FROM persons WHERE person_id IN ({placeholders})""",
            list(all_pids),
        ).fetchall():
            persons_meta[r["person_id"]] = dict(r)
    conn.close()

    # Step 4: assemble the unified row set.
    # Per person_id we prefer Bloomberg (more precise). Forbes-linked rows
    # only fill in when Bloomberg has nothing for that person. Forbes-
    # unlinked rows always come through — they're additional names.
    bloomberg_by_pid = {r["person_id"]: r for r in bloomberg}
    forbes_by_pid = {r["person_id"]: r for r in forbes_linked}

    rows = []
    seen_pids = set()
    for pid, b in bloomberg_by_pid.items():
        if b["net_worth_usd"] is None:
            continue
        meta = persons_meta.get(pid, {})
        # Daily change: as-of - day before. Skip when we don't have
        # a prior observation (start of coverage).
        nw = b["net_worth_usd"]
        prev = b["prev_net_worth_usd"]
        last_change = (nw - prev) if (nw is not None and prev is not None) else None
        last_change_pct = (
            (last_change / prev) if (last_change is not None and prev) else None
        )
        # YTD change: as-of - year-start (or earliest prior obs). When
        # year_start_net_worth_usd is None (person had no data before
        # the target year) we leave YTD blank rather than show 0.
        ystart = b["year_start_net_worth_usd"]
        # Edge: if year_start_net_worth_usd equals nw because the as-of
        # IS the first observation of the year, YTD reads 0 — which is
        # correct (zero change since YTD started, by definition).
        ytd_change = (
            (nw - ystart) if (nw is not None and ystart is not None) else None
        )
        ytd_change_pct = (
            (ytd_change / ystart) if (ytd_change is not None and ystart) else None
        )
        rows.append({
            "person_id": pid,
            "as_of_date": b["as_of_date"],
            "net_worth_usd": nw,
            "last_change_usd": last_change,
            "last_change_pct": last_change_pct,
            "ytd_change_usd": ytd_change,
            "ytd_change_pct": ytd_change_pct,
            "common_name": meta.get("common_name"),
            "full_name": meta.get("full_name"),
            "citizenship": meta.get("citizenship"),
            "age": meta.get("age"),
            "birth_year": meta.get("birth_year"),
            "gender": meta.get("gender"),
            "gender_confidence": meta.get("gender_confidence"),
            "industry": meta.get("industry"),
            "source": "bloomberg",
        })
        seen_pids.add(pid)

    for pid, f in forbes_by_pid.items():
        if pid in seen_pids or f["net_worth_usd"] is None:
            continue
        meta = persons_meta.get(pid, {})
        rows.append({
            "person_id": pid,
            "as_of_date": f"{f['year']}-12-31",
            "net_worth_usd": f["net_worth_usd"],
            # Forbes is annual — no daily / YTD precision available
            "last_change_usd": None,
            "last_change_pct": None,
            "ytd_change_usd": None,
            "ytd_change_pct": None,
            "common_name": meta.get("common_name") or f["name"],
            "full_name": meta.get("full_name"),
            "citizenship": f["citizenship"] or meta.get("citizenship"),
            "age": f["age"] or meta.get("age"),
            "birth_year": meta.get("birth_year"),
            "gender": meta.get("gender"),
            "gender_confidence": meta.get("gender_confidence"),
            "industry": f["industry"] or meta.get("industry"),
            "source": f["source"],
        })

    for f in forbes_unlinked:
        if f["net_worth_usd"] is None:
            continue
        # Suppress likely-duplicates: if a linked row already in `rows`
        # shares a last-name token AND is within a similar net-worth bracket,
        # this unlinked Forbes row is probably the same person under a
        # different name spelling ("Bill Gates" vs "William Gates III").
        # Better to drop than to double-count in the ranking.
        f_last = (f["name"] or "").rsplit(" ", 1)[-1].lower().strip(".,")
        is_dupe = False
        for existing in rows:
            if not existing["common_name"]:
                continue
            e_last = existing["common_name"].rsplit(" ", 1)[-1].lower().strip(".,")
            if e_last == f_last and f_last and len(f_last) > 3:
                # Same last name; check if net worth is within 20% — Kaggle
                # and forbes_world usually agree closely on the same person
                e_w = existing["net_worth_usd"] or 0
                f_w = f["net_worth_usd"] or 0
                if e_w and f_w and abs(e_w - f_w) / max(e_w, f_w) < 0.20:
                    is_dupe = True
                    break
        if is_dupe:
            continue
        rows.append({
            "person_id": None,
            "as_of_date": f"{f['year']}-12-31",
            "net_worth_usd": f["net_worth_usd"],
            # Forbes annual — no daily / YTD precision
            "last_change_usd": None,
            "last_change_pct": None,
            "ytd_change_usd": None,
            "ytd_change_pct": None,
            "common_name": f["name"],
            "full_name": None,
            "citizenship": f["citizenship"],
            "age": f["age"],
            "birth_year": None,
            "gender": None,
            "gender_confidence": None,
            "industry": f["industry"],
            "source": f["source"],
        })

    # Apply filters in Python — small enough rowset that doing it here is
    # simpler than templating the SQL across three queries.
    if country:
        rows = [r for r in rows if r["citizenship"] == country]
    if industry:
        rows = [r for r in rows if r["industry"] == industry]
    if gender:
        rows = [r for r in rows if r["gender"] == gender]
    if q:
        ql = q.lower()
        rows = [r for r in rows if r["common_name"] and ql in r["common_name"].lower()]

    # Step 5: rank by net worth descending, assign rank, truncate.
    rows.sort(key=lambda r: -(r["net_worth_usd"] or 0))
    rows = rows[:limit]
    for i, r in enumerate(rows, start=1):
        r["rank"] = i

    # Optional re-sort if user asked for non-rank
    allowed = {"rank", "net_worth_usd", "common_name", "age", "as_of_date"}
    sort_col = sort.lstrip("-")
    if sort_col in allowed and sort_col != "rank":
        reverse = sort.startswith("-")
        rows.sort(key=lambda x: (x.get(sort_col) is None, x.get(sort_col)), reverse=reverse)

    return {
        "data": rows,
        "total": len(rows),
        "as_of": target,
    }


@router.get("/billionaires/diff")
def billionaires_diff(
    from_date: str,
    to_date: str,
    top: int = 500,
):
    """Diff the top-N at two dates. Returns:
    - entered: persons in `to_date` ranking who weren't in `from_date`
    - exited:  persons in `from_date` ranking who aren't in `to_date`
    - movers:  persons in both, with rank delta and net-worth delta
    Sorted lists, capped at 50 per category to keep payload small.
    """
    if not from_date or not to_date:
        return {"error": "from_date and to_date required"}

    a = billionaires_as_of(date=from_date, limit=top)
    b = billionaires_as_of(date=to_date, limit=top)
    if "error" in a:
        return a
    if "error" in b:
        return b

    a_by_id = {r["person_id"]: r for r in a["data"]}
    b_by_id = {r["person_id"]: r for r in b["data"]}

    entered = sorted(
        [b_by_id[pid] for pid in b_by_id if pid not in a_by_id],
        key=lambda r: r["rank"],
    )[:50]
    exited = sorted(
        [a_by_id[pid] for pid in a_by_id if pid not in b_by_id],
        key=lambda r: r["rank"],
    )[:50]

    movers = []
    for pid, b_row in b_by_id.items():
        if pid not in a_by_id:
            continue
        a_row = a_by_id[pid]
        rank_change = a_row["rank"] - b_row["rank"]  # positive = moved up
        worth_change = b_row["net_worth_usd"] - a_row["net_worth_usd"]
        worth_pct = (
            worth_change / a_row["net_worth_usd"] * 100
            if a_row["net_worth_usd"] else None
        )
        movers.append({
            "person_id": pid,
            "common_name": b_row["common_name"],
            "from_rank": a_row["rank"],
            "to_rank": b_row["rank"],
            "rank_change": rank_change,
            "from_worth": a_row["net_worth_usd"],
            "to_worth": b_row["net_worth_usd"],
            "worth_change": worth_change,
            "worth_pct": worth_pct,
        })
    top_gainers = sorted(
        [m for m in movers if (m["worth_change"] or 0) > 0],
        key=lambda m: -(m["worth_change"] or 0),
    )[:50]
    top_losers = sorted(
        [m for m in movers if (m["worth_change"] or 0) < 0],
        key=lambda m: (m["worth_change"] or 0),
    )[:50]

    return {
        "from_date": a.get("as_of"),
        "to_date": b.get("as_of"),
        "totals": {"from": a["total"], "to": b["total"]},
        "entered": entered,
        "exited": exited,
        "top_gainers": top_gainers,
        "top_losers": top_losers,
    }


@router.get("/billionaires/data-range")
def billionaires_data_range():
    """Min/max date for which we have *some* historical wealth data —
    Bloomberg wealth_history daily rows or Forbes historical_rankings
    annual snapshots. Used by the time-travel slider to know its bounds."""
    conn = get_db()
    bloom = conn.execute(
        "SELECT MIN(date) AS min_date, MAX(date) AS max_date FROM wealth_history"
    ).fetchone()
    forbes = conn.execute(
        "SELECT MIN(year) AS min_y, MAX(year) AS max_y FROM historical_rankings"
    ).fetchone()
    conn.close()
    candidates_min = []
    candidates_max = []
    if bloom and bloom["min_date"]:
        candidates_min.append(bloom["min_date"])
        candidates_max.append(bloom["max_date"])
    if forbes and forbes["min_y"]:
        candidates_min.append(f"{forbes['min_y']}-01-01")
        candidates_max.append(f"{forbes['max_y']}-12-31")
    if not candidates_min:
        return {"min_date": None, "max_date": None}
    return {
        "min_date": min(candidates_min),
        "max_date": max(candidates_max),
        "bloomberg_start": bloom["min_date"] if bloom else None,
        "forbes_years": [forbes["min_y"], forbes["max_y"]] if forbes and forbes["min_y"] else None,
    }


@router.get("/billionaires/{person_id}/history")
def person_history(person_id: int, clean: bool = True):
    """Wealth history for a single person.

    `clean=True` (default) runs `app.outliers.flag_outliers` over the
    series and replaces points it identifies as data-quality artifacts
    (sustained-revaluation jumps that bounce back). The original raw
    value stays available as `net_worth_usd_raw`, and `outlier=True`
    is set on the affected rows so chart code can render a marker.

    Pass `?clean=false` to get the unmodified series — useful for
    debugging the cleaner or when the caller wants the raw data
    (CSV exports, the audit panel)."""
    conn = get_db()
    cursor = conn.execute(
        "SELECT date AS scraped_at, net_worth_usd FROM wealth_history WHERE person_id = ? ORDER BY date",
        (person_id,),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    if not rows:
        cursor = conn.execute("""
            SELECT scraped_at, rank, net_worth_usd, last_change_usd, ytd_change_usd
            FROM snapshots WHERE person_id = ?
            ORDER BY scraped_at
        """, (person_id,))
        rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    if clean and rows:
        from app.outliers import flag_outliers
        flagged = flag_outliers(rows)
        # Swap raw → cleaned for charts; keep raw as a sibling field
        # so the UI / API consumer can audit what changed.
        for r in flagged:
            r["net_worth_usd_raw"] = r["net_worth_usd"]
            if r.get("outlier") and r.get("cleaned") is not None:
                r["net_worth_usd"] = r["cleaned"]
        return flagged
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


@router.get("/persons/{person_id}/profile")
def person_profile(person_id: int):
    profile = get_person_profile(person_id)
    if not profile:
        return {"error": "not found"}
    # If this person has never had news fetched, kick off a one-shot Wikipedia
    # backfill in the background. The current request returns whatever's in
    # the DB (probably empty); the next visit will see populated articles.
    # Guards inside run_news_backfill skip if already running and dedupe on
    # backfilled flag, so it's safe to call from a request handler.
    pending = _maybe_kick_off_news_fetch(person_id)
    profile["news_fetch_pending"] = pending
    return profile


@router.post("/persons/{person_id}/refresh-news")
def trigger_person_news_refresh(person_id: int):
    """Manual fetch trigger — used by the profile UI's "fetch news" button
    when articles are empty or stale."""
    pending = _maybe_kick_off_news_fetch(person_id, force=True)
    return {"pending": pending}


def _maybe_kick_off_news_fetch(person_id, force=False):
    """Background-trigger news backfill+refresh for a single person.
    Returns True if a fetch was started, False if already done/cached."""
    conn = get_db()
    row = conn.execute(
        "SELECT backfilled FROM news_fetched WHERE person_id = ?",
        (person_id,),
    ).fetchone()
    conn.close()
    if row and row["backfilled"] and not force:
        return False  # already done

    def _runner():
        # Run the single-person fetch directly so it doesn't block on (or
        # get blocked by) the global backfill state — that's reserved for
        # the long-running "everybody" passes.
        from app.wiki_news import fetch_wikipedia_news
        from app.news import fetch_news_for_person
        from app.database import get_db, get_network_db
        from datetime import datetime
        import json
        try:
            net = get_network_db()
            row = net.execute(
                "SELECT wikidata_metadata FROM persons_index WHERE person_id = ?",
                (person_id,),
            ).fetchone()
            net.close()
            wiki_url = None
            if row and row["wikidata_metadata"]:
                try:
                    wiki_url = json.loads(row["wikidata_metadata"]).get("wikipedia_url")
                except (ValueError, TypeError):
                    pass
            if wiki_url:
                articles = fetch_wikipedia_news(wiki_url, limit=200)
                if articles:
                    conn = get_db()
                    now = datetime.now().isoformat()
                    conn.executemany(
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
                    conn.commit()
                    conn.close()
            # Mark backfilled so we don't keep refetching on every visit.
            conn = get_db()
            conn.execute(
                "INSERT OR REPLACE INTO news_fetched (person_id, fetched_at, backfilled) "
                "VALUES (?, ?, 1)",
                (person_id, datetime.now().isoformat()),
            )
            conn.commit()
            # Quick recent-news pass via GDELT for the last 30d. Best-effort —
            # GDELT's IP throttle frequently 429s, swallow silently.
            main = get_db()
            person = main.execute(
                "SELECT full_name, common_name FROM persons WHERE person_id = ?",
                (person_id,),
            ).fetchone()
            main.close()
            name = (person["full_name"] if person else None) or (person["common_name"] if person else None)
            if name:
                recent = fetch_news_for_person(name, limit=20)
                if recent:
                    conn = get_db()
                    now = datetime.now().isoformat()
                    conn.executemany(
                        """
                        INSERT OR IGNORE INTO news_articles
                            (person_id, article_date, date_precision, title, url, source, importance, fetched_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (person_id, a["article_date"], a.get("date_precision", "day"),
                             a["title"], a["url"], a.get("source"), a["importance"], now)
                            for a in recent
                        ],
                    )
                    conn.commit()
                    conn.close()
            conn.close()
        except Exception:
            pass

    threading.Thread(target=_runner, daemon=True).start()
    return True


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
