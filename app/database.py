"""SQLite layer for the Bloomberg billionaires app.

Two databases:
- bloomberg.db   — billionaire metadata, snapshots, wealth history, scraper state.
- network.db     — Wikidata-derived graph (persons_index, family_edges, entities).

Kept separate so the network DB can be downloaded standalone and rebuilt without
touching scrape data.
"""
import sqlite3
from pathlib import Path


# =============================================================================
# Paths & schemas
# =============================================================================

DB_PATH = Path("data/bloomberg.db")
NETWORK_DB_PATH = Path("data/network.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS persons (
    person_id            INTEGER PRIMARY KEY,
    common_name          TEXT,
    full_name            TEXT,
    first_name           TEXT,
    last_name            TEXT,
    middle_name          TEXT,
    citizenship          TEXT,
    age                  INTEGER,
    birth_year           INTEGER,
    gender               TEXT,
    gender_confidence    REAL,
    industry             TEXT,
    biography            TEXT,
    overview             TEXT,
    net_worth_summary    TEXT,
    schools_json         TEXT,
    facts_json           TEXT,
    milestones_json      TEXT,
    slug                 TEXT,
    confidence           INTEGER,
    updated_at           DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    scraped_at           DATETIME NOT NULL,
    person_id            INTEGER NOT NULL,
    rank                 INTEGER,
    net_worth_usd        INTEGER,
    last_change_usd      INTEGER,
    last_change_pct      REAL,
    ytd_change_usd       INTEGER,
    ytd_change_pct       REAL,
    public_assets_total  INTEGER,
    private_assets_total INTEGER,
    cash_assets_total    INTEGER,
    public_assets_json   TEXT,
    private_assets_json  TEXT,
    cash_asset_value     INTEGER,
    liabilities_value    INTEGER,
    liabilities_note     TEXT,
    FOREIGN KEY (person_id) REFERENCES persons(person_id)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_person_scraped ON snapshots(person_id, scraped_at);
CREATE INDEX IF NOT EXISTS idx_snapshots_scraped ON snapshots(scraped_at);

CREATE TABLE IF NOT EXISTS wealth_history (
    person_id     INTEGER NOT NULL,
    date          TEXT NOT NULL,
    net_worth_usd INTEGER NOT NULL,
    PRIMARY KEY (person_id, date),
    FOREIGN KEY (person_id) REFERENCES persons(person_id)
);

CREATE INDEX IF NOT EXISTS idx_wealth_history_date ON wealth_history(date);
CREATE INDEX IF NOT EXISTS idx_wealth_history_date_value ON wealth_history(date, net_worth_usd DESC);

CREATE TABLE IF NOT EXISTS history_backfilled (
    person_id     INTEGER PRIMARY KEY,
    backfilled_at DATETIME NOT NULL,
    FOREIGN KEY (person_id) REFERENCES persons(person_id)
);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   DATETIME NOT NULL,
    finished_at  DATETIME,
    status       TEXT NOT NULL,
    record_count INTEGER,
    duration_ms  INTEGER,
    error        TEXT
);

CREATE TABLE IF NOT EXISTS schedule_config (
    id       INTEGER PRIMARY KEY DEFAULT 1,
    times    TEXT NOT NULL DEFAULT '["08:00"]',
    timezone TEXT NOT NULL DEFAULT 'UTC',
    enabled  BOOLEAN NOT NULL DEFAULT 1
);

INSERT OR IGNORE INTO schedule_config (id) VALUES (1);
"""

NETWORK_SCHEMA = """
CREATE TABLE IF NOT EXISTS persons_index (
    person_id    INTEGER PRIMARY KEY,
    common_name  TEXT,
    wikidata_qid TEXT
);

CREATE INDEX IF NOT EXISTS idx_persons_index_qid ON persons_index(wikidata_qid);

CREATE TABLE IF NOT EXISTS family_edges (
    person_id    INTEGER NOT NULL,
    related_id   INTEGER NOT NULL,
    kind         TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT 'wikidata',
    PRIMARY KEY (person_id, related_id, kind)
);

CREATE INDEX IF NOT EXISTS idx_family_edges_related ON family_edges(related_id);

CREATE TABLE IF NOT EXISTS entities (
    entity_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    qid            TEXT NOT NULL UNIQUE,
    name           TEXT,
    kind           TEXT,
    description    TEXT,
    inception_year INTEGER,
    country        TEXT,
    industry       TEXT,
    website        TEXT,
    employee_count INTEGER,
    revenue_usd    INTEGER,
    wikipedia_url  TEXT
);

