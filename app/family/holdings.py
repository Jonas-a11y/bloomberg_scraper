"""Holdings-as-bridges: connect billionaires through publicly-held tickers
and privately-held companies.

Both sources read from each person's most recent snapshot and write into the
same `entities` / `entity_links` tables Wikidata bridges live in, with
`source='bloomberg'` so they're never deleted by the Wikidata refresh path.

Synthetic QIDs are used to satisfy the UNIQUE constraint on entities.qid
without colliding with real Wikidata QIDs (which all start with 'Q'):
  - `T:<ticker>` for public holdings (e.g. T:MSFT US Equity)
  - `PRIV:<normalized name>` for private holdings (e.g. PRIV:hunt refining)
"""
import json
import logging
import re

from app.database import get_db, get_network_db

logger = logging.getLogger(__name__)


def _strip_ticker_suffix(ticker):
    """`MSFT US Equity` -> `MSFT`. Falls back to the full string."""
    parts = ticker.split(" ", 1)
    return parts[0] if parts else ticker


def _normalize_private_name(name):
    """Lowercase, collapse whitespace, strip punctuation. Used as the dedup
    key so 'Hunt Refining', 'hunt refining co.', etc. fold together
    well enough without an external matching service.

    Aggressive enough to collapse common suffixes ('Co.', 'Inc.', '&') —
    not perfect (e.g. 'NFL team X' will still split from 'X NFL') but good
    on this dataset where most names are clean."""
    s = name.lower()
    s = re.sub(r"[.,&'\"()]+", " ", s)
    s = re.sub(r"\b(co|inc|llc|corp|corporation|company|holdings?|group|ltd)\b", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# Bloomberg's private_assets_json uses these as catch-all category names rather
# than actual company names, so they bridge billionaires through "owns real
# estate" rather than a real shared business. Filter on the normalized form.
PRIVATE_GENERIC_BUCKETS = {
    "real estate", "art collection", "art", "personal assets",
    "cash and investments", "cash", "investments", "personal",
    "other investments", "private investments", "miscellaneous",
}


def _gather_public_bridges(min_holders, max_holders):
    """Return {qid: (display_name, kind, description, holders_set)} for public tickers."""
    main = get_db()
    rows = main.execute("""
        WITH latest AS (
            SELECT person_id, MAX(scraped_at) AS m FROM snapshots GROUP BY person_id
        )
        SELECT s.person_id, s.public_assets_json
        FROM snapshots s
        JOIN latest l ON s.person_id = l.person_id AND s.scraped_at = l.m
        WHERE s.public_assets_json IS NOT NULL AND s.public_assets_json != '[]'
    """).fetchall()
    main.close()

    holders_by_ticker = {}
    for row in rows:
        try:
            assets = json.loads(row["public_assets_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        for asset in assets:
            ticker = (asset.get("ticker") or "").strip()
            if not ticker:
                continue
            holders_by_ticker.setdefault(ticker, set()).add(row["person_id"])

    bridges = {}
    for ticker, holders in holders_by_ticker.items():
        if min_holders <= len(holders) <= max_holders:
            bridges[f"T:{ticker}"] = (
                _strip_ticker_suffix(ticker), "stock",
                f"{ticker} — held by {len(holders)} of the tracked billionaires",
                holders,
            )
    return bridges


def _gather_private_bridges(min_holders, max_holders):
    """Return {qid: (display_name, kind, description, holders_set)} for private cos.

    Display name is the first occurrence we saw (preserves capitalization);
    dedup happens on the normalized form."""
    main = get_db()
    rows = main.execute("""
        WITH latest AS (
            SELECT person_id, MAX(scraped_at) AS m FROM snapshots GROUP BY person_id
        )
        SELECT s.person_id, s.private_assets_json
        FROM snapshots s
        JOIN latest l ON s.person_id = l.person_id AND s.scraped_at = l.m
        WHERE s.private_assets_json IS NOT NULL AND s.private_assets_json != '[]'
    """).fetchall()
    main.close()

    holders_by_key = {}
    display_by_key = {}
    for row in rows:
        try:
            assets = json.loads(row["private_assets_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        for asset in assets:
            raw_name = (asset.get("name") or "").strip()
            if not raw_name:
                continue
            key = _normalize_private_name(raw_name)
            if not key or key in PRIVATE_GENERIC_BUCKETS:
                continue
            holders_by_key.setdefault(key, set()).add(row["person_id"])
            display_by_key.setdefault(key, raw_name)

    bridges = {}
    for key, holders in holders_by_key.items():
        if min_holders <= len(holders) <= max_holders:
            display = display_by_key[key]
            bridges[f"PRIV:{key}"] = (
                display, "private_company",
                f"{display} — privately co-owned by {len(holders)} tracked billionaires",
                holders,
            )
    return bridges


def refresh_holdings_bridges(state=None, min_holders=2, max_holders=50):
    """Replace prior bloomberg-sourced entity rows + links with the current set.

    Both public tickers and private companies are written. Filter caps match
    the Wikidata bridge logic: 1-holder isn't a bridge, and >max_holders
    creates noise rather than signal. 50 catches mega-caps like AAPL/MSFT
    while leaving truly universal holdings out.

    Returns (entities_added, links_added)."""
    public = _gather_public_bridges(min_holders, max_holders)
    private = _gather_private_bridges(min_holders, max_holders)
    bridges = {**public, **private}

    net = get_network_db()
    net.execute("DELETE FROM entity_links WHERE source = 'bloomberg'")
    net.execute(
        "DELETE FROM entities WHERE entity_id NOT IN "
        "(SELECT entity_id FROM entity_links "
        " UNION SELECT entity_a_id FROM entity_edges "
        " UNION SELECT entity_b_id FROM entity_edges)"
    )

    insert_sql = (
        "INSERT INTO entities (qid, name, kind, description) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(qid) DO UPDATE SET name = excluded.name, "
        "kind = excluded.kind, description = excluded.description"
    )
    for qid, (name, kind, description, _) in bridges.items():
        net.execute(insert_sql, (qid, name, kind, description))

    qid_to_eid = {
        r["qid"]: r["entity_id"]
        for r in net.execute(
            "SELECT entity_id, qid FROM entities "
            "WHERE qid LIKE 'T:%' OR qid LIKE 'PRIV:%'"
        ).fetchall()
    }

    links_added = 0
    for qid, (_, kind, _, holders) in bridges.items():
        eid = qid_to_eid.get(qid)
        if not eid:
            continue
        role = "holds" if kind == "stock" else "owns"
        for pid in holders:
            cur = net.execute(
                "INSERT OR IGNORE INTO entity_links "
                "(person_id, entity_id, role, source) VALUES (?, ?, ?, 'bloomberg')",
                (pid, eid, role),
            )
            links_added += cur.rowcount
    net.commit()
    net.close()

    public_count = sum(1 for q in bridges if q.startswith("T:"))
    private_count = sum(1 for q in bridges if q.startswith("PRIV:"))
    if state is not None:
        state["holdings_bridges_added"] = len(bridges)
        state["holdings_links_added"] = links_added
    logger.info(
        "Holdings bridges: %d tickers + %d private cos, %d links",
        public_count, private_count, links_added,
    )
    return len(bridges), links_added
