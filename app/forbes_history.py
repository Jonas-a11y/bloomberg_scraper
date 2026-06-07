"""Forbes historical rankings backfill.

Pulls annual "The World's Billionaires" tables from Wikipedia and writes
to historical_rankings. Each year's article has its own URL, e.g.
`The_World's_Billionaires_2015`. We parse the wikitable rows and extract
rank, name, net worth, age, citizenship, source-of-wealth.

Then a second pass tries to link each row to an existing Bloomberg
person_id by exact name match (or via Wikidata QID resolution if needed).
Rows without a person_id stay in the table — they widen the historical
ranking even if we can't link them to a profile.

Caveats:
- Wikitable formatting varies year to year. The parser is forgiving and
  skips rows whose layout doesn't match (we'd rather drop a few rows than
  insert wrong data).
- 2024+ articles seem to live under different titles and aren't tried by
  default; pass them in explicitly via `years`.
"""
import logging
import re
import time
from datetime import date

from curl_cffi import requests

from app.database import get_db
from app.wiki_news import WIKI_API, USER_AGENT, WIKI_RETRY_DELAYS_SEC

logger = logging.getLogger(__name__)

# How long to wait between Wikipedia fetches in the backfill loop.
FORBES_FETCH_DELAY_SEC = 2.0

# Wikipedia's per-year page slug. The API auto-resolves redirects so the
# canonical title may differ slightly in the response.
WIKI_PAGE_TEMPLATE = "The World's Billionaires {year}"

# Earliest year with a per-year Wikipedia article.
DEFAULT_START_YEAR = 2002
DEFAULT_END_YEAR = 2019  # 2020+ titles live elsewhere; pass explicit years if needed


def _fetch_wikitext(page_title, timeout=20):
    """Pull the wikitext of a Wikipedia article. Retries on 429."""
    for attempt, delay in enumerate([0] + WIKI_RETRY_DELAYS_SEC):
        if delay:
            logger.info(f"Wikipedia throttled, sleeping {delay}s for {page_title}")
            time.sleep(delay)
        try:
            r = requests.get(
                WIKI_API,
                params={
                    "action": "parse",
                    "page": page_title,
                    "format": "json",
                    "prop": "wikitext",
                    "redirects": 1,
                    "formatversion": 2,
                },
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=timeout,
                impersonate="chrome",
            )
        except Exception as e:
            logger.warning(f"Wikipedia fetch failed for {page_title}: {e}")
            return None
        body_preview = r.text[:200].lower() if hasattr(r, "text") else ""
        throttled = (
            r.status_code == 429
            or "too many requests" in body_preview
            or "<html" in body_preview
        )
        if not throttled:
            break
    else:
        return None
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except Exception:
        return None
    if "error" in data:
        logger.info(f"Wikipedia: {data['error'].get('info')}")
        return None
    return data.get("parse", {}).get("wikitext", "")


def _strip_wiki_markup(s):
    """Best-effort: turn `[[Apple Inc.|Apple]]` → `Apple`, drop other
    template noise. Used on per-cell strings in the wikitable."""
    if not s:
        return ""
    # Resolve {{sortname|First|Last}} → First Last (Wikipedia's sortable
    # name template; pipe-separated parts that are NOT key=value).
    def _sortname(m):
        parts = [p.strip() for p in m.group(1).split("|") if "=" not in p]
        return " ".join(parts)
    s = re.sub(r"\{\{\s*sortname\s*\|([^{}]*)\}\}", _sortname, s, flags=re.IGNORECASE)
    s = re.sub(r"\{\{\s*nts\s*\|([^{}|]*)[^{}]*\}\}", r"\1", s, flags=re.IGNORECASE)
    # Resolve [[A|B]] → B, [[A]] → A
    s = re.sub(r"\[\[(?:[^|\]]+\|)?([^\]]+)\]\]", r"\1", s)
    # Drop simple templates like {{steady}} {{profit}} etc.
    s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    # Strip HTML tags (tooltips, refs)
    s = re.sub(r"<[^>]+>", "", s)
    # Common cleanup
    s = s.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", s).strip()


