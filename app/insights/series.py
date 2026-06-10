"""Top-N billionaires over time, two flavours.

``top_over_time``         — per-year frames for the bar-chart race
                            (one row per person per year).
``top_over_time_series``  — interpolated monthly series for the
                            continuous-timeline view (one row per
                            person; series of monthly anchors).

Both share dedup logic for alternate-spelling Forbes rows
(``William Gates III`` vs ``Bill Gates``) — same last-name token +
similar net worth = drop the unlinked row in favour of the linked
one.
"""
from __future__ import annotations

from collections import defaultdict

from app.database import get_db, get_network_db
from .industries import normalize_industry
from .yearly import historical_or_bloomberg_per_year


_NAME_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}


def _last_token(name: str) -> str:
    """Last surname-ish token, ignoring Roman-numeral and Jr./Sr.
    suffixes that are common in Forbes-style names."""
    if not name:
        return ""
    tokens = [t.strip(".,") for t in name.split() if t.strip(".,")]
    while tokens and tokens[-1].lower() in _NAME_SUFFIXES:
        tokens.pop()
    return tokens[-1].lower() if tokens else ""


def _dedupe_alt_spellings(ppl):
    """Within a single year-bucket: prefer linked rows, drop unlinked
    rows that look like the same person under a different spelling
    (same last-name token + ≤20% wealth difference)."""
    seen_pids = set()
    deduped = []
    for r in ppl:
        pid = r.get("person_id")
        if pid:
            if pid in seen_pids:
                continue
            seen_pids.add(pid)
            deduped.append(r)
            continue
        last = _last_token(r["name"])
        is_dupe = False
        for existing in deduped:
            if not existing["name"]:
                continue
            e_last = _last_token(existing["name"])
            if e_last == last and last and len(last) > 3:
                e_w = existing["net_worth_usd"] or 0
                f_w = r["net_worth_usd"] or 0
                if e_w and f_w and abs(e_w - f_w) / max(e_w, f_w) < 0.20:
                    is_dupe = True
                    break
        if not is_dupe:
            deduped.append(r)
    return deduped


def _images_for(pids):
    """Pull image_url for a set of pids from the network DB."""
    if not pids:
        return {}
    images = {}
    net = get_network_db()
    placeholders = ",".join("?" * len(pids))
    for r in net.execute(
        f"SELECT person_id, image_url FROM persons_index "
        f"WHERE person_id IN ({placeholders})",
        list(pids),
    ).fetchall():
        if r["image_url"]:
            images[r["person_id"]] = r["image_url"]
    net.close()
    return images


# ──────────────────────────────────────────────────────────────────────────
# /insights/top-over-time — per-year frames
# ──────────────────────────────────────────────────────────────────────────

def top_over_time(n: int, year_from: int, year_to: int,
                  country: str | None, industry: str | None):
    """Top-N net worth at each year-end. Used by the bar-chart race
    on the Insights tab.

    Returns ``{years, frames: {year: [...]}, n}``. Each frame is sorted
    descending by net worth.
    """
    conn = get_db()
    rows = historical_or_bloomberg_per_year(
        conn, year_from, year_to, country, industry,
    )
    conn.close()

    pids_seen = {r["person_id"] for r in rows if r.get("person_id")}
    images = _images_for(pids_seen)

    by_year = defaultdict(list)
    for r in rows:
        d = dict(r)
        d["image_url"] = images.get(d.get("person_id"))
        by_year[d["year"]].append(d)

    frames = {}
    for year, ppl in by_year.items():
        ppl.sort(key=lambda r: -(r["net_worth_usd"] or 0))
        frames[year] = _dedupe_alt_spellings(ppl)[:n]

    years = sorted(frames.keys())
    return {"years": years, "frames": {y: frames[y] for y in years}, "n": n}


# ──────────────────────────────────────────────────────────────────────────
# /insights/top-over-time-series — continuous monthly series
# ──────────────────────────────────────────────────────────────────────────

