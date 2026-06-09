"""Insights tab endpoints.

Five focused chart endpoints:
- /insights/top-over-time      → bar-chart-race data (top N at each step)
- /insights/cohort-survival    → "where is the class of YEAR now"
- /insights/source-gap         → Forbes vs Bloomberg per-person valuation gaps
- /insights/inequality         → Gini + Lorenz curve points within the list
- /insights/count-over-time    → total billionaire count + breakdown over time

Each endpoint accepts simple filter params (country, industry, year_from,
year_to where applicable). The UI passes them through.
"""
import logging
from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter

from app.database import get_db
from app import insights_cache


def _cached(endpoint: str, params: dict, ttl_sec: int = 6 * 3600):
    """Decorator: wrap an endpoint handler so it goes through
    insights_cache. The decorated function still computes the answer;
    the wrapper just decides whether to call it.

    Usage:
        @router.get("/insights/foo")
        def foo(year: int = 2001):
            return _cached("/insights/foo", {"year": year})(_compute_foo)(year)

    For a less verbose pattern, callers below use this helper inline
    inside each handler — see the cached_or_compute() one-liner.
    """
    def wrap(compute):
        def go(*args, **kwargs):
            payload, _state, _age = insights_cache.cached_or_compute(
                endpoint, params, lambda: compute(*args, **kwargs),
                ttl_sec=ttl_sec,
            )
            return payload
        return go
    return wrap

logger = logging.getLogger(__name__)
router = APIRouter()


# Different sources spell industries differently — Bloomberg returns plain
# strings ("Technology"), Kaggle stores them as Python-list literals like
# `['Technology                    ']` with stray whitespace. Normalize to
# a single canonical label so the UI's color palette can do a stable
# lookup. Also collapse a few near-synonyms ("Finance & Investments" vs
# "Finance") so a person isn't a different color year-over-year.
_INDUSTRY_CANONICAL = {
    "finance": "Finance & Investments",
    "finance & investments": "Finance & Investments",
    "investments": "Finance & Investments",
    "fashion": "Fashion & Retail",
    "retail": "Fashion & Retail",
    "fashion & retail": "Fashion & Retail",
    "tech": "Technology",
    "technology": "Technology",
    "media": "Media & Entertainment",
    "media & entertainment": "Media & Entertainment",
    "telecom": "Telecom",
    "telecommunications": "Telecom",
    "construction": "Construction & Engineering",
    "construction & engineering": "Construction & Engineering",
    "metals": "Metals & Mining",
    "metals & mining": "Metals & Mining",
    "food": "Food & Beverage",
    "food & beverage": "Food & Beverage",
    "diversified": "Diversified",
    "energy": "Energy",
    "real estate": "Real Estate",
    "healthcare": "Healthcare",
    "manufacturing": "Manufacturing",
    "automotive": "Automotive",
    "logistics": "Logistics",
    "service": "Service",
    "sports": "Sports",
    "gambling & casinos": "Gambling & Casinos",
}