def _parse_net_worth_billion(s):
    """'$53.5 billion' → 53_500_000_000. Returns None on garbage."""
    if not s:
        return None
    cleaned = re.sub(r"[^\d.]", "", s)
    if not cleaned:
        return None
    try:
        billions = float(cleaned)
    except ValueError:
        return None
    return int(round(billions * 1_000_000_000))


def _parse_int(s):
    if not s:
        return None
    m = re.search(r"\d+", s)
    return int(m.group()) if m else None


def parse_year_wikitext(wikitext):
    """Extract (rank, name, net_worth_usd, age, citizenship, industry, notes)
    rows from one year's wikitable. Returns a list of dicts.

    The wikitables are inconsistent: some years use a single ranking table,
    others have multiple sections. We score each candidate table by how many
    of its rows look like billionaire entries (numeric rank + name + worth)
    and pick the top scorer."""
    if not wikitext:
        return []

    # Match each `{| class="...wikitable..."` table all the way to its `|}`.
    tables = re.findall(r"\{\|\s*class=\"[^\"]*wikitable[^\"]*\".*?\|\}",
                        wikitext, re.DOTALL)
    if not tables:
        return []

    # For each candidate table, parse rows the same way the main loop does
    # below and count how many produced a valid (rank, name, worth) tuple.
    # That's a much more reliable picker than "most |- separators".
    def _rows_from_table(table):
        raw_rows = re.split(r"\n\s*\|-", table)
        out = []
        for raw in raw_rows[1:]:
            row = _parse_row(raw)
            if row:
                out.append(row)
        return out

    best_rows = []
    for t in tables:
        candidate = _rows_from_table(t)
        if len(candidate) > len(best_rows):
            best_rows = candidate

    # Drop dupes by rank (vandalism sometimes inserts a fake #1 above the
    # real #1; we keep the first occurrence, which is usually the real one
    # since editors tend to insert vandalism above good data temporarily —
    # but still log conflicts).
    seen = set()
    cleaned = []
    for r in best_rows:
        if r["rank"] in seen:
            continue
        seen.add(r["rank"])
        cleaned.append(r)
    return cleaned


def _parse_row(raw):
    """Parse a single `|-` separated wikitable row into a structured dict.
    Returns None when the row doesn't look like a billionaire entry
    (header rows, malformed rows, footer rows)."""
    # Each cell starts with `|` or `||`. Wikipedia tables also use newlines
    # to separate cells in some years. We normalize both.
    body = raw.replace("||", "\n|")
    cells = []
    for line in body.split("\n"):
        line = line.strip()
        if not line.startswith("|"):
            continue
        cell = line.lstrip("|").strip()
        # `style="..."|value` → keep `value`. Only strip when there's a
        # KEY=VALUE prefix on the left of a pipe.
        attr_match = re.match(r"^([a-zA-Z][\w-]*\s*=\s*\"[^\"]*\")\s*\|\s*(.*)$", cell)
        if attr_match:
            cell = attr_match.group(2).strip()
        cells.append(_strip_wiki_markup(cell))
    if len(cells) < 3:
        return None
    rank = _parse_int(cells[0])
    if rank is None or rank < 1 or rank > 500:
        return None
    name = cells[1] if len(cells) > 1 else ""
    if not name or len(name) < 3:
        return None
    # Find the net-worth cell — first one that parses as a number > 0.5.
    # 2010-era tables sometimes drop the "$" and "billion" suffix, leaving
    # bare floats like "53.5"; later years use "$53.5 billion".
    worth = None
    for c in cells[2:]:
        candidate = _parse_net_worth_billion(c)
        if candidate and candidate >= 500_000_000:  # ≥ $0.5B
            worth = candidate
            break
        # Bare-number fallback: "53.5" treated as billions
        if c and re.match(r"^\d+(\.\d+)?$", c):
            n = float(c)
            if 0.5 <= n <= 1000:  # plausible billionaire range
                worth = int(round(n * 1_000_000_000))
                break
    if worth is None:
        return None

    # Strip "& family" / similar trailing notes from name
    notes = ""
    m = re.search(r"(.+?)\s+&?\s*(family|estate)$", name, re.IGNORECASE)
    if m:
        name = m.group(1).strip()
        notes = m.group(2).lower()

    age = None
    country = None
    industry = None
    for c in cells[2:]:
        if not age:
            a = _parse_int(c)
            if a and 18 <= a <= 110 and a != worth // 1_000_000_000:
                age = a
                continue
        if not country and any(
            kw in c for kw in ("United States", "China", "Russia", "India",
                                "France", "Germany", "Japan", "Mexico",
                                "Hong Kong", "Brazil", "Italy", "Spain",
                                "United Kingdom", "Canada", "Saudi Arabia",
                                "Sweden", "Switzerland", "Australia", "Norway")
        ):
            country = c
            continue
        if not industry and len(c) > 2 and not c.replace(".", "").isdigit():
            industry = c
    return {
        "rank": rank,
        "name": name,
        "net_worth_usd": worth,
        "age": age,
        "citizenship": country,
        "industry": industry,
        "notes": notes or None,
    }