def top_over_time_series(n: int, year_from: int, year_to: int,
                         country: str | None, industry: str | None):
    """Continuous-timeline data for the bar-chart race.

    Differs from ``top_over_time`` (per-year frames). Here we return
    per-person monthly observations across the full range so the
    frontend can interpolate smoothly between any two points instead
    of snapping at year boundaries.

    Strategy:
      1. Identify the union of every person who was in the top-N at
         any year in the range (so they don't pop in mid-animation).
      2. For each, build a monthly value series:
         - Bloomberg ``wealth_history`` rows that month → take the
           last day's value (one observation per month).
         - Otherwise Forbes annual rows → use that year's value as
           the December anchor; the frontend interpolates between
           adjacent monthly anchors.
      3. Strip industries to canonical labels and pull image_url so
         the frontend doesn't re-fetch persons_index for every render.

    Returns the JSON payload directly:
    ::

        {
          "start": "YYYY-MM",  "end": "YYYY-MM",
          "n": 12,
          "persons": [
            {person_id, name, industry, image_url, citizenship, source,
             series: [{ "ym": "YYYY-MM", "v": int }, ...]   # monthly anchors
            }, …
          ]
        }
    """
    conn = get_db()

    # Step 1: find every person who hit the top-N at any year-end.
    # Reuse the existing helper so the dedupe / forbes_world filtering
    # stays consistent with the per-year endpoint. Then filter rows
    # down to forbes_kaggle / bloomberg only — forbes_world has
    # known vandalism bugs that wreck monthly interpolation (a single
    # $100B fake row interpolates everyone above the real top-N for
    # years).
    rows = historical_or_bloomberg_per_year(
        conn, year_from, year_to, country, industry,
    )
    rows = [r for r in rows if r.get("source") in ("bloomberg", "forbes_kaggle")]

    by_year = defaultdict(list)
    for r in rows:
        by_year[r["year"]].append(r)
    union_keys = set()
    for year, ppl in by_year.items():
        ppl.sort(key=lambda r: -(r["net_worth_usd"] or 0))
        for r in _dedupe_alt_spellings(ppl)[:n]:
            union_keys.add(r["person_id"] or ("name:" + (r["name"] or "")))

    # Pull metadata + Bloomberg monthly + Forbes annual for the union
    # set. We restrict to person_ids; the few unlinked union entries
    # (rare in the top-N) get a Forbes-only series.
    pids = [k for k in union_keys if isinstance(k, int)]
    name_keys = [k for k in union_keys if isinstance(k, str)]

    persons_meta = {}
    if pids:
        placeholders = ",".join("?" * len(pids))
        for r in conn.execute(
            f"""SELECT person_id, common_name, citizenship, industry
                FROM persons WHERE person_id IN ({placeholders})""",
            pids,
        ).fetchall():
            persons_meta[r["person_id"]] = dict(r)

    # Bloomberg monthly: last day of each month per person, restricted
    # to the date range.
    bloom_series = defaultdict(list)
    if pids:
        placeholders = ",".join("?" * len(pids))
        for r in conn.execute(
            f"""
            WITH monthly AS (
                SELECT person_id,
                       substr(date, 1, 7) AS ym,
                       MAX(date) AS last_date
                FROM wealth_history
                WHERE person_id IN ({placeholders})
                  AND date BETWEEN ? AND ?
                GROUP BY person_id, substr(date, 1, 7)
            )
            SELECT m.person_id, m.ym, wh.net_worth_usd
            FROM monthly m
            JOIN wealth_history wh
              ON wh.person_id = m.person_id AND wh.date = m.last_date
            ORDER BY m.person_id, m.ym
            """,
            (*pids, f"{year_from}-01-01", f"{year_to}-12-31"),
        ).fetchall():
            if r["net_worth_usd"]:
                bloom_series[r["person_id"]].append(
                    {"ym": r["ym"], "v": r["net_worth_usd"]}
                )

    # Forbes annual: one anchor at YYYY-12 for each (person_id, year).
    # Anchored at December so monthly interpolation between two
    # Forbes years tells a consistent story.
    #
    # IMPORTANT: only forbes_kaggle here. The legacy forbes_world
    # Wikipedia scrape has known vandalism bugs (e.g. Forrest Mars
    # 2011 at $100B vs the real $13.8B). The Kaggle dataset covers
    # 2001-2024, so we don't need Wikipedia as a fallback for the
    # continuous-timeline view.
    #
    # Restrict to the union: without this, the country/industry filter
    # is silently lost — forbes_series picks up anchors for every
    # person in historical_rankings, then the merge step re-includes
    # them in the output.
    forbes_series = defaultdict(list)
    forbes_unlinked_series = defaultdict(list)
    if pids or name_keys:
        clauses = ["source = 'forbes_kaggle'", "year BETWEEN ? AND ?"]
        params = [year_from, year_to]
        union_clauses = []
        if pids:
            ph = ",".join("?" * len(pids))
            union_clauses.append(f"person_id IN ({ph})")
            params.extend(pids)
        if name_keys:
            names = [k[5:] for k in name_keys if k.startswith("name:")]
            if names:
                ph = ",".join("?" * len(names))
                union_clauses.append(
                    f"(person_id IS NULL AND name IN ({ph}))"
                )
                params.extend(names)
        if union_clauses:
            clauses.append("(" + " OR ".join(union_clauses) + ")")
        sql = f"""
            SELECT person_id, name, year, net_worth_usd, citizenship,
                   industry, source
            FROM historical_rankings
            WHERE {' AND '.join(clauses)}
        """
        for r in conn.execute(sql, params).fetchall():
            anchor = {"ym": f"{r['year']}-12", "v": r["net_worth_usd"]}
            if r["person_id"]:
                forbes_series[r["person_id"]].append(anchor)
                if r["person_id"] not in persons_meta:
                    persons_meta[r["person_id"]] = {
                        "person_id": r["person_id"],
                        "common_name": r["name"],
                        "citizenship": r["citizenship"],
                        "industry": r["industry"],
                    }
            else:
                key = "name:" + (r["name"] or "")
                if key in union_keys:
                    forbes_unlinked_series[key].append(anchor)
                    persons_meta.setdefault(key, {
                        "person_id": None,
                        "common_name": r["name"],
                        "citizenship": r["citizenship"],
                        "industry": r["industry"],
                    })

    images = _images_for(set(pids))
    conn.close()

    # Merge Bloomberg + Forbes per person. Bloomberg wins per-month
    # when both have data. Forbes anchors fill the gap; the frontend
    # interpolates linearly between any two.
    persons_out = []
    all_keys = (set(bloom_series.keys()) | set(forbes_series.keys())
                | set(forbes_unlinked_series.keys()))
    for k in all_keys:
        if isinstance(k, int):
            bloom = bloom_series.get(k, [])
            forbes = forbes_series.get(k, [])
            meta = persons_meta.get(k, {})
        else:
            bloom = []
            forbes = forbes_unlinked_series.get(k, [])
            meta = persons_meta.get(k, {})

        bloom_yms = {b["ym"] for b in bloom}
        merged = list(bloom)
        for f in forbes:
            if f["ym"] not in bloom_yms:
                merged.append(f)
        merged.sort(key=lambda x: x["ym"])
        if not merged:
            continue

        persons_out.append({
            "person_id": meta.get("person_id"),
            "name": meta.get("common_name") or "?",
            "citizenship": meta.get("citizenship"),
            "industry": normalize_industry(meta.get("industry")),
            "image_url": images.get(meta.get("person_id")) if meta.get("person_id") else None,
            "series": merged,
        })

    return {
        "start": f"{year_from}-01",
        "end": f"{year_to}-12",
        "n": n,
        "persons": persons_out,
    }
