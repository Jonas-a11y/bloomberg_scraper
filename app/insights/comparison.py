"""Side-by-side comparison data for a pair of billionaires.

Used by the heatmap-cell click handler. Returns:

* ``a``, ``b``       — full person stats (rank, wealth, deltas,
                       industry, country, peak, first-seen, image,
                       biography, overview)
* ``correlation``    — Pearson r over the chosen window
* ``history``        — common-date wealth, both normalised to 100
                       at the first overlapping observation
* ``shared``         — quick "why correlated?" hints
                       (industry / country match)

One round-trip; the UI pops the modal without further fetches.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from app.database import get_db, get_network_db


def _person_stats(conn, pid: int):
    p = conn.execute(
        """
        SELECT p.person_id, p.common_name AS name, p.full_name,
               p.citizenship, p.industry, p.age, p.birth_year,
               p.gender, p.biography, p.overview
        FROM persons p
        WHERE p.person_id = ?
        """,
        (pid,),
    ).fetchone()
    if not p:
        return None
    snap = conn.execute(
        """
        SELECT s.scraped_at, s.rank, s.net_worth_usd,
               s.last_change_usd, s.ytd_change_usd,
               s.last_change_pct, s.ytd_change_pct,
               s.public_assets_total, s.private_assets_total,
               s.cash_assets_total
        FROM snapshots s
        WHERE s.person_id = ?
        ORDER BY s.scraped_at DESC LIMIT 1
        """,
        (pid,),
    ).fetchone()
    # All-time peak — gives the user a "where is this person in their
    # own arc?" anchor point alongside the current rank.
    peak = conn.execute(
        """
        SELECT MAX(net_worth_usd) AS peak_usd,
               (SELECT date FROM wealth_history wh2
                WHERE wh2.person_id = ?
                ORDER BY net_worth_usd DESC LIMIT 1) AS peak_date
        FROM wealth_history WHERE person_id = ?
        """,
        (pid, pid),
    ).fetchone()
    first_seen = conn.execute(
        "SELECT MIN(date) AS first_date FROM wealth_history WHERE person_id = ?",
        (pid,),
    ).fetchone()
    return {
        **dict(p),
        "snapshot": dict(snap) if snap else None,
        "peak_usd": peak["peak_usd"] if peak else None,
        "peak_date": peak["peak_date"] if peak else None,
        "first_seen_date": first_seen["first_date"] if first_seen else None,
    }


def _pair_correlation(common_dates, a_by_date, b_by_date):
    """Pearson r on log-returns over the common date axis. Returns
    (r_value, n_observations). r_value is None when there's < 30
    common-date observations or zero variance on either side."""
    if len(common_dates) < 30:
        return None, len(common_dates)
    ra, rb = [], []
    for i in range(1, len(common_dates)):
        d_prev = common_dates[i - 1]
        d_curr = common_dates[i]
        wa_prev, wa_curr = a_by_date[d_prev], a_by_date[d_curr]
        wb_prev, wb_curr = b_by_date[d_prev], b_by_date[d_curr]
        if all(x and x > 0 for x in (wa_prev, wa_curr, wb_prev, wb_curr)):
            ra.append(math.log(wa_curr / wa_prev))
            rb.append(math.log(wb_curr / wb_prev))
    if len(ra) < 30:
        return None, len(ra)
    mx = sum(ra) / len(ra)
    my = sum(rb) / len(rb)
    sx = sum((x - mx) ** 2 for x in ra) ** 0.5
    sy = sum((y - my) ** 2 for y in rb) ** 0.5
    if sx == 0 or sy == 0:
        return None, len(ra)
    cov = sum((x - mx) * (y - my) for x, y in zip(ra, rb))
    return round(cov / (sx * sy), 3), len(ra)


def compare_pair(a: int, b: int, days: int):
    end = date.today()
    start = end - timedelta(days=days)

    main = get_db()
    A = _person_stats(main, a)
    B = _person_stats(main, b)
    if not A or not B:
        main.close()
        return {"error": "person_not_found"}

    # Wealth history for both, in the chosen window.
    rows_a = main.execute(
        "SELECT date, net_worth_usd FROM wealth_history "
        "WHERE person_id = ? AND date >= ? AND date <= ? ORDER BY date",
        (a, start.isoformat(), end.isoformat()),
    ).fetchall()
    rows_b = main.execute(
        "SELECT date, net_worth_usd FROM wealth_history "
        "WHERE person_id = ? AND date >= ? AND date <= ? ORDER BY date",
        (b, start.isoformat(), end.isoformat()),
    ).fetchall()

    # Image URLs from the network DB.
    image_by_pid = {}
    try:
        net = get_network_db()
        for r in net.execute(
            "SELECT person_id, image_url FROM persons_index "
            "WHERE person_id IN (?, ?)",
            (a, b),
        ).fetchall():
            if r["image_url"]:
                image_by_pid[r["person_id"]] = r["image_url"]
        net.close()
    except Exception:
        pass
    main.close()

    A["image_url"] = image_by_pid.get(a)
    B["image_url"] = image_by_pid.get(b)

    # Build a unified history: each row has both A and B normalised to
    # 100 at first overlapping observation (so the chart shows relative
    # movement, comparable regardless of absolute scale).
    a_by_date = {r["date"]: r["net_worth_usd"] for r in rows_a if r["net_worth_usd"]}
    b_by_date = {r["date"]: r["net_worth_usd"] for r in rows_b if r["net_worth_usd"]}
    common_dates = sorted(set(a_by_date.keys()) & set(b_by_date.keys()))
    a0 = a_by_date[common_dates[0]] if common_dates else None
    b0 = b_by_date[common_dates[0]] if common_dates else None

    history = []
    for d in common_dates:
        a_v = a_by_date[d]
        b_v = b_by_date[d]
        history.append({
            "date": d,
            "a_usd": a_v,
            "b_usd": b_v,
            "a_norm": round(a_v / a0 * 100, 2) if a0 else None,
            "b_norm": round(b_v / b0 * 100, 2) if b0 else None,
        })

    r_value, n_obs = _pair_correlation(common_dates, a_by_date, b_by_date)

    # "Why might they correlate?" hints — quick wins for the user.
    shared = {
        "industry": A["industry"] if A["industry"] and A["industry"] == B["industry"] else None,
        "country": A["citizenship"] if A["citizenship"] and A["citizenship"] == B["citizenship"] else None,
    }

    return {
        "a": A,
        "b": B,
        "correlation": {
            "r": r_value,
            "n_days": n_obs,
            "days_window": days,
            "common_dates": len(common_dates),
        },
        "history": history,
        "shared": shared,
    }
