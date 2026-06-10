"""Cohort survival: where the class of YEAR is now.

For each person in the top-N at ``year``, classify their fate today
into one of four buckets:

* ``still_listed``  — they're in the latest Bloomberg snapshot
* ``dropped``        — Bloomberg has wealth_history rows but they're
                       not in the latest snapshot
* ``died``           — Wikidata has a death_date for them
* ``never_tracked``  — they're in Forbes' year but Bloomberg never
                       picked them up
"""
from __future__ import annotations

import json as _json

from app.database import get_db, get_network_db


def cohort_survival(year: int, top: int):
    conn = get_db()
    cohort = conn.execute(
        """
        WITH ranked AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY year
                ORDER BY net_worth_usd DESC
            ) AS rk
            FROM historical_rankings
            WHERE year = ? AND source = 'forbes_kaggle'
        )
        SELECT person_id, name, net_worth_usd, citizenship
        FROM ranked WHERE rk <= ?
        ORDER BY rk
        """,
        (year, top),
    ).fetchall()

    # Latest Bloomberg snapshot person_ids
    latest = {
        r[0] for r in conn.execute(
            "SELECT DISTINCT person_id FROM snapshots "
            "WHERE scraped_at = (SELECT MAX(scraped_at) FROM snapshots)"
        ).fetchall()
    }

    # Bloomberg-tracked person_ids (anyone with wealth_history)
    bloomberg_tracked = {
        r[0] for r in conn.execute(
            "SELECT DISTINCT person_id FROM wealth_history"
        ).fetchall()
    }
    conn.close()

    # Death dates from Wikidata metadata blob
    net = get_network_db()
    death_dates = {}
    for r in net.execute(
        "SELECT person_id, wikidata_metadata FROM persons_index "
        "WHERE wikidata_metadata IS NOT NULL"
    ).fetchall():
        try:
            blob = _json.loads(r["wikidata_metadata"])
            if blob.get("death_date"):
                death_dates[r["person_id"]] = blob["death_date"]
        except (ValueError, TypeError):
            pass
    net.close()

    counts = {"still_listed": 0, "dropped": 0, "died": 0, "never_tracked": 0}
    members = {"still_listed": [], "dropped": [], "died": [], "never_tracked": []}
    for r in cohort:
        d = dict(r)
        pid = d["person_id"]
        if pid and pid in death_dates:
            cat = "died"
            d["death_date"] = death_dates[pid]
        elif pid and pid in latest:
            cat = "still_listed"
        elif pid and pid in bloomberg_tracked:
            cat = "dropped"
        else:
            cat = "never_tracked"
        counts[cat] += 1
        members[cat].append(d)

    return {
        "year": year,
        "top": top,
        "total": len(cohort),
        "counts": counts,
        "members": members,
    }