CREATE TABLE IF NOT EXISTS entity_links (
    person_id INTEGER NOT NULL,
    entity_id INTEGER NOT NULL,
    role      TEXT NOT NULL,
    source    TEXT NOT NULL DEFAULT 'wikidata',
    PRIMARY KEY (person_id, entity_id, role)
);

CREATE INDEX IF NOT EXISTS idx_entity_links_entity ON entity_links(entity_id);

CREATE TABLE IF NOT EXISTS entity_edges (
    entity_a_id INTEGER NOT NULL,
    entity_b_id INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'wikidata',
    PRIMARY KEY (entity_a_id, entity_b_id, kind),
    FOREIGN KEY (entity_a_id) REFERENCES entities(entity_id),
    FOREIGN KEY (entity_b_id) REFERENCES entities(entity_id)
);

CREATE INDEX IF NOT EXISTS idx_entity_edges_b ON entity_edges(entity_b_id);
"""

PERSON_COLUMNS = [
    "person_id", "common_name", "full_name", "first_name", "last_name",
    "middle_name", "citizenship", "age", "birth_year", "gender",
    "gender_confidence", "industry", "biography", "overview",
    "net_worth_summary", "schools_json", "facts_json", "milestones_json",
    "slug", "confidence", "updated_at",
]

SNAPSHOT_COLUMNS = [
    "scraped_at", "person_id", "rank", "net_worth_usd", "last_change_usd",
    "last_change_pct", "ytd_change_usd", "ytd_change_pct",
    "public_assets_total", "private_assets_total", "cash_assets_total",
    "public_assets_json", "private_assets_json", "cash_asset_value",
    "liabilities_value", "liabilities_note",
]


# =============================================================================
# Connections
# =============================================================================

def get_db(db_path=None):
    path = str(db_path or DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_network_db(db_path=None):
    path = str(db_path or NETWORK_DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


# =============================================================================
# Init & migrations
# =============================================================================

def init_db(db_path=None, network_db_path=None):
    path = str(db_path or DB_PATH)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    _migrate_legacy_table(conn)
    _seed_history_backfilled(conn)
    conn.close()

    npath = str(network_db_path or NETWORK_DB_PATH)
    Path(npath).parent.mkdir(parents=True, exist_ok=True)
    nconn = sqlite3.connect(npath)
    nconn.executescript(NETWORK_SCHEMA)
    _migrate_entities_columns(nconn)
    nconn.close()

    _migrate_network_data_out_of_main(path, npath)


def _migrate_entities_columns(conn):
    """Idempotent: ALTER TABLE entities for the metadata columns added later."""
    existing = {r[1] for r in conn.execute("PRAGMA table_info(entities)").fetchall()}
    additions = [
        ("description", "TEXT"),
        ("inception_year", "INTEGER"),
        ("country", "TEXT"),
        ("industry", "TEXT"),
        ("website", "TEXT"),
        ("employee_count", "INTEGER"),
        ("revenue_usd", "INTEGER"),
        ("wikipedia_url", "TEXT"),
    ]
    for col, sql_type in additions:
        if col not in existing:
            conn.execute(f"ALTER TABLE entities ADD COLUMN {col} {sql_type}")
    conn.commit()


def _seed_history_backfilled(conn):
    """One-time seed: any person who already has wealth_history rows is treated
    as already backfilled, so we don't re-fetch them. New persons (added in
    future scrapes) won't be in this table and will be picked up automatically."""
    conn.execute("""
        INSERT OR IGNORE INTO history_backfilled (person_id, backfilled_at)
        SELECT DISTINCT person_id, datetime('now') FROM wealth_history
    """)
    conn.commit()