def link_to_persons(rows):
    """Best-effort: match each parsed row's name to an existing person_id
    in the persons table (case-insensitive substring). Returns rows with
    `person_id` filled in where we found a match."""
    conn = get_db()
    persons = conn.execute(
        "SELECT person_id, common_name, full_name FROM persons"
    ).fetchall()
    conn.close()

    # Build lookup: lowercased common+full names → person_id
    by_name = {}
    for p in persons:
        for n in (p["common_name"], p["full_name"]):
            if n:
                by_name[n.lower().strip()] = p["person_id"]

    for r in rows:
        target = r["name"].lower().strip()
        # Exact first
        if target in by_name:
            r["person_id"] = by_name[target]
            continue
        # Substring fallback — only if the lookup name fully contains or is
        # contained by the target. Avoids matching "Steve" → any person.
        match = None
        for k, pid in by_name.items():
            if len(k) < 6:
                continue
            if k in target or target in k:
                if match and match != pid:
                    match = None
                    break
                match = pid
        if match:
            r["person_id"] = match
    return rows


def import_year(year, source="forbes_world", page_title=None):
    """Fetch one year's Wikipedia article, parse its wikitable, link to
    persons, and INSERT OR REPLACE into historical_rankings.
    Returns (rows_imported, rows_linked)."""
    title = page_title or WIKI_PAGE_TEMPLATE.format(year=year)
    wikitext = _fetch_wikitext(title)
    if not wikitext:
        logger.warning(f"No wikitext for {year} ({title})")
        return 0, 0
    rows = parse_year_wikitext(wikitext)
    if not rows:
        logger.warning(f"No rows parsed for {year}")
        return 0, 0
    rows = link_to_persons(rows)
    linked = sum(1 for r in rows if r.get("person_id"))

    conn = get_db()
    conn.executemany(
        """
        INSERT OR REPLACE INTO historical_rankings
            (source, year, rank, person_id, name, net_worth_usd,
             citizenship, age, industry, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (source, year, r["rank"], r.get("person_id"), r["name"],
             r["net_worth_usd"], r.get("citizenship"), r.get("age"),
             r.get("industry"), r.get("notes"))
            for r in rows
        ],
    )
    conn.commit()
    conn.close()
    return len(rows), linked


def backfill_all(start=DEFAULT_START_YEAR, end=DEFAULT_END_YEAR, source="forbes_world"):
    """Walk the year range, fetching + importing each year. Polite delay
    between fetches so Wikipedia doesn't 429 us. Returns a per-year report."""
    report = {}
    for year in range(start, end + 1):
        try:
            imported, linked = import_year(year, source=source)
            report[year] = {"imported": imported, "linked": linked}
            logger.info(f"Forbes {year}: {imported} rows, {linked} linked to Bloomberg")
        except Exception as e:
            logger.warning(f"Forbes {year}: {e}")
            report[year] = {"error": str(e)}
        time.sleep(FORBES_FETCH_DELAY_SEC)
    return report
