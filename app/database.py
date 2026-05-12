import sqlite3
from pathlib import Path

DB_PATH = Path("data/bloomberg.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS billionaires (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    scraped_at           DATETIME NOT NULL,
    person_id            INTEGER NOT NULL,
    rank                 INTEGER,
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
    sector               TEXT,
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
    schools_json         TEXT,
    facts_json           TEXT,
    milestones_json      TEXT,
    biography            TEXT,
    overview             TEXT,
    net_worth_summary    TEXT,
    slug                 TEXT,
    confidence           INTEGER
);

CREATE INDEX IF NOT EXISTS idx_billionaires_person_scraped ON billionaires(person_id, scraped_at);
CREATE INDEX IF NOT EXISTS idx_billionaires_scraped ON billionaires(scraped_at);

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

BILLIONAIRE_COLUMNS = [
    "scraped_at", "person_id", "rank", "common_name", "full_name",
    "first_name", "last_name", "middle_name", "citizenship", "age",
    "birth_year", "gender", "gender_confidence", "industry", "sector",
    "net_worth_usd", "last_change_usd", "last_change_pct", "ytd_change_usd",
    "ytd_change_pct", "public_assets_total", "private_assets_total",
    "cash_assets_total", "public_assets_json", "private_assets_json",
    "cash_asset_value", "liabilities_value", "liabilities_note",
    "schools_json", "facts_json", "milestones_json", "biography",
    "overview", "net_worth_summary", "slug", "confidence",
]


def get_db(db_path=None):
    path = str(db_path or DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path=None):
    path = str(db_path or DB_PATH)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.close()


def insert_billionaires(db_path, rows):
    conn = get_db(db_path)
    placeholders = ", ".join(["?"] * len(BILLIONAIRE_COLUMNS))
    cols = ", ".join(BILLIONAIRE_COLUMNS)
    sql = f"INSERT INTO billionaires ({cols}) VALUES ({placeholders})"
    values = [
        tuple(row.get(col) for col in BILLIONAIRE_COLUMNS)
        for row in rows
    ]
    conn.executemany(sql, values)
    conn.commit()
    conn.close()


def get_latest_snapshot(db_path=None):
    conn = get_db(db_path)
    cursor = conn.execute("""
        SELECT * FROM billionaires
        WHERE scraped_at = (SELECT MAX(scraped_at) FROM billionaires)
        ORDER BY rank
    """)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_snapshot_dates(db_path=None):
    conn = get_db(db_path)
    cursor = conn.execute("""
        SELECT DISTINCT DATE(scraped_at) as date
        FROM billionaires ORDER BY date DESC
    """)
    dates = [row[0] for row in cursor.fetchall()]
    conn.close()
    return dates


def get_dashboard_stats(db_path=None):
    conn = get_db(db_path)
    latest = conn.execute(
        "SELECT MAX(scraped_at) as latest FROM billionaires"
    ).fetchone()
    if not latest or not latest[0]:
        conn.close()
        return {"total_wealth": 0, "count": 0, "snapshots": 0, "latest_scrape": None}
    latest_at = latest[0]
    stats = conn.execute("""
        SELECT
            SUM(net_worth_usd) as total_wealth,
            COUNT(*) as count
        FROM billionaires WHERE scraped_at = ?
    """, (latest_at,)).fetchone()
    snapshot_count = conn.execute(
        "SELECT COUNT(DISTINCT DATE(scraped_at)) FROM billionaires"
    ).fetchone()[0]
    conn.close()
    return {
        "total_wealth": stats[0] or 0,
        "count": stats[1] or 0,
        "snapshots": snapshot_count,
        "latest_scrape": latest_at,
    }