def _normalize_industry(raw):
    """Take whatever shape an industry came in — list-literal, plain
    string, None — and return a clean canonical label or None."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # Strip Python-list-literal wrappers from Kaggle data
    s = s.strip("[]")
    # Take the first comma-separated chunk (they're usually single-element)
    s = s.split(",")[0].strip()
    # Strip leftover quote characters
    s = s.strip("'\"").strip()
    if not s:
        return None
    return _INDUSTRY_CANONICAL.get(s.lower(), s)


def _historical_or_bloomberg_per_year(conn, year_from, year_to,
                                       country=None, industry=None):
    """Per-year, per-person net worth observations.

    Each year-end gets at most one row per person, sourced from:
      - Bloomberg wealth_history at YYYY-12-31 (or latest pre that year)
      - Otherwise historical_rankings (forbes_kaggle preferred)

    Returns rows of (year, person_id, name, net_worth_usd, citizenship,
    industry, source). Filter args applied after the SQL fetch.

    Performance: a naive correlated-subquery per (person, year) is too slow
    on the 1.8M-row wealth_history. We instead extract (person, year) ->
    max_date_in_year via a single GROUP BY, then JOIN back to grab the
    actual net worth. Reduces 25-year query from ~30s to ~1s.

    `forbes_world` rows (Wikipedia scrape) are dropped for any year where
    `forbes_kaggle` has data — Kaggle is denser and pre-cleaned, while
    the Wikipedia scrape sometimes captured vandalized table rows with
    inflated values. forbes_world is kept only as a fallback for years
    past the Kaggle freeze.
    """
    rows = conn.execute(
        """
        WITH bloomberg_per_year AS (
            -- For each (person, year), the latest date in that year.
            -- We then look up the wealth on that date by joining back.
            SELECT person_id,
                   CAST(substr(date, 1, 4) AS INTEGER) AS year,
                   MAX(date) AS max_date
            FROM wealth_history
            WHERE date >= ? AND date <= ?
            GROUP BY person_id, CAST(substr(date, 1, 4) AS INTEGER)
        ),
        bloomberg AS (
            SELECT bpy.person_id, bpy.year, wh.net_worth_usd
            FROM bloomberg_per_year bpy
            JOIN wealth_history wh
              ON wh.person_id = bpy.person_id AND wh.date = bpy.max_date
        ),
        kaggle_years AS (
            SELECT DISTINCT year FROM historical_rankings
            WHERE source = 'forbes_kaggle' AND year BETWEEN ? AND ?
        ),
        forbes AS (
            SELECT hr.year, hr.person_id, hr.net_worth_usd,
                   hr.name, hr.citizenship, hr.industry, hr.source,
                   ROW_NUMBER() OVER (
                       PARTITION BY hr.year, COALESCE(hr.person_id, 'name:' || hr.name)
                       ORDER BY CASE hr.source
                           WHEN 'forbes_kaggle' THEN 1
                           WHEN 'forbes_world' THEN 2
                           ELSE 3 END
                   ) AS rn
            FROM historical_rankings hr
            WHERE hr.year BETWEEN ? AND ?
              -- Drop forbes_world for years we have Kaggle data for.
              AND NOT (hr.source = 'forbes_world'
                       AND hr.year IN (SELECT year FROM kaggle_years))
        )
        SELECT b.year, b.person_id,
               p.common_name AS name,
               p.citizenship, p.industry,
               b.net_worth_usd,
               'bloomberg' AS source
        FROM bloomberg b
        JOIN persons p ON p.person_id = b.person_id
        UNION ALL
        SELECT f.year, f.person_id,
               COALESCE(p.common_name, f.name) AS name,
               COALESCE(p.citizenship, f.citizenship) AS citizenship,
               COALESCE(p.industry, f.industry) AS industry,
               f.net_worth_usd, f.source
        FROM forbes f
        LEFT JOIN persons p ON p.person_id = f.person_id
        WHERE f.rn = 1
          AND NOT EXISTS (
              SELECT 1 FROM bloomberg b2
              WHERE b2.year = f.year AND b2.person_id = f.person_id
          )
        """,
        (f"{year_from}-01-01", f"{year_to}-12-31",
         year_from, year_to,
         year_from, year_to),
    ).fetchall()

    out = []
    for r in rows:
        if country and r["citizenship"] != country:
            continue
        if industry and r["industry"] != industry:
            continue
        d = dict(r)
        d["industry"] = _normalize_industry(d.get("industry"))
        out.append(d)
    return out


@router.get("/insights/top-over-time")
def top_over_time(
    n: int = 10,
    year_from: int = 2001,
    year_to: int | None = None,
    country: str | None = None,
    industry: str | None = None,
):
    """Top-N net worth at each year-end, for a bar-chart race.

    Returns: { years: [...], frames: { year: [{ name, net_worth_usd, ... }] } }
    Each frame is sorted descending. UI animates between frames.

    Cached via insights_cache: the underlying computation joins
    snapshots × historical_rankings × wealth_history and is one of
    the slower endpoints on the production dataset."""
    if year_to is None:
        year_to = datetime.now().year
    return insights_cache.cached_or_compute(
        "/insights/top-over-time",
        {"n": n, "year_from": year_from, "year_to": year_to,
         "country": country, "industry": industry},
        lambda: _top_over_time_compute(n, year_from, year_to, country, industry),
    )[0]


def _top_over_time_compute(
    n: int, year_from: int, year_to: int,
    country: str | None, industry: str | None,
):
    conn = get_db()
    rows = _historical_or_bloomberg_per_year(
        conn, year_from, year_to, country, industry,
    )
    conn.close()

    # Pull image_url + last_name for each linked person from the network DB
    # so the UI can render avatars + nicer surnames in the bar-chart race.
    from app.database import get_network_db
    pids_seen = {r["person_id"] for r in rows if r.get("person_id")}
    images = {}
    if pids_seen:
        net = get_network_db()
        placeholders = ",".join("?" * len(pids_seen))
        for r in net.execute(
            f"SELECT person_id, image_url FROM persons_index "
            f"WHERE person_id IN ({placeholders})",
            list(pids_seen),
        ).fetchall():
            if r["image_url"]:
                images[r["person_id"]] = r["image_url"]
        net.close()

    by_year = defaultdict(list)
    for r in rows:
        d = dict(r)
        d["image_url"] = images.get(d.get("person_id"))
        by_year[d["year"]].append(d)
    frames = {}

    def _last_token(name):
        """Last surname-ish token, ignoring Roman-numeral and Jr./Sr.
        suffixes that are common in Forbes-style names ("William Gates III").
        Used to detect alternate-spelling duplicates."""
        if not name:
            return ""
        SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}
        tokens = [t.strip(".,") for t in name.split() if t.strip(".,")]
        while tokens and tokens[-1].lower() in SUFFIXES:
            tokens.pop()
        return tokens[-1].lower() if tokens else ""

    for year, ppl in by_year.items():
        # Dedupe alternate spellings within the same year:
        # if a forbes_world row shares last name + similar wealth with a
        # bloomberg/forbes_kaggle row, drop it. Same logic as the as-of view.
        ppl.sort(key=lambda r: -(r["net_worth_usd"] or 0))
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
            # Unlinked row: check against existing rows for last-name +
            # similar-wealth match
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
        frames[year] = deduped[:n]

    years = sorted(frames.keys())
    return {"years": years, "frames": {y: frames[y] for y in years}, "n": n}


@router.get("/insights/top-over-time-series")
def top_over_time_series(
    n: int = 12,
    year_from: int = 2001,
    year_to: int | None = None,
    country: str | None = None,
    industry: str | None = None,
):
    """Continuous-timeline data for the bar-chart race.

    Differs from /top-over-time which returns per-year frames. Here we
    return per-person monthly observations across the full range so the
    frontend can interpolate smoothly between any two points instead of
    snapping at year boundaries.

    Strategy:
    1. Identify the union of every person who was in the top-N at any
       year in the range (so they don't pop in mid-animation)
    2. For each, build a monthly value series:
       - Where Bloomberg has wealth_history rows that month → take the
         last day's value (one observation per month)
       - Where only Forbes has annual rows → use that year's value as
         the December anchor; the frontend will linearly interpolate
         between adjacent monthly anchors
    3. Strip industries to canonical labels and pull image_url so the
       frontend doesn't re-fetch persons_index for every render

    Returns:
    {
      "start": "YYYY-MM",   "end": "YYYY-MM",
      "persons": [{
        person_id, name, industry, image_url, citizenship, source,
        series: [{ "ym": "YYYY-MM", "v": int }, ...]   # monthly anchors
      }]
    }
    """
    if year_to is None:
        year_to = datetime.now().year
    return insights_cache.cached_or_compute(
        "/insights/top-over-time-series",
        {"n": n, "year_from": year_from, "year_to": year_to,
         "country": country, "industry": industry},
        lambda: _top_over_time_series_compute(
            n, year_from, year_to, country, industry,
        ),
    )[0]


def _top_over_time_series_compute(
    n: int, year_from: int, year_to: int,
    country: str | None, industry: str | None,
):
    conn = get_db()

    # Step 1: find every person who hit the top-N at any year-end. Reuse
    # the existing helper so the dedupe / forbes_world filtering stays
    # consistent with the per-year endpoint. Then filter the rows down to
    # forbes_kaggle / bloomberg only — forbes_world has known vandalism
    # bugs that wreck monthly interpolation (a single $100B fake row
    # interpolates everyone above the real top-N for years).
    rows = _historical_or_bloomberg_per_year(
        conn, year_from, year_to, country, industry,
    )
    rows = [r for r in rows if r.get("source") in ("bloomberg", "forbes_kaggle")]

    # Bucket per year so we can pick top-N each
    by_year = defaultdict(list)
    for r in rows:
        by_year[r["year"]].append(r)
    union_keys = set()
    for year, ppl in by_year.items():
        ppl.sort(key=lambda r: -(r["net_worth_usd"] or 0))
        # Same dedupe as top-over-time's frame builder
        seen_pids = set()
        deduped = []

        def _last_token(name):
            if not name:
                return ""
            SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}
            tokens = [t.strip(".,") for t in name.split() if t.strip(".,")]
            while tokens and tokens[-1].lower() in SUFFIXES:
                tokens.pop()
            return tokens[-1].lower() if tokens else ""

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
            for ex in deduped:
                if last and len(last) > 3 and _last_token(ex["name"]) == last:
                    e_w = ex["net_worth_usd"] or 0
                    f_w = r["net_worth_usd"] or 0
                    if e_w and f_w and abs(e_w - f_w) / max(e_w, f_w) < 0.20:
                        is_dupe = True
                        break
            if not is_dupe:
                deduped.append(r)
        for r in deduped[:n]:
            union_keys.add(r["person_id"] or ("name:" + (r["name"] or "")))

    # Pull metadata + Bloomberg monthly + Forbes annual for the union set.
    # We restrict to person_ids; the few unlinked union entries (rare in
    # the top-N) get a Forbes-only series.
    pids = [k for k in union_keys if isinstance(k, int)]
    name_keys = [k for k in union_keys if isinstance(k, str)]

    # Person metadata
    persons_meta = {}
    if pids:
        placeholders = ",".join("?" * len(pids))
        for r in conn.execute(
            f"""SELECT person_id, common_name, citizenship, industry
                FROM persons WHERE person_id IN ({placeholders})""",
            pids,
        ).fetchall():
            persons_meta[r["person_id"]] = dict(r)

    # Bloomberg monthly: last day of each month per person, restricted to
    # the date range.
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
    # We always anchor at December so monthly interpolation between two
    # Forbes years tells a consistent story.
    #
    # IMPORTANT: only use forbes_kaggle here. The legacy forbes_world
    # Wikipedia scrape has known vandalism bugs (e.g. Forrest Mars 2011
    # at $100B vs the real $13.8B). The Kaggle dataset covers 2001-2024,
    # so we don't need the Wikipedia scrape as a fallback for the
    # continuous-timeline view.
    forbes_series = defaultdict(list)
    forbes_unlinked_series = defaultdict(list)
    if pids or name_keys:
        # Restrict to the union — without this, the country/industry
        # filter is silently lost: forbes_series picks up anchors for
        # every person in historical_rankings, then `all_keys =
        # bloom_series | forbes_series` re-includes them in the output.
        # We accept rows where person_id ∈ pids OR (person_id IS NULL
        # AND name matches one of our name_keys).
        clauses = ["source = 'forbes_kaggle'", "year BETWEEN ? AND ?"]
        params = [year_from, year_to]
        union_clauses = []
        if pids:
            ph = ",".join("?" * len(pids))
            union_clauses.append(f"person_id IN ({ph})")
            params.extend(pids)
        if name_keys:
            # name_keys are stored as "name:Some Name" — strip the prefix.
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
                # Capture metadata for unlinked persons in case we don't have it
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

    # Image URLs from network DB
    images = {}
    if pids:
        from app.database import get_network_db
        net = get_network_db()
        placeholders = ",".join("?" * len(pids))
        for r in net.execute(
            f"SELECT person_id, image_url FROM persons_index "
            f"WHERE person_id IN ({placeholders})",
            pids,
        ).fetchall():
            if r["image_url"]:
                images[r["person_id"]] = r["image_url"]
        net.close()
    conn.close()

    # Merge Bloomberg + Forbes per person.
    # Bloomberg wins per-month when both have data. Forbes anchors fill
    # the gap; the frontend interpolates linearly between any two.
    persons_out = []
    all_keys = set(bloom_series.keys()) | set(forbes_series.keys()) | set(forbes_unlinked_series.keys())
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
            "industry": _normalize_industry(meta.get("industry")),
            "image_url": images.get(meta.get("person_id")) if meta.get("person_id") else None,
            "series": merged,
        })

    return {
        "start": f"{year_from}-01",
        "end": f"{year_to}-12",
        "n": n,
        "persons": persons_out,
    }


@router.get("/insights/cohort-survival")
def cohort_survival(year: int = 2001, top: int = 100):
    """For each person in the top-N at `year`, classify their fate today.

    Categories:
      - still_listed: appears in latest Bloomberg snapshot
      - dropped:      had Bloomberg history but isn't in latest snapshot
      - died:         Wikidata has a death_date
      - never_tracked: was on Forbes `year` but Bloomberg never picked them up
    """
    return insights_cache.cached_or_compute(
        "/insights/cohort-survival", {"year": year, "top": top},
        lambda: _cohort_survival_compute(year, top),
    )[0]


def _cohort_survival_compute(year: int, top: int):
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
    from app.database import get_network_db
    import json as _json
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


@router.get("/insights/source-gap")
def source_gap(year: int | None = None, limit: int = 30):
    """Per-person valuation gap: Forbes annual vs Bloomberg's snapshot
    closest to that year. Surfaces which billionaires the two sources
    most disagree about. Default year = most recent year for which we
    have BOTH Forbes annual + at least 100 Bloomberg observations."""
    return insights_cache.cached_or_compute(
        "/insights/source-gap", {"year": year, "limit": limit},
        lambda: _source_gap_compute(year, limit),
    )[0]


def _source_gap_compute(year: int | None, limit: int):
    conn = get_db()
    if year is None:
        # Pick the most recent year that has Forbes Kaggle data; clamp
        # to ≤ current year - 1 so we never use a partial year.
        from datetime import datetime as _dt
        max_year_row = conn.execute(
            "SELECT MAX(year) AS y FROM historical_rankings WHERE source = 'forbes_kaggle'"
        ).fetchone()
        year = min(
            max_year_row["y"] or _dt.now().year - 1,
            _dt.now().year - 1,
        )
    rows = conn.execute(
        """
        WITH forbes AS (
            SELECT person_id, name, net_worth_usd AS forbes_worth
            FROM historical_rankings
            WHERE year = ? AND source = 'forbes_kaggle' AND person_id IS NOT NULL
        ),
        bloomberg AS (
            SELECT wh.person_id, wh.net_worth_usd AS bloomberg_worth
            FROM wealth_history wh
            WHERE wh.date = (
                SELECT MAX(date) FROM wealth_history wh2
                WHERE wh2.person_id = wh.person_id AND wh2.date <= ? || '-12-31'
            )
        )
        SELECT f.person_id, COALESCE(p.common_name, f.name) AS name,
               p.citizenship, p.industry,
               f.forbes_worth, b.bloomberg_worth
        FROM forbes f
        JOIN bloomberg b ON b.person_id = f.person_id
        LEFT JOIN persons p ON p.person_id = f.person_id
        WHERE f.forbes_worth > 0 AND b.bloomberg_worth > 0
        """,
        (year, str(year)),
    ).fetchall()
    conn.close()

    pairs = []
    for r in rows:
        d = dict(r)
        f, b = d["forbes_worth"], d["bloomberg_worth"]
        d["gap_abs"] = b - f
        d["gap_pct"] = ((b - f) / f * 100) if f else 0
        pairs.append(d)
    # Sort by absolute % gap descending
    pairs.sort(key=lambda x: -abs(x["gap_pct"]))
    return {
        "year": year,
        "total_compared": len(pairs),
        "pairs": pairs[:limit],
    }


@router.get("/insights/inequality")
def inequality(year_from: int = 2001, year_to: int | None = None,
               country: str | None = None, industry: str | None = None):
    """Gini coefficient and Lorenz curve points per year, computed across
    the top 500 (or filtered subset) for that year.
    """
    if year_to is None:
        year_to = datetime.now().year
    return insights_cache.cached_or_compute(
        "/insights/inequality",
        {"year_from": year_from, "year_to": year_to,
         "country": country, "industry": industry},
        lambda: _inequality_compute(year_from, year_to, country, industry),
    )[0]


def _inequality_compute(year_from: int, year_to: int,
                        country: str | None, industry: str | None):
    conn = get_db()
    rows = _historical_or_bloomberg_per_year(
        conn, year_from, year_to, country, industry,
    )
    conn.close()

    by_year = defaultdict(list)
    for r in rows:
        if r["net_worth_usd"]:
            by_year[r["year"]].append(r["net_worth_usd"])

    series = []
    for year in sorted(by_year.keys()):
        values = sorted(by_year[year])
        n = len(values)
        if n < 2:
            continue
        total = sum(values)
        # Gini via the cumulative formula:
        #   G = (2 * sum(i * x_i) / (n * sum(x_i))) - (n + 1)/n
        cum = sum(i * v for i, v in enumerate(values, start=1))
        gini = (2 * cum) / (n * total) - (n + 1) / n
        # Lorenz curve: cumulative share of wealth held by bottom-x% of list
        # We sample 11 evenly-spaced points (0%, 10%, ..., 100%)
        lorenz = []
        cum_sum = 0
        for i, v in enumerate(values, start=1):
            cum_sum += v
            if i % max(1, n // 10) == 0 or i == n:
                lorenz.append({
                    "x": round(i / n, 3),
                    "y": round(cum_sum / total, 3),
                })
        # Top 10's share of total billionaire wealth — easier to grasp than
        # Gini.
        top10 = sum(values[-10:]) if n >= 10 else sum(values)
        top10_share = top10 / total
        series.append({
            "year": year,
            "n": n,
            "total_usd": total,
            "gini": round(gini, 4),
            "top10_share": round(top10_share, 4),
            "lorenz": lorenz,
        })
    return {"years": [s["year"] for s in series], "series": series}


@router.get("/insights/count-over-time")
def count_over_time(year_from: int = 2001, year_to: int | None = None,
                    by: str = "country"):
    """Number of billionaires over time, optionally split by country or
    industry. by: 'total' | 'country' | 'industry' | 'self_made'.
    """
    if year_to is None:
        year_to = datetime.now().year
    return insights_cache.cached_or_compute(
        "/insights/count-over-time",
        {"year_from": year_from, "year_to": year_to, "by": by},
        lambda: _count_over_time_compute(year_from, year_to, by),
    )[0]


def _count_over_time_compute(year_from: int, year_to: int, by: str):
    conn = get_db()
    rows = _historical_or_bloomberg_per_year(
        conn, year_from, year_to,
    )
    conn.close()

    if by == "total":
        counts = defaultdict(int)
        for r in rows:
            counts[r["year"]] += 1
        return {
            "by": "total",
            "series": [{"year": y, "count": counts[y]} for y in sorted(counts)],
        }

    if by == "industry":
        # Industry strings in the dataset are noisy ("['Technology']" etc.).
        # Strip brackets and quotes.
        def _norm(ind):
            if not ind:
                return "Unknown"
            s = str(ind).strip("[]")
            s = s.strip("'\"").strip()
            return s.split(",")[0].strip("'\"") or "Unknown"

        nested = defaultdict(lambda: defaultdict(int))
        for r in rows:
            nested[r["year"]][_norm(r["industry"])] += 1
        all_inds = set()
        for d in nested.values():
            all_inds.update(d.keys())
        # Pick top-10 industries by total count across all years
        ind_totals = defaultdict(int)
        for d in nested.values():
            for i, c in d.items():
                ind_totals[i] += c
        top_inds = sorted(ind_totals, key=lambda i: -ind_totals[i])[:10]
        series = {ind: [] for ind in top_inds}
        years = sorted(nested.keys())
        for ind in top_inds:
            for y in years:
                series[ind].append({"year": y, "count": nested[y].get(ind, 0)})
        return {"by": "industry", "years": years, "series": series}

    # by == "country"
    nested = defaultdict(lambda: defaultdict(int))
    for r in rows:
        if not r["citizenship"]:
            continue
        nested[r["year"]][r["citizenship"]] += 1
    country_totals = defaultdict(int)
    for d in nested.values():
        for c, n in d.items():
            country_totals[c] += n
    top_countries = sorted(country_totals, key=lambda c: -country_totals[c])[:10]
    years = sorted(nested.keys())
    series = {c: [] for c in top_countries}
    for c in top_countries:
        for y in years:
            series[c].append({"year": y, "count": nested[y].get(c, 0)})
    return {"by": "country", "years": years, "series": series}


# ─── Kriesel-style mining: discover hidden links from wealth-data alone ─────

@router.get("/insights/wealth-correlation")
def wealth_correlation(
    n: int = 30,
    days: int = 365,
    threshold: float = 0.85,
    end_date: str | None = None,
):
    """Pairwise daily-log-return correlation between the top N billionaires
    over the past `days` days (ending at `end_date`, default today).

    Strong correlations (|r| ≥ threshold) often signal hidden links: same
    company, co-founders, or holders of the same stock. We return both the
    full matrix (for the heatmap) and the strongest pairs (for the
    "discoveries" list).

    Scaling: at N=500 we do ~125k pair correlations. The naive
    Python-loop implementation took ~25s; numpy + a single matmul on
    z-scored returns brings it under 1s. Result is also cached — the
    second visitor sees an instant load.
    """
    return insights_cache.cached_or_compute(
        "/insights/wealth-correlation",
        {"n": n, "days": days, "threshold": threshold, "end_date": end_date},
        lambda: _wealth_correlation_compute(n, days, threshold, end_date),
    )[0]


def _wealth_correlation_compute(
    n: int, days: int, threshold: float, end_date: str | None,
):
    import math
    from datetime import date, timedelta

    # Hard cap to keep the endpoint bounded. 500 is plenty for the
    # full Bloomberg list and means a 500×500 = 250k-cell payload —
    # still under 5 MB JSON when each cell is a 5-char float string.
    n = max(2, min(int(n), 500))
    days = max(30, min(int(days), 3650))

    if end_date:
        try:
            end = date.fromisoformat(end_date[:10])
        except ValueError:
            end = date.today()
    else:
        end = date.today()
    start = end - timedelta(days=days)

    conn = get_db()
    # Top N by latest wealth
    top = conn.execute(
        """
        SELECT p.person_id, p.common_name
        FROM persons p
        JOIN snapshots s ON s.person_id = p.person_id
        WHERE s.scraped_at = (SELECT MAX(scraped_at) FROM snapshots)
        ORDER BY s.rank ASC
        LIMIT ?
        """,
        (n,),
    ).fetchall()
    pids = [r["person_id"] for r in top]
    name_by_pid = {r["person_id"]: r["common_name"] for r in top}
    if len(pids) < 2:
        conn.close()
        return {"persons": [], "matrix": [], "pairs": []}

    placeholders = ",".join("?" * len(pids))
    rows = conn.execute(
        f"""
        SELECT person_id, date, net_worth_usd
        FROM wealth_history
        WHERE person_id IN ({placeholders})
          AND date >= ? AND date <= ?
        ORDER BY person_id, date
        """,
        (*pids, start.isoformat(), end.isoformat()),
    ).fetchall()
    conn.close()

    # Try the vectorised numpy path first — orders of magnitude faster
    # at large N. Fall back to the pure-python loop if numpy isn't
    # available (it should always be — pandas pulls it — but the
    # fallback keeps the endpoint robust).
    try:
        return _correlation_numpy(pids, name_by_pid, rows, days, threshold)
    except ImportError:
        return _correlation_python(pids, name_by_pid, rows, days, threshold)


def _correlation_numpy(pids, name_by_pid, rows, days, threshold):
    """Vectorised correlation: build a (N × T) matrix of log-returns
    aligned on a shared date axis, mean-center + z-score each row,
    correlation = (Z @ Z.T) / pair_overlap_count.

    NaN-aware: we mask absent days per person, then count pairwise
    overlap separately so a pair with too few common days returns
    None instead of a near-random correlation.
    """
    import math
    import numpy as np

    # Group history rows by pid → list of (date, w)
    by_pid_dates = {}
    for r in rows:
        by_pid_dates.setdefault(r["person_id"], []).append(
            (r["date"], r["net_worth_usd"])
        )

    # Build the union of all dates across all persons, sorted.
    all_dates = sorted({
        d for series in by_pid_dates.values() for d, _ in series
    })
    if len(all_dates) < 31:
        # Not enough overlap is possible — return empty matrix gracefully
        return {
            "persons": [{"person_id": p, "name": name_by_pid[p]} for p in pids],
            "matrix": [[None] * len(pids) for _ in pids],
            "pairs": [],
            "days": days,
            "threshold": threshold,
            "note": "Not enough Bloomberg history to compute correlations.",
        }
    date_idx = {d: i for i, d in enumerate(all_dates)}

    # Per-pid log-return on the shared date axis. Days without an
    # observation (or where the previous day is missing) stay NaN.
    T = len(all_dates) - 1  # log-returns are between consecutive days
    N = len(pids)
    R = np.full((N, T), np.nan, dtype=np.float64)

    for i, pid in enumerate(pids):
        series = by_pid_dates.get(pid, [])
        # Build a (date_index → wealth) map then iterate consecutive
        # all_dates to compute log-returns where both ends exist.
        w_by_date = {d: w for d, w in series}
        for t in range(T):
            d_prev = all_dates[t]
            d_curr = all_dates[t + 1]
            w_prev = w_by_date.get(d_prev)
            w_curr = w_by_date.get(d_curr)
            if w_prev and w_curr and w_prev > 0 and w_curr > 0:
                R[i, t] = math.log(w_curr / w_prev)

    # Pairwise correlation, NaN-aware. We can't use np.corrcoef
    # directly because each pair has its own valid-mask; a single
    # global mask would throw away too much data.
    valid = ~np.isnan(R)                # (N, T) bool
    R0 = np.where(valid, R, 0.0)        # zero out NaNs for masked dot products

    # Pair overlap count: number of timesteps where BOTH rows are valid.
    overlap = valid.astype(np.int32) @ valid.astype(np.int32).T  # (N, N)

    # Per-pair sum of x, y, x*y, x², y² over the OVERLAP, computed
    # via masked dot products. mask_b zero-outs columns where the
    # other row is invalid.
    sum_x = np.zeros((N, N))
    sum_y = np.zeros((N, N))
    sum_xy = np.zeros((N, N))
    sum_xx = np.zeros((N, N))
    sum_yy = np.zeros((N, N))

    # Loop pairs only along i; vectorise over j. With N=500 this is
    # 500 outer iterations of a single matmul — milliseconds.
    for i in range(N):
        ri = R0[i]                       # (T,)
        vi = valid[i]                    # (T,) bool
        # mask_j: for each j, only timesteps where i and j both valid
        mask_j = valid & vi              # (N, T)
        rj = R0 * mask_j                 # (N, T)
        ri_masked = ri * mask_j          # (N, T) — i's row, zeroed where j invalid
        sum_x[i] = ri_masked.sum(axis=1)
        sum_y[i] = rj.sum(axis=1)
        sum_xy[i] = (ri_masked * rj).sum(axis=1)
        sum_xx[i] = (ri_masked * ri_masked).sum(axis=1)
        sum_yy[i] = (rj * rj).sum(axis=1)

    # Pearson r = (n·Σxy − ΣxΣy) / sqrt[(n·Σx² − (Σx)²)(n·Σy² − (Σy)²)]
    with np.errstate(invalid="ignore", divide="ignore"):
        num = overlap * sum_xy - sum_x * sum_y
        denom = np.sqrt(
            (overlap * sum_xx - sum_x ** 2) * (overlap * sum_yy - sum_y ** 2)
        )
        r = np.where(denom > 0, num / denom, np.nan)

    # Mask out pairs with too few overlap days — same threshold as the
    # python path (30 days).
    too_short = overlap < 30
    r = np.where(too_short, np.nan, r)
    np.fill_diagonal(r, 1.0)

    # Format matrix with rounded values; None for NaN (so JSON has null).
    matrix = []
    for i in range(N):
        row = []
        for j in range(N):
            v = r[i, j]
            if np.isnan(v):
                row.append(None)
            else:
                row.append(round(float(v), 3))
        matrix.append(row)

    # Strongest pairs above threshold. Cap proportional to N — at N=30
    # we used to return up to 50; at N=500 the user might want a few
    # hundred. Use 2N or 200, whichever is smaller (50 floor).
    pair_cap = max(50, min(200, 2 * N))
    pairs_idx = np.transpose(np.triu_indices(N, k=1))  # (P, 2)
    pairs = []
    for ii, jj in pairs_idx:
        v = r[ii, jj]
        if np.isnan(v) or abs(v) < threshold:
            continue
        pairs.append({
            "a_id": pids[ii], "a_name": name_by_pid[pids[ii]],
            "b_id": pids[jj], "b_name": name_by_pid[pids[jj]],
            "r": round(float(v), 3),
            "n_days": int(overlap[ii, jj]),
        })
    pairs.sort(key=lambda p: -abs(p["r"]))
    pairs = pairs[:pair_cap]

    return {
        "persons": [
            {"person_id": pid, "name": name_by_pid[pid]} for pid in pids
        ],
        "matrix": matrix,
        "pairs": pairs,
        "days": days,
        "threshold": threshold,
    }


def _correlation_python(pids, name_by_pid, rows, days, threshold):
    """Pure-python fallback. Used only when numpy isn't importable
    (shouldn't happen — pandas pulls it). Same result as the numpy
    path, just slower at large N."""
    import math

    by_pid_dates = {}
    for r in rows:
        by_pid_dates.setdefault(r["person_id"], []).append(
            (r["date"], r["net_worth_usd"])
        )
    by_pid = {}
    for pid, series in by_pid_dates.items():
        rets = {}
        for i in range(1, len(series)):
            d, w = series[i]
            _, prev = series[i - 1]
            if prev and w and w > 0 and prev > 0:
                rets[d] = math.log(w / prev)
        by_pid[pid] = rets

    def _corr(xs, ys):
        common = sorted(set(xs.keys()) & set(ys.keys()))
        if len(common) < 30:
            return None, len(common)
        x = [xs[d] for d in common]
        y = [ys[d] for d in common]
        mx = sum(x) / len(x)
        my = sum(y) / len(y)
        sx = sum((xi - mx) ** 2 for xi in x) ** 0.5
        sy = sum((yi - my) ** 2 for yi in y) ** 0.5
        if sx == 0 or sy == 0:
            return None, len(common)
        cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
        return cov / (sx * sy), len(common)

    matrix = []
    pairs = []
    for i, a in enumerate(pids):
        row = []
        for j, b in enumerate(pids):
            if i == j:
                row.append(1.0)
                continue
            if j < i:
                row.append(matrix[j][i])
                continue
            r, n_obs = _corr(by_pid.get(a, {}), by_pid.get(b, {}))
            row.append(round(r, 3) if r is not None else None)
            if r is not None and abs(r) >= threshold:
                pairs.append({
                    "a_id": a, "a_name": name_by_pid[a],
                    "b_id": b, "b_name": name_by_pid[b],
                    "r": round(r, 3),
                    "n_days": n_obs,
                })
        matrix.append(row)
    pair_cap = max(50, min(200, 2 * len(pids)))
    pairs.sort(key=lambda p: -abs(p["r"]))

    return {
        "persons": [
            {"person_id": pid, "name": name_by_pid[pid]} for pid in pids
        ],
        "matrix": matrix,
        "pairs": pairs[:pair_cap],
        "days": days,
        "threshold": threshold,
    }


@router.get("/insights/compare-pair")
def compare_pair(a: int, b: int, days: int = 365):
    """Side-by-side data for two billionaires + their pair-correlation.

    Used by the wealth-correlation heatmap: clicking a cell opens a
    comparison modal that needs both persons' headline stats and a
    chart of their wealth on the same time axis. Single round-trip.

    Returns:
      {
        a: {person_id, name, snapshot:{rank,wealth,...},
            citizenship, industry, age, gender, image_url},
        b: { ... same shape },
        correlation: {r, n_days, days, threshold},
        history: [
          {date, a_norm, b_norm, a_usd, b_usd}  # both normalised to 100
        ],
        shared: {country, industry}  # quick "why correlated?" hints
      }

    Cached per (a, b, days) — same numpy correlation core but only
    one pair, so it's cheap. We share the cached underlying log-return
    helpers via the routine wealth_correlation already uses.
    """
    days = max(30, min(int(days), 3650))
    return insights_cache.cached_or_compute(
        "/insights/compare-pair",
        {"a": a, "b": b, "days": days},
        lambda: _compare_pair_compute(a, b, days),
        ttl_sec=6 * 3600,
    )[0]


def _compare_pair_compute(a: int, b: int, days: int):
    import math
    from datetime import date, timedelta
    from app.database import get_network_db

    end = date.today()
    start = end - timedelta(days=days)

    main = get_db()

    def _person_stats(pid):
        p = main.execute(
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
        snap = main.execute(
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
        # All-time peak — gives the user a "where is this person in
        # their own arc?" anchor point alongside the current rank.
        peak = main.execute(
            """
            SELECT MAX(net_worth_usd) AS peak_usd,
                   (SELECT date FROM wealth_history wh2
                    WHERE wh2.person_id = ?
                    ORDER BY net_worth_usd DESC LIMIT 1) AS peak_date
            FROM wealth_history WHERE person_id = ?
            """,
            (pid, pid),
        ).fetchone()
        # First time on the list (earliest wealth_history row)
        first_seen = main.execute(
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

    A = _person_stats(a)
    B = _person_stats(b)
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

    # Pull image URLs for headers — same query the profile uses.
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

    # Build a unified history: each row has both A and B normalised
    # to 100 at first observation (so the chart shows relative
    # movement, comparable regardless of absolute scale).
    a_by_date = {r["date"]: r["net_worth_usd"] for r in rows_a if r["net_worth_usd"]}
    b_by_date = {r["date"]: r["net_worth_usd"] for r in rows_b if r["net_worth_usd"]}
    common_dates = sorted(set(a_by_date.keys()) & set(b_by_date.keys()))
    if common_dates:
        a0 = a_by_date[common_dates[0]]
        b0 = b_by_date[common_dates[0]]
    else:
        a0 = b0 = None

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

    # Compute correlation directly on the common-date log returns —
    # no need to call the full N×N machinery for one pair.
    if len(common_dates) >= 30:
        ra, rb = [], []
        for i in range(1, len(common_dates)):
            d_prev = common_dates[i - 1]
            d_curr = common_dates[i]
            wa_prev, wa_curr = a_by_date[d_prev], a_by_date[d_curr]
            wb_prev, wb_curr = b_by_date[d_prev], b_by_date[d_curr]
            if all(x and x > 0 for x in (wa_prev, wa_curr, wb_prev, wb_curr)):
                ra.append(math.log(wa_curr / wa_prev))
                rb.append(math.log(wb_curr / wb_prev))
        if len(ra) >= 30:
            mx = sum(ra) / len(ra)
            my = sum(rb) / len(rb)
            sx = sum((x - mx) ** 2 for x in ra) ** 0.5
            sy = sum((y - my) ** 2 for y in rb) ** 0.5
            if sx > 0 and sy > 0:
                cov = sum((x - mx) * (y - my) for x, y in zip(ra, rb))
                r_value = round(cov / (sx * sy), 3)
            else:
                r_value = None
            n_obs = len(ra)
        else:
            r_value, n_obs = None, len(ra)
    else:
        r_value, n_obs = None, len(common_dates)

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


@router.get("/insights/geo-migration")
def geo_migration():
    """Birth country → residence country flows from Wikidata metadata.

    Returns: list of {birth, residence, count, total_wealth_usd}.
    Persons whose birth and residence are the same are also returned
    (homebodies); the UI can choose to suppress them.
    """
    return insights_cache.cached_or_compute(
        "/insights/geo-migration", None,
        _geo_migration_compute,
    )[0]


def _geo_migration_compute():
    import json as _json
    from app.database import get_network_db

    main = get_db()
    persons = {
        r["person_id"]: dict(r) for r in main.execute(
            """
            SELECT p.person_id, p.common_name, p.citizenship,
                   s.net_worth_usd
            FROM persons p
            LEFT JOIN snapshots s ON s.person_id = p.person_id
              AND s.scraped_at = (SELECT MAX(scraped_at) FROM snapshots)
            """,
        ).fetchall()
    }
    main.close()

    net = get_network_db()
    rows = net.execute(
        "SELECT person_id, wikidata_metadata FROM persons_index "
        "WHERE wikidata_metadata IS NOT NULL"
    ).fetchall()
    net.close()

    # The Wikidata fields look like "City, Region, Country" or just
    # "City". The trailing chunk after a comma is usually the country
    # but lots of US entries say "Pretoria" (no comma) or "Houston"
    # which we'd misclassify as "Pretoria". Heuristic: if the trailing
    # chunk matches the person's `citizenship` field exactly OR is a
    # known country name, use it; otherwise fall back to citizenship.
    KNOWN_COUNTRIES = {
        "United States", "United Kingdom", "Canada", "Australia",
        "China", "India", "Russia", "Russian Federation", "Germany",
        "France", "Italy", "Spain", "Japan", "South Korea", "Brazil",
        "Mexico", "Saudi Arabia", "United Arab Emirates", "Israel",
        "South Africa", "Switzerland", "Sweden", "Norway", "Denmark",
        "Netherlands", "Belgium", "Austria", "Finland", "Ireland",
        "Singapore", "Hong Kong", "Taiwan", "Thailand", "Indonesia",
        "Malaysia", "Philippines", "Vietnam", "Turkey", "Egypt",
        "Nigeria", "Kenya", "Ukraine", "Poland", "Czech Republic",
        "Greece", "Portugal", "Argentina", "Chile", "Colombia",
        "Peru", "Venezuela", "New Zealand", "Lebanon", "Cyprus",
        "Monaco", "Liechtenstein", "Luxembourg",
    }
    # Different name forms for the same country collapsed to a canonical
    # label so the flow chart doesn't have separate "Russia" and "Russian
    # Federation" nodes etc.
    NORMALIZE = {
        "Russian Federation": "Russia",
        "Republic of China": "China",
        "PRC": "China",
        "U.S.": "United States",
        "USA": "United States",
        "UK": "United Kingdom",
        "U.K.": "United Kingdom",
    }

    def _country(s, fallback=None):
        if not s:
            return NORMALIZE.get(fallback, fallback) if fallback in KNOWN_COUNTRIES else None
        # Try the trailing comma-chunk first
        tail = s.split(",")[-1].strip()
        if tail in KNOWN_COUNTRIES:
            return NORMALIZE.get(tail, tail)
        # Try any chunk that's a known country
        for chunk in s.split(","):
            c = chunk.strip()
            if c in KNOWN_COUNTRIES:
                return NORMALIZE.get(c, c)
        # Fall back to person's citizenship if available
        if fallback and fallback in KNOWN_COUNTRIES:
            return NORMALIZE.get(fallback, fallback)
        return None

    flows = defaultdict(lambda: {"count": 0, "total_wealth_usd": 0,
                                  "people": []})
    nodes = {}
    for r in rows:
        try:
            blob = _json.loads(r["wikidata_metadata"])
        except (ValueError, TypeError):
            continue
        person = persons.get(r["person_id"], {})
        citizenship = person.get("citizenship")
        birth_c = _country(blob.get("birth_place"), citizenship)
        res_c = _country(blob.get("residence"), citizenship)
        if not birth_c and not res_c:
            continue
        wealth = person.get("net_worth_usd") or 0
        name = person.get("common_name", "?")
        if birth_c and res_c:
            key = (birth_c, res_c)
            flows[key]["count"] += 1
            flows[key]["total_wealth_usd"] += wealth
            flows[key]["people"].append({"name": name, "wealth": wealth})
        for c in (birth_c, res_c):
            if c:
                nodes.setdefault(c, {"persons": 0, "wealth": 0})
                nodes[c]["persons"] += 1
                nodes[c]["wealth"] += wealth

    flow_list = []
    for (b, r), v in flows.items():
        flow_list.append({
            "birth": b, "residence": r,
            "count": v["count"],
            "total_wealth_usd": v["total_wealth_usd"],
            "is_self_flow": b == r,
            "sample_people": sorted(
                v["people"], key=lambda p: -p["wealth"]
            )[:5],
        })
    flow_list.sort(key=lambda f: -f["total_wealth_usd"])

    return {
        "flows": flow_list,
        "nodes": [
            {"country": c, "persons": v["persons"], "wealth": v["wealth"]}
            for c, v in sorted(nodes.items(), key=lambda kv: -kv[1]["wealth"])
        ],
    }