def _migrate_legacy_table(conn):
    """Migrate data from old flat billionaires table to new schema if needed."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='billionaires'"
    )
    if not cursor.fetchone():
        return

    count = conn.execute("SELECT COUNT(*) FROM billionaires").fetchone()[0]
    if count == 0:
        return

    new_count = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    if new_count > 0:
        return

    conn.execute("""
        INSERT OR REPLACE INTO persons (
            person_id, common_name, full_name, first_name, last_name,
            middle_name, citizenship, age, birth_year, gender,
            gender_confidence, industry, biography, overview,
            net_worth_summary, schools_json, facts_json, milestones_json,
            slug, confidence, updated_at
        )
        SELECT
            person_id, common_name, full_name, first_name, last_name,
            middle_name, citizenship, age, birth_year, gender,
            gender_confidence, industry, biography, overview,
            net_worth_summary, schools_json, facts_json, milestones_json,
            slug, confidence, scraped_at
        FROM billionaires
        WHERE scraped_at = (SELECT MAX(scraped_at) FROM billionaires)
    """)

    conn.execute("""
        INSERT INTO snapshots (
            scraped_at, person_id, rank, net_worth_usd, last_change_usd,
            last_change_pct, ytd_change_usd, ytd_change_pct,
            public_assets_total, private_assets_total, cash_assets_total,
            public_assets_json, private_assets_json, cash_asset_value,
            liabilities_value, liabilities_note
        )
        SELECT
            scraped_at, person_id, rank, net_worth_usd, last_change_usd,
            last_change_pct, ytd_change_usd, ytd_change_pct,
            public_assets_total, private_assets_total, cash_assets_total,
            public_assets_json, private_assets_json, cash_asset_value,
            liabilities_value, liabilities_note
        FROM billionaires
    """)

    conn.commit()


def _migrate_network_data_out_of_main(main_path, network_path):
    """One-shot: if the legacy bloomberg.db still has wikidata_qid /
    family_edges / entities / entity_links, copy them into the network DB
    and drop them from the main DB. Idempotent."""
    main = sqlite3.connect(main_path)
    main.row_factory = sqlite3.Row
    try:
        main.execute("ATTACH DATABASE ? AS net", (network_path,))

        cols = [r[1] for r in main.execute("PRAGMA table_info(persons)").fetchall()]
        if "wikidata_qid" in cols:
            main.execute("""
                INSERT OR IGNORE INTO net.persons_index (person_id, common_name, wikidata_qid)
                SELECT person_id, common_name, wikidata_qid FROM persons
                WHERE wikidata_qid IS NOT NULL
            """)

        legacy_tables = ("family_edges", "entities", "entity_links")
        existing = {
            r[0] for r in main.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?, ?, ?)",
                legacy_tables,
            ).fetchall()
        }
        if "family_edges" in existing:
            main.execute("""
                INSERT OR IGNORE INTO net.family_edges (person_id, related_id, kind, source)
                SELECT person_id, related_id, kind, source FROM family_edges
            """)
        if "entities" in existing:
            main.execute("""
                INSERT OR IGNORE INTO net.entities (entity_id, qid, name, kind)
                SELECT entity_id, qid, name, kind FROM entities
            """)
        if "entity_links" in existing:
            main.execute("""
                INSERT OR IGNORE INTO net.entity_links (person_id, entity_id, role, source)
                SELECT person_id, entity_id, role, source FROM entity_links
            """)
        main.commit()

        for t in ("entity_links", "entities", "family_edges"):
            if t in existing:
                main.execute(f"DROP TABLE {t}")
        main.commit()
    finally:
        main.execute("DETACH DATABASE net")
        main.close()


# =============================================================================
# Persons & snapshots
# =============================================================================

def insert_scrape_data(db_path, rows):
    """Insert scraped data into persons and snapshots tables.

    Also appends a wealth_history row for each person on the snapshot date,
    so the daily series stays in sync without a separate backfill call.
    """
    conn = get_db(db_path)
    scraped_at = rows[0]["scraped_at"] if rows else None
    today = scraped_at.split("T")[0] if scraped_at else None

    person_placeholders = ", ".join(["?"] * len(PERSON_COLUMNS))
    person_cols = ", ".join(PERSON_COLUMNS)
    snapshot_placeholders = ", ".join(["?"] * len(SNAPSHOT_COLUMNS))
    snapshot_cols = ", ".join(SNAPSHOT_COLUMNS)

    for row in rows:
        conn.execute(
            f"INSERT OR REPLACE INTO persons ({person_cols}) VALUES ({person_placeholders})",
            tuple(row.get(col) for col in PERSON_COLUMNS),
        )
        conn.execute(
            f"INSERT INTO snapshots ({snapshot_cols}) VALUES ({snapshot_placeholders})",
            tuple(row.get(col) for col in SNAPSHOT_COLUMNS),
        )
        if today and row.get("net_worth_usd") is not None:
            conn.execute(
                "INSERT OR REPLACE INTO wealth_history (person_id, date, net_worth_usd) "
                "VALUES (?, ?, ?)",
                (row["person_id"], today, row["net_worth_usd"]),
            )

    conn.commit()
    conn.close()


def get_latest_snapshot(db_path=None):
    conn = get_db(db_path)
    cursor = conn.execute("""
        SELECT s.*, p.common_name, p.full_name, p.citizenship, p.age,
               p.birth_year, p.gender, p.gender_confidence, p.industry
        FROM snapshots s
        JOIN persons p ON s.person_id = p.person_id
        WHERE s.scraped_at = (SELECT MAX(scraped_at) FROM snapshots)
        ORDER BY s.rank
    """)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_snapshot_dates(db_path=None):
    conn = get_db(db_path)
    cursor = conn.execute("""
        SELECT DISTINCT DATE(scraped_at) as date
        FROM snapshots ORDER BY date DESC
    """)
    dates = [row[0] for row in cursor.fetchall()]
    conn.close()
    return dates


# =============================================================================
# Wealth history
# =============================================================================

def insert_wealth_history(db_path, person_id, stats):
    """Bulk upsert (date, net_worth_usd) pairs for a person. Returns rows written."""
    if not stats:
        return 0
    conn = get_db(db_path)
    conn.executemany(
        "INSERT OR REPLACE INTO wealth_history (person_id, date, net_worth_usd) VALUES (?, ?, ?)",
        [(person_id, d, w) for d, w in stats if d and w is not None],
    )
    conn.commit()
    written = conn.total_changes
    conn.close()
    return written


def get_wealth_history(person_id, db_path=None):
    conn = get_db(db_path)
    cursor = conn.execute(
        "SELECT date, net_worth_usd FROM wealth_history WHERE person_id = ? ORDER BY date",
        (person_id,),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_history_coverage(db_path=None):
    """Returns {persons: int, rows: int}."""
    conn = get_db(db_path)
    persons = conn.execute(
        "SELECT COUNT(DISTINCT person_id) FROM wealth_history"
    ).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM wealth_history").fetchone()[0]
    conn.close()
    return {"persons": persons, "rows": total}


def sync_history_from_snapshots(db_path=None):
    """Backfill wealth_history with any (person_id, date, net_worth) pairs that
    exist in snapshots but not yet in wealth_history. Returns rows added."""
    conn = get_db(db_path)
    before = conn.execute("SELECT COUNT(*) FROM wealth_history").fetchone()[0]
    conn.execute("""
        INSERT OR IGNORE INTO wealth_history (person_id, date, net_worth_usd)
        SELECT person_id, DATE(scraped_at), net_worth_usd
        FROM snapshots
        WHERE net_worth_usd IS NOT NULL
    """)
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM wealth_history").fetchone()[0]
    conn.close()
    return after - before


# =============================================================================
# Dashboard aggregates
# =============================================================================

def get_dashboard_stats(db_path=None):
    conn = get_db(db_path)
    latest = conn.execute(
        "SELECT MAX(scraped_at) as latest FROM snapshots"
    ).fetchone()
    if not latest or not latest[0]:
        conn.close()
        return {
            "total_wealth": 0, "count": 0, "snapshots": 0, "latest_scrape": None,
            "history_rows": 0, "history_persons": 0, "history_earliest": None,
            "country_leaderboard": [], "industry_leaderboard": [],
            "movement": None, "wealth_age": [], "concentration_trend": [],
        }
    latest_at = latest[0]
    stats = conn.execute("""
        SELECT
            SUM(net_worth_usd) as total_wealth,
            COUNT(*) as count
        FROM snapshots WHERE scraped_at = ?
    """, (latest_at,)).fetchone()
    snapshot_count = conn.execute(
        "SELECT COUNT(DISTINCT DATE(scraped_at)) FROM snapshots"
    ).fetchone()[0]
    history = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT person_id), MIN(date) FROM wealth_history"
    ).fetchone()

    country_leaderboard = [dict(r) for r in conn.execute("""
        SELECT p.citizenship AS country,
               SUM(s.net_worth_usd) AS total_wealth,
               COUNT(*) AS count
        FROM snapshots s JOIN persons p ON s.person_id = p.person_id
        WHERE s.scraped_at = ? AND p.citizenship IS NOT NULL AND p.citizenship != ''
        GROUP BY p.citizenship
        ORDER BY total_wealth DESC LIMIT 10
    """, (latest_at,)).fetchall()]

    industry_leaderboard = [dict(r) for r in conn.execute("""
        SELECT p.industry,
               SUM(s.net_worth_usd) AS total_wealth,
               COUNT(*) AS count
        FROM snapshots s JOIN persons p ON s.person_id = p.person_id
        WHERE s.scraped_at = ? AND p.industry IS NOT NULL AND p.industry != ''
        GROUP BY p.industry
        ORDER BY total_wealth DESC LIMIT 10
    """, (latest_at,)).fetchall()]

    movement = _compute_movement(conn, latest_at)

    wealth_age = [dict(r) for r in conn.execute("""
        SELECT p.common_name AS name, p.age, p.industry, p.citizenship,
               s.net_worth_usd
        FROM snapshots s JOIN persons p ON s.person_id = p.person_id
        WHERE s.scraped_at = ? AND p.age IS NOT NULL AND s.net_worth_usd IS NOT NULL
    """, (latest_at,)).fetchall()]

    # Snapshot-based concentration: one row per scraped date, picking the
    # latest scrape per person per day (multiple intra-day scrapes possible).
    concentration_trend = [dict(r) for r in conn.execute("""
        WITH per_day AS (
            SELECT DATE(scraped_at) AS d, person_id, net_worth_usd,
                   ROW_NUMBER() OVER (PARTITION BY DATE(scraped_at), person_id
                                      ORDER BY scraped_at DESC) AS rn_day
            FROM snapshots WHERE net_worth_usd IS NOT NULL
        ),
        ranked AS (
            SELECT d, net_worth_usd,
                   ROW_NUMBER() OVER (PARTITION BY d ORDER BY net_worth_usd DESC) AS rk
            FROM per_day WHERE rn_day = 1
        )
        SELECT d AS date,
               SUM(net_worth_usd) AS total,
               SUM(CASE WHEN rk = 1   THEN net_worth_usd ELSE 0 END) AS top_1,
               SUM(CASE WHEN rk <= 10 THEN net_worth_usd ELSE 0 END) AS top_10,
               SUM(CASE WHEN rk <= 100 THEN net_worth_usd ELSE 0 END) AS top_100,
               COUNT(*) AS count
        FROM ranked GROUP BY d HAVING count >= 100 ORDER BY d
    """).fetchall()]

    conn.close()
    return {
        "total_wealth": stats[0] or 0,
        "count": stats[1] or 0,
        "snapshots": snapshot_count,
        "latest_scrape": latest_at,
        "history_rows": history[0] or 0,
        "history_persons": history[1] or 0,
        "history_earliest": history[2],
        "country_leaderboard": country_leaderboard,
        "industry_leaderboard": industry_leaderboard,
        "movement": movement,
        "wealth_age": wealth_age,
        "concentration_trend": concentration_trend,
    }


def _compute_movement(conn, latest_at):
    """Persons in earliest vs latest snapshot — newcomers and dropouts."""
    first_at = conn.execute(
        "SELECT MIN(scraped_at) FROM snapshots"
    ).fetchone()[0]
    if not first_at or first_at == latest_at:
        return {"since": first_at, "until": latest_at,
                "newcomers_count": 0, "dropped_count": 0,
                "newcomers": [], "dropped": []}
    first_pids = {r[0] for r in conn.execute(
        "SELECT DISTINCT person_id FROM snapshots WHERE scraped_at = ?",
        (first_at,),
    ).fetchall()}
    latest_pids = {r[0] for r in conn.execute(
        "SELECT DISTINCT person_id FROM snapshots WHERE scraped_at = ?",
        (latest_at,),
    ).fetchall()}
    newcomer_ids = latest_pids - first_pids
    dropped_ids = first_pids - latest_pids

    def _examples(pids, at, order_dir):
        if not pids:
            return []
        placeholders = ",".join("?" * len(pids))
        rows = conn.execute(
            f"""SELECT p.person_id, p.common_name AS name, s.rank, p.slug
                FROM snapshots s JOIN persons p ON s.person_id = p.person_id
                WHERE s.scraped_at = ? AND s.person_id IN ({placeholders})
                ORDER BY s.rank {order_dir} LIMIT 5""",
            [at, *pids],
        ).fetchall()
        return [dict(r) for r in rows]

    return {
        "since": first_at, "until": latest_at,
        "newcomers_count": len(newcomer_ids),
        "dropped_count": len(dropped_ids),
        "newcomers": _examples(newcomer_ids, latest_at, "ASC"),
        "dropped": _examples(dropped_ids, first_at, "ASC"),
    }
