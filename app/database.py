import sqlite3
from pathlib import Path

DB_PATH = Path("data/bloomberg.db")

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


def get_db(db_path=None):
    path = str(db_path or DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path=None):
    path = str(db_path or DB_PATH)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    _migrate_legacy_table(conn)
    conn.close()


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


def insert_scrape_data(db_path, rows):
    """Insert scraped data into persons and snapshots tables."""
    conn = get_db(db_path)
    scraped_at = rows[0]["scraped_at"] if rows else None
    today = scraped_at.split("T")[0] if scraped_at else None

    for row in rows:
        person_vals = tuple(row.get(col) for col in PERSON_COLUMNS)
        placeholders = ", ".join(["?"] * len(PERSON_COLUMNS))
        cols = ", ".join(PERSON_COLUMNS)
        conn.execute(
            f"INSERT OR REPLACE INTO persons ({cols}) VALUES ({placeholders})",
            person_vals,
        )

        snapshot_vals = tuple(row.get(col) for col in SNAPSHOT_COLUMNS)
        placeholders = ", ".join(["?"] * len(SNAPSHOT_COLUMNS))
        cols = ", ".join(SNAPSHOT_COLUMNS)
        conn.execute(
            f"INSERT INTO snapshots ({cols}) VALUES ({placeholders})",
            snapshot_vals,
        )

        if today and row.get("net_worth_usd") is not None:
            conn.execute(
                "INSERT OR REPLACE INTO wealth_history (person_id, date, net_worth_usd) VALUES (?, ?, ?)",
                (row["person_id"], today, row["net_worth_usd"]),
            )

    conn.commit()
    conn.close()


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
    """Returns (persons_with_history, total_history_rows)."""
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


def get_dashboard_stats(db_path=None):
    conn = get_db(db_path)
    latest = conn.execute(
        "SELECT MAX(scraped_at) as latest FROM snapshots"
    ).fetchone()
    if not latest or not latest[0]:
        conn.close()
        return {"total_wealth": 0, "count": 0, "snapshots": 0, "latest_scrape": None,
                "history_rows": 0, "history_persons": 0, "history_earliest": None}
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
    conn.close()
    return {
        "total_wealth": stats[0] or 0,
        "count": stats[1] or 0,
        "snapshots": snapshot_count,
        "latest_scrape": latest_at,
        "history_rows": history[0] or 0,
        "history_persons": history[1] or 0,
        "history_earliest": history[2],
    }
