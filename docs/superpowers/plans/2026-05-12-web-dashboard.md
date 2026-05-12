# Bloomberg Scraper Web Dashboard — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a web dashboard for automated Bloomberg billionaires scraping with scheduling, data exploration, and historical analysis.

**Architecture:** FastAPI serves a JSON API + static frontend. APScheduler runs scrapes on a configurable schedule. SQLite stores all data. Alpine.js + Chart.js provide lightweight interactivity and charts in the browser.

**Tech Stack:** FastAPI, uvicorn, APScheduler, curl-cffi, SQLite, Alpine.js, Chart.js

---

## File Structure

```
bloomberg_scraper/
├── app/
│   ├── __init__.py        # Empty
│   ├── main.py            # FastAPI app, lifespan, route includes, static mount
│   ├── database.py        # SQLite connection, schema creation, query helpers
│   ├── scraper.py         # Scrape logic as callable function (refactored from scrape_bloomberg.py)
│   ├── scheduler.py       # APScheduler lifecycle, schedule CRUD
│   ├── models.py          # Pydantic models for API responses
│   └── routes/
│       ├── __init__.py    # Empty
│       ├── dashboard.py   # GET /api/dashboard
│       ├── billionaires.py# GET /api/billionaires, /api/billionaires/{id}/history, /api/search
│       ├── analytics.py   # GET /api/analytics/*, /api/snapshots/*
│       ├── scraper_api.py # GET/POST /api/scraper/*
│       └── export.py      # GET /api/export, /api/export/db
├── static/
│   ├── index.html         # Single-page app shell
│   ├── app.js             # Alpine.js stores + Chart.js
│   └── style.css          # Layout + theme
├── tests/
│   ├── __init__.py
│   ├── test_database.py
│   ├── test_scraper.py
│   └── test_api.py
├── data/                   # SQLite DB lives here
├── requirements.txt
└── scrape_bloomberg.py    # Kept as standalone CLI tool
```

---

### Task 1: Project Setup & Dependencies

**Files:**
- Create: `requirements.txt`
- Create: `app/__init__.py`
- Create: `app/routes/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Create requirements.txt**

```
fastapi>=0.104.0
uvicorn[standard]>=0.24.0
apscheduler>=3.10.0
curl-cffi>=0.5.0
pandas>=2.0.0
pytest>=7.0.0
httpx>=0.25.0
```

- [ ] **Step 2: Create package init files**

Create empty `app/__init__.py`, `app/routes/__init__.py`, `tests/__init__.py`.

- [ ] **Step 3: Install dependencies**

Run: `pip install -r requirements.txt`
Expected: All packages install successfully

- [ ] **Step 4: Commit**

```bash
git add requirements.txt app/__init__.py app/routes/__init__.py tests/__init__.py
git commit -m "feat: project setup with dependencies"
```

---

### Task 2: Database Layer

**Files:**
- Create: `app/database.py`
- Create: `tests/test_database.py`

- [ ] **Step 1: Write the failing test for schema creation**

```python
# tests/test_database.py
import os
import tempfile

from app.database import get_db, init_db, insert_billionaires, get_latest_snapshot


def test_init_db_creates_tables():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.db")
        init_db(db_path)
        db = get_db(db_path)
        cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cursor.fetchall()]
        db.close()
        assert "billionaires" in tables
        assert "scrape_runs" in tables
        assert "schedule_config" in tables


def test_insert_and_query_billionaires():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.db")
        init_db(db_path)
        rows = [
            {
                "scraped_at": "2026-05-12T08:00:00",
                "person_id": 1,
                "rank": 1,
                "common_name": "Test Person",
                "full_name": "Test A Person",
                "first_name": "TEST",
                "last_name": "PERSON",
                "middle_name": "A",
                "citizenship": "United States",
                "age": 50,
                "birth_year": 1976,
                "gender": "male",
                "gender_confidence": 0.95,
                "industry": "Technology",
                "sector": "Technology",
                "net_worth_usd": 100000000000,
                "last_change_usd": 1000000000,
                "last_change_pct": 1.0,
                "ytd_change_usd": 5000000000,
                "ytd_change_pct": 5.0,
                "public_assets_total": 80000000000,
                "private_assets_total": 20000000000,
                "cash_assets_total": 0,
                "public_assets_json": '[{"ticker":"TEST","value":80000000000}]',
                "private_assets_json": None,
                "cash_asset_value": None,
                "liabilities_value": None,
                "liabilities_note": None,
                "schools_json": None,
                "facts_json": None,
                "milestones_json": None,
                "biography": "A test person.",
                "overview": "Test overview.",
                "net_worth_summary": "Test summary.",
                "slug": "test-person",
                "confidence": 3,
            }
        ]
        insert_billionaires(db_path, rows)
        result = get_latest_snapshot(db_path)
        assert len(result) == 1
        assert result[0]["common_name"] == "Test Person"
        assert result[0]["net_worth_usd"] == 100000000000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_database.py -v`
Expected: FAIL with ModuleNotFoundError (app.database not found)

- [ ] **Step 3: Implement database.py**

```python
# app/database.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_database.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/database.py tests/test_database.py
git commit -m "feat: database layer with schema, insert, and query helpers"
```

---

### Task 3: Scraper Module (Refactor)

**Files:**
- Create: `app/scraper.py`
- Create: `tests/test_scraper.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scraper.py
from app.scraper import scrape_billionaires, extract_birth_year, infer_gender, flatten_person


def test_extract_birth_year_from_milestones():
    person = {"milestones": [{"year": 1971, "event": "Born in Pretoria."}], "age": 54}
    assert extract_birth_year(person) == 1971


def test_extract_birth_year_from_age():
    person = {"milestones": [{"year": 2000, "event": "Founded company."}], "age": 50}
    from datetime import datetime
    assert extract_birth_year(person) == datetime.now().year - 50


def test_infer_gender_male():
    person = {"biography": "He founded the company. His vision was clear.", "overview": "", "netWorthSummary": ""}
    gender, conf = infer_gender(person)
    assert gender == "male"
    assert conf > 0.8


def test_infer_gender_female():
    person = {"biography": "She is the daughter of the founder. Her leadership transformed the company.", "overview": "", "netWorthSummary": ""}
    gender, conf = infer_gender(person)
    assert gender == "female"
    assert conf > 0.6


def test_flatten_person():
    person = {
        "personId": 1, "rank": 1, "fullName": "Test Person", "commonName": "Test",
        "firstName": "TEST", "lastName": "PERSON", "middleName": None,
        "citizenship": "US", "age": 50, "industry": "Tech", "sector": "Tech",
        "worth": 100, "lastChange": 10, "lastPercentChange": 1.0,
        "ytdChange": 50, "ytdPercentChange": 5.0,
        "fWorth": "$100", "fLastChange": "+$10", "fYtdChange": "+$50",
        "fLastPercentChange": "+1%", "fYtdPercentChange": "+5%",
        "publicAssetsTotal": 80, "privateAssetsTotal": 20, "cashAssetsTotal": 0,
        "publicAssets": [], "privateAssets": [], "cashAssets": [], "miscLiabilities": [],
        "schools": [], "intelligence": [], "milestones": [],
        "biography": "He is a businessman.", "overview": "", "netWorthSummary": "",
        "slug": "test", "confidence": 3,
    }
    row = flatten_person(person)
    assert row["person_id"] == 1
    assert row["rank"] == 1
    assert row["net_worth_usd"] == 100
    assert row["gender"] == "male"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scraper.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Implement scraper.py**

```python
# app/scraper.py
import json
import re
from datetime import datetime

from curl_cffi import requests

URL = "https://www.bloomberg.com/billionaires/"

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

MALE_PATTERNS = [re.compile(p) for p in [
    r"\bhe\b", r"\bhis\b", r"\bhim\b", r"\bhimself\b",
    r"\bson\b", r"\bfather\b", r"\bbusinessman\b", r"\bchairman\b",
]]
FEMALE_PATTERNS = [re.compile(p) for p in [
    r"\bshe\b", r"\bher\b", r"\bherself\b",
    r"\bdaughter\b", r"\bmother\b", r"\bbusinesswoman\b",
    r"\bchairwoman\b", r"\bgranddaughter\b",
]]


def extract_birth_year(person):
    milestones = person.get("milestones", [])
    if milestones:
        first = milestones[0]
        event = first.get("event", "").lower()
        if "born" in event or "birth" in event:
            return first.get("year")
    age = person.get("age")
    if age:
        return datetime.now().year - age
    return None


def infer_gender(person):
    text = " ".join([
        person.get("biography", ""),
        person.get("overview", ""),
        person.get("netWorthSummary", ""),
    ]).lower()
    male_count = sum(len(p.findall(text)) for p in MALE_PATTERNS)
    female_count = sum(len(p.findall(text)) for p in FEMALE_PATTERNS)
    total = male_count + female_count
    if total == 0:
        return "unknown", 0.0
    if male_count > female_count:
        return "male", round(male_count / total, 2)
    elif female_count > male_count:
        return "female", round(female_count / total, 2)
    return "unknown", 0.0


def flatten_person(person):
    row = {}
    row["person_id"] = person.get("personId")
    row["rank"] = person.get("rank")
    row["full_name"] = person.get("fullName")
    row["common_name"] = person.get("commonName")
    row["first_name"] = person.get("firstName")
    row["last_name"] = person.get("lastName")
    row["middle_name"] = person.get("middleName")
    row["citizenship"] = person.get("citizenship")
    row["age"] = person.get("age")
    row["birth_year"] = extract_birth_year(person)
    gender, gender_confidence = infer_gender(person)
    row["gender"] = gender
    row["gender_confidence"] = gender_confidence
    row["industry"] = person.get("industry")
    row["sector"] = person.get("sector")
    row["net_worth_usd"] = person.get("worth")
    row["last_change_usd"] = person.get("lastChange")
    row["last_change_pct"] = person.get("lastPercentChange")
    row["ytd_change_usd"] = person.get("ytdChange")
    row["ytd_change_pct"] = person.get("ytdPercentChange")
    row["public_assets_total"] = person.get("publicAssetsTotal")
    row["private_assets_total"] = person.get("privateAssetsTotal")
    row["cash_assets_total"] = person.get("cashAssetsTotal")
    public_assets = person.get("publicAssets", [])
    row["public_assets_json"] = json.dumps(
        [{"ticker": a.get("ticker"), "value": a.get("value")} for a in public_assets]
    ) if public_assets else None
    private_assets = person.get("privateAssets", [])
    row["private_assets_json"] = json.dumps(
        [{"name": a.get("name"), "value": a.get("value")} for a in private_assets]
    ) if private_assets else None
    cash_assets = person.get("cashAssets", [])
    row["cash_asset_value"] = cash_assets[0].get("value") if cash_assets else None
    liabilities = person.get("miscLiabilities", [])
    if liabilities:
        row["liabilities_value"] = liabilities[0].get("value")
        row["liabilities_note"] = liabilities[0].get("note", "").strip()
    else:
        row["liabilities_value"] = None
        row["liabilities_note"] = None
    schools = person.get("schools", [])
    row["schools_json"] = json.dumps(
        [s.get("school") for s in schools]
    ) if schools else None
    intelligence = person.get("intelligence", [])
    row["facts_json"] = json.dumps(intelligence) if intelligence else None
    milestones = person.get("milestones", [])
    row["milestones_json"] = json.dumps(milestones) if milestones else None
    row["biography"] = person.get("biography")
    row["overview"] = person.get("overview")
    row["net_worth_summary"] = person.get("netWorthSummary")
    row["slug"] = person.get("slug")
    row["confidence"] = person.get("confidence")
    return row


def scrape_billionaires():
    """Fetch and parse Bloomberg billionaires data. Returns list of dicts."""
    response = requests.get(URL, impersonate="chrome", headers=HEADERS)
    if response.status_code != 200:
        raise RuntimeError(f"Request failed: {response.status_code}")
    if "robot" in response.text[:1000].lower():
        raise RuntimeError("Blocked by bot detection")
    match = re.search(
        r"window\.top500\s*=\s*(\[.*?\])\s*;?\s*(?:</script>|window\.)",
        response.text,
        re.DOTALL,
    )
    if not match:
        raise RuntimeError("Could not find window.top500 data in page")
    data = json.loads(match.group(1))
    if len(data) < 400:
        raise RuntimeError(f"Partial data: only {len(data)} records")
    scraped_at = datetime.now().isoformat()
    rows = []
    for person in data:
        row = flatten_person(person)
        row["scraped_at"] = scraped_at
        rows.append(row)
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scraper.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/scraper.py tests/test_scraper.py
git commit -m "feat: scraper module refactored as importable function"
```

---

### Task 4: Pydantic Models

**Files:**
- Create: `app/models.py`

- [ ] **Step 1: Create models**

```python
# app/models.py
from pydantic import BaseModel


class DashboardStats(BaseModel):
    total_wealth: int
    count: int
    snapshots: int
    latest_scrape: str | None


class BillionaireRow(BaseModel):
    person_id: int
    rank: int
    common_name: str | None
    full_name: str | None
    citizenship: str | None
    age: int | None
    birth_year: int | None
    gender: str | None
    gender_confidence: float | None
    industry: str | None
    sector: str | None
    net_worth_usd: int | None
    last_change_usd: int | None
    last_change_pct: float | None
    ytd_change_usd: int | None
    ytd_change_pct: float | None


class BillionaireList(BaseModel):
    data: list[BillionaireRow]
    total: int
    page: int
    pages: int


class ScraperStatus(BaseModel):
    status: str
    next_run: str | None
    last_success: str | None


class ScrapeRun(BaseModel):
    id: int
    started_at: str
    finished_at: str | None
    status: str
    record_count: int | None
    duration_ms: int | None
    error: str | None


class ScheduleConfig(BaseModel):
    times: list[str]
    timezone: str
    enabled: bool


class ScheduleUpdate(BaseModel):
    times: list[str]
    timezone: str
    enabled: bool
```

- [ ] **Step 2: Commit**

```bash
git add app/models.py
git commit -m "feat: pydantic models for API request/response"
```

---

### Task 5: FastAPI App + Dashboard & Billionaires Routes

**Files:**
- Create: `app/main.py`
- Create: `app/routes/dashboard.py`
- Create: `app/routes/billionaires.py`
- Create: `tests/test_api.py`

- [ ] **Step 1: Write failing test for dashboard endpoint**

```python
# tests/test_api.py
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from app.database import init_db, insert_billionaires


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("app.database.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("app.routes.dashboard.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("app.routes.billionaires.DB_PATH", tmp_path / "test.db")
    init_db(db_path)
    from app.main import app
    return TestClient(app)


@pytest.fixture
def seeded_client(client, tmp_path):
    db_path = str(tmp_path / "test.db")
    rows = [
        {
            "scraped_at": "2026-05-12T08:00:00",
            "person_id": i,
            "rank": i,
            "common_name": f"Person {i}",
            "full_name": f"Person {i} Full",
            "first_name": "FIRST",
            "last_name": f"LAST{i}",
            "middle_name": None,
            "citizenship": "United States" if i % 2 == 0 else "France",
            "age": 40 + i,
            "birth_year": 1986 - i,
            "gender": "male" if i % 3 != 0 else "female",
            "gender_confidence": 0.9,
            "industry": "Technology" if i <= 3 else "Finance",
            "sector": "Technology" if i <= 3 else "Finance",
            "net_worth_usd": (10 - i) * 100000000000,
            "last_change_usd": 1000000000,
            "last_change_pct": 1.0,
            "ytd_change_usd": 5000000000,
            "ytd_change_pct": 5.0,
            "public_assets_total": 50000000000,
            "private_assets_total": 30000000000,
            "cash_assets_total": 0,
            "public_assets_json": None,
            "private_assets_json": None,
            "cash_asset_value": None,
            "liabilities_value": None,
            "liabilities_note": None,
            "schools_json": None,
            "facts_json": None,
            "milestones_json": None,
            "biography": "A test person.",
            "overview": "Overview.",
            "net_worth_summary": "Summary.",
            "slug": f"person-{i}",
            "confidence": 3,
        }
        for i in range(1, 6)
    ]
    insert_billionaires(db_path, rows)
    return client


def test_dashboard_stats(seeded_client):
    resp = seeded_client.get("/api/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 5
    assert data["snapshots"] == 1
    assert data["total_wealth"] > 0


def test_billionaires_list(seeded_client):
    resp = seeded_client.get("/api/billionaires")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert len(data["data"]) == 5
    assert data["data"][0]["rank"] == 1


def test_billionaires_filter_country(seeded_client):
    resp = seeded_client.get("/api/billionaires?country=France")
    assert resp.status_code == 200
    data = resp.json()
    assert all(b["citizenship"] == "France" for b in data["data"])


def test_search(seeded_client):
    resp = seeded_client.get("/api/search?q=Person 3")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert "Person 3" in data[0]["common_name"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api.py -v`
Expected: FAIL

- [ ] **Step 3: Implement main.py**

```python
# app/main.py
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import init_db, DB_PATH
from app.routes import dashboard, billionaires, analytics, scraper_api, export


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Bloomberg Scraper", lifespan=lifespan)

app.include_router(dashboard.router, prefix="/api")
app.include_router(billionaires.router, prefix="/api")
app.include_router(analytics.router, prefix="/api")
app.include_router(scraper_api.router, prefix="/api")
app.include_router(export.router, prefix="/api")

static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
```

- [ ] **Step 4: Implement dashboard route**

```python
# app/routes/dashboard.py
from fastapi import APIRouter

from app.database import get_dashboard_stats, DB_PATH

router = APIRouter()


@router.get("/dashboard")
def dashboard():
    return get_dashboard_stats()
```

- [ ] **Step 5: Implement billionaires route**

```python
# app/routes/billionaires.py
import math

from fastapi import APIRouter, Query

from app.database import get_db, DB_PATH

router = APIRouter()


@router.get("/billionaires")
def list_billionaires(
    country: str | None = None,
    industry: str | None = None,
    gender: str | None = None,
    snapshot: str | None = None,
    sort: str = "rank",
    page: int = 1,
    q: str | None = None,
):
    conn = get_db()
    conditions = []
    params = []

    if snapshot:
        conditions.append("DATE(scraped_at) = ?")
        params.append(snapshot)
    else:
        conditions.append("scraped_at = (SELECT MAX(scraped_at) FROM billionaires)")

    if country:
        conditions.append("citizenship = ?")
        params.append(country)
    if industry:
        conditions.append("industry = ?")
        params.append(industry)
    if gender:
        conditions.append("gender = ?")
        params.append(gender)
    if q:
        conditions.append("common_name LIKE ?")
        params.append(f"%{q}%")

    where = " AND ".join(conditions)
    allowed_sorts = {"rank", "net_worth_usd", "last_change_usd", "ytd_change_usd", "age", "common_name"}
    sort_col = sort.lstrip("-")
    if sort_col not in allowed_sorts:
        sort_col = "rank"
    sort_dir = "DESC" if sort.startswith("-") else "ASC"

    count_sql = f"SELECT COUNT(*) FROM billionaires WHERE {where}"
    total = conn.execute(count_sql, params).fetchone()[0]

    per_page = 50
    pages = max(1, math.ceil(total / per_page))
    offset = (page - 1) * per_page

    data_sql = f"""
        SELECT person_id, rank, common_name, full_name, citizenship, age,
               birth_year, gender, gender_confidence, industry, sector,
               net_worth_usd, last_change_usd, last_change_pct,
               ytd_change_usd, ytd_change_pct
        FROM billionaires WHERE {where}
        ORDER BY {sort_col} {sort_dir}
        LIMIT ? OFFSET ?
    """
    cursor = conn.execute(data_sql, params + [per_page, offset])
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return {"data": rows, "total": total, "page": page, "pages": pages}


@router.get("/billionaires/{person_id}/history")
def person_history(person_id: int):
    conn = get_db()
    cursor = conn.execute("""
        SELECT scraped_at, rank, net_worth_usd, last_change_usd, ytd_change_usd
        FROM billionaires WHERE person_id = ?
        ORDER BY scraped_at
    """, (person_id,))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


@router.get("/search")
def search(q: str = Query(..., min_length=1)):
    conn = get_db()
    cursor = conn.execute("""
        SELECT DISTINCT person_id, common_name, net_worth_usd, rank
        FROM billionaires
        WHERE common_name LIKE ? AND scraped_at = (SELECT MAX(scraped_at) FROM billionaires)
        ORDER BY rank LIMIT 10
    """, (f"%{q}%",))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows
```

- [ ] **Step 6: Create stub route files (analytics, scraper_api, export)**

```python
# app/routes/analytics.py
from fastapi import APIRouter
router = APIRouter()
```

```python
# app/routes/scraper_api.py
from fastapi import APIRouter
router = APIRouter()
```

```python
# app/routes/export.py
from fastapi import APIRouter
router = APIRouter()
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_api.py -v`
Expected: All tests PASS

- [ ] **Step 8: Commit**

```bash
git add app/main.py app/routes/dashboard.py app/routes/billionaires.py app/routes/analytics.py app/routes/scraper_api.py app/routes/export.py tests/test_api.py
git commit -m "feat: FastAPI app with dashboard and billionaires API"
```

---

### Task 6: Analytics & Snapshots Routes

**Files:**
- Modify: `app/routes/analytics.py`

- [ ] **Step 1: Add tests for analytics endpoints**

Append to `tests/test_api.py`:

```python
def test_analytics_by_industry(seeded_client):
    resp = seeded_client.get("/api/analytics/by-industry")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) > 0
    assert "industry" in data[0]
    assert "total_wealth" in data[0]


def test_analytics_by_country(seeded_client):
    resp = seeded_client.get("/api/analytics/by-country")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) > 0


def test_analytics_demographics(seeded_client):
    resp = seeded_client.get("/api/analytics/demographics")
    assert resp.status_code == 200
    data = resp.json()
    assert "gender" in data
    assert "age_distribution" in data


def test_snapshots_list(seeded_client):
    resp = seeded_client.get("/api/snapshots")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1


def test_snapshots_compare(seeded_client):
    resp = seeded_client.get("/api/snapshots/compare?from_date=2026-05-12&to_date=2026-05-12")
    assert resp.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api.py::test_analytics_by_industry -v`
Expected: FAIL (404 — route not defined)

- [ ] **Step 3: Implement analytics routes**

```python
# app/routes/analytics.py
from fastapi import APIRouter, Query

from app.database import get_db, get_snapshot_dates

router = APIRouter()


@router.get("/analytics/by-industry")
def by_industry():
    conn = get_db()
    cursor = conn.execute("""
        SELECT industry, SUM(net_worth_usd) as total_wealth, COUNT(*) as count
        FROM billionaires
        WHERE scraped_at = (SELECT MAX(scraped_at) FROM billionaires)
        GROUP BY industry ORDER BY total_wealth DESC
    """)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


@router.get("/analytics/by-country")
def by_country():
    conn = get_db()
    cursor = conn.execute("""
        SELECT citizenship as country, SUM(net_worth_usd) as total_wealth, COUNT(*) as count
        FROM billionaires
        WHERE scraped_at = (SELECT MAX(scraped_at) FROM billionaires)
        GROUP BY citizenship ORDER BY total_wealth DESC
        LIMIT 20
    """)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


@router.get("/analytics/demographics")
def demographics():
    conn = get_db()
    gender_cursor = conn.execute("""
        SELECT gender, COUNT(*) as count
        FROM billionaires
        WHERE scraped_at = (SELECT MAX(scraped_at) FROM billionaires)
        GROUP BY gender
    """)
    gender = [dict(row) for row in gender_cursor.fetchall()]

    age_cursor = conn.execute("""
        SELECT
            CASE
                WHEN age < 40 THEN '30-39'
                WHEN age < 50 THEN '40-49'
                WHEN age < 60 THEN '50-59'
                WHEN age < 70 THEN '60-69'
                WHEN age < 80 THEN '70-79'
                WHEN age < 90 THEN '80-89'
                ELSE '90+'
            END as bracket,
            COUNT(*) as count
        FROM billionaires
        WHERE scraped_at = (SELECT MAX(scraped_at) FROM billionaires)
        GROUP BY bracket ORDER BY bracket
    """)
    age_distribution = [dict(row) for row in age_cursor.fetchall()]
    conn.close()
    return {"gender": gender, "age_distribution": age_distribution}


@router.get("/snapshots")
def snapshots():
    return get_snapshot_dates()


@router.get("/snapshots/compare")
def compare_snapshots(from_date: str = Query(...), to_date: str = Query(...)):
    conn = get_db()
    from_cursor = conn.execute("""
        SELECT person_id, common_name, rank, net_worth_usd
        FROM billionaires WHERE DATE(scraped_at) = ?
    """, (from_date,))
    from_data = {row[0]: dict(row) for row in from_cursor.fetchall()}

    to_cursor = conn.execute("""
        SELECT person_id, common_name, rank, net_worth_usd
        FROM billionaires WHERE DATE(scraped_at) = ?
    """, (to_date,))
    to_data = {row[0]: dict(row) for row in to_cursor.fetchall()}
    conn.close()

    changes = []
    for pid, to_row in to_data.items():
        if pid in from_data:
            rank_change = from_data[pid]["rank"] - to_row["rank"]
            wealth_change = to_row["net_worth_usd"] - from_data[pid]["net_worth_usd"]
            if rank_change != 0 or wealth_change != 0:
                changes.append({
                    "person_id": pid,
                    "common_name": to_row["common_name"],
                    "rank_change": rank_change,
                    "wealth_change": wealth_change,
                    "new_rank": to_row["rank"],
                })

    changes.sort(key=lambda x: abs(x["wealth_change"]), reverse=True)

    new_entries = [dict(to_data[pid]) for pid in to_data if pid not in from_data]
    dropped = [dict(from_data[pid]) for pid in from_data if pid not in to_data]

    return {"changes": changes[:20], "new_entries": new_entries, "dropped": dropped}
```

- [ ] **Step 4: Add monkeypatch for analytics route in test fixture**

Update the `client` fixture in `tests/test_api.py`:

```python
@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("app.database.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("app.routes.dashboard.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("app.routes.billionaires.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("app.routes.analytics.get_db", lambda db_path=None: __import__("app.database", fromlist=["get_db"]).get_db(str(tmp_path / "test.db")))
    init_db(db_path)
    from app.main import app
    return TestClient(app)
```

Actually, a cleaner approach — make `get_db` use the module-level `DB_PATH` and just monkeypatch that once:

Update `tests/test_api.py` fixture to:

```python
@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.database
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(app.database, "DB_PATH", db_path)
    init_db(str(db_path))
    from app.main import app
    return TestClient(app)
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_api.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add app/routes/analytics.py tests/test_api.py
git commit -m "feat: analytics and snapshot comparison endpoints"
```

---

### Task 7: Scraper API & Scheduler Routes

**Files:**
- Modify: `app/routes/scraper_api.py`
- Create: `app/scheduler.py`

- [ ] **Step 1: Implement scheduler.py**

```python
# app/scheduler.py
import json
import logging
import time
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from app.database import get_db, insert_billionaires
from app.scraper import scrape_billionaires

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()
_is_running = False


def get_schedule_config():
    conn = get_db()
    row = conn.execute("SELECT times, timezone, enabled FROM schedule_config WHERE id = 1").fetchone()
    conn.close()
    if row:
        return {"times": json.loads(row[0]), "timezone": row[1], "enabled": bool(row[2])}
    return {"times": ["08:00"], "timezone": "UTC", "enabled": True}


def save_schedule_config(times, timezone, enabled):
    conn = get_db()
    conn.execute(
        "UPDATE schedule_config SET times = ?, timezone = ?, enabled = ? WHERE id = 1",
        (json.dumps(times), timezone, 1 if enabled else 0),
    )
    conn.commit()
    conn.close()


def run_scrape():
    global _is_running
    if _is_running:
        return
    _is_running = True
    conn = get_db()
    conn.execute(
        "INSERT INTO scrape_runs (started_at, status) VALUES (?, 'running')",
        (datetime.now().isoformat(),),
    )
    conn.commit()
    run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    start = time.time()
    try:
        rows = scrape_billionaires()
        insert_billionaires(None, rows)
        duration_ms = int((time.time() - start) * 1000)
        conn = get_db()
        conn.execute(
            "UPDATE scrape_runs SET finished_at = ?, status = 'success', record_count = ?, duration_ms = ? WHERE id = ?",
            (datetime.now().isoformat(), len(rows), duration_ms, run_id),
        )
        conn.commit()
        conn.close()
        logger.info(f"Scrape complete: {len(rows)} records in {duration_ms}ms")
    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        conn = get_db()
        conn.execute(
            "UPDATE scrape_runs SET finished_at = ?, status = 'failed', duration_ms = ?, error = ? WHERE id = ?",
            (datetime.now().isoformat(), duration_ms, str(e), run_id),
        )
        conn.commit()
        conn.close()
        logger.error(f"Scrape failed: {e}")
    finally:
        _is_running = False


def apply_schedule():
    scheduler.remove_all_jobs()
    config = get_schedule_config()
    if not config["enabled"]:
        return
    for time_str in config["times"]:
        hour, minute = time_str.split(":")
        scheduler.add_job(
            run_scrape,
            "cron",
            hour=int(hour),
            minute=int(minute),
            timezone=config["timezone"],
            id=f"scrape_{time_str}",
        )


def start_scheduler():
    apply_schedule()
    if not scheduler.running:
        scheduler.start()


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)


def is_running():
    return _is_running


def get_next_run():
    jobs = scheduler.get_jobs()
    if not jobs:
        return None
    next_times = [job.next_run_time for job in jobs if job.next_run_time]
    if not next_times:
        return None
    return min(next_times).isoformat()
```

- [ ] **Step 2: Implement scraper API routes**

```python
# app/routes/scraper_api.py
import threading

from fastapi import APIRouter

from app.database import get_db
from app.scheduler import (
    get_schedule_config,
    save_schedule_config,
    apply_schedule,
    run_scrape,
    is_running,
    get_next_run,
)
from app.models import ScheduleConfig, ScheduleUpdate

router = APIRouter()


@router.get("/scraper/status")
def scraper_status():
    conn = get_db()
    last_success = conn.execute(
        "SELECT finished_at FROM scrape_runs WHERE status = 'success' ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return {
        "status": "running" if is_running() else "idle",
        "next_run": get_next_run(),
        "last_success": last_success[0] if last_success else None,
    }


@router.get("/scraper/runs")
def scraper_runs():
    conn = get_db()
    cursor = conn.execute(
        "SELECT * FROM scrape_runs ORDER BY started_at DESC LIMIT 20"
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


@router.post("/scraper/run")
def trigger_scrape():
    if is_running():
        return {"status": "already_running"}
    thread = threading.Thread(target=run_scrape, daemon=True)
    thread.start()
    return {"status": "started"}


@router.get("/scraper/schedule")
def get_schedule():
    return get_schedule_config()


@router.put("/scraper/schedule")
def update_schedule(config: ScheduleUpdate):
    save_schedule_config(config.times, config.timezone, config.enabled)
    apply_schedule()
    return get_schedule_config()
```

- [ ] **Step 3: Wire scheduler into app lifespan**

Update `app/main.py`:

```python
# app/main.py
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.scheduler import start_scheduler, stop_scheduler
from app.routes import dashboard, billionaires, analytics, scraper_api, export


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Bloomberg Scraper", lifespan=lifespan)

app.include_router(dashboard.router, prefix="/api")
app.include_router(billionaires.router, prefix="/api")
app.include_router(analytics.router, prefix="/api")
app.include_router(scraper_api.router, prefix="/api")
app.include_router(export.router, prefix="/api")

static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
```

- [ ] **Step 4: Run all tests**

Run: `pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/scheduler.py app/routes/scraper_api.py app/main.py
git commit -m "feat: scheduler and scraper API with run-now trigger"
```

---

### Task 8: Export Routes

**Files:**
- Modify: `app/routes/export.py`

- [ ] **Step 1: Implement export routes**

```python
# app/routes/export.py
import csv
import io
import json

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse, FileResponse

from app.database import get_db, DB_PATH

router = APIRouter()


@router.get("/export")
def export_data(
    format: str = "csv",
    scope: str = "latest",
    from_date: str | None = None,
    to_date: str | None = None,
    country: str | None = None,
    industry: str | None = None,
    top: int | None = None,
):
    conn = get_db()
    conditions = []
    params = []

    if scope == "latest":
        conditions.append("scraped_at = (SELECT MAX(scraped_at) FROM billionaires)")
    elif scope == "range" and from_date and to_date:
        conditions.append("DATE(scraped_at) BETWEEN ? AND ?")
        params.extend([from_date, to_date])

    if country:
        conditions.append("citizenship = ?")
        params.append(country)
    if industry:
        conditions.append("industry = ?")
        params.append(industry)

    where = " AND ".join(conditions) if conditions else "1=1"
    limit = f"LIMIT {top}" if top else ""

    sql = f"SELECT * FROM billionaires WHERE {where} ORDER BY scraped_at DESC, rank {limit}"
    cursor = conn.execute(sql, params)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    if format == "json":
        return StreamingResponse(
            io.BytesIO(json.dumps(rows, indent=2).encode()),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=bloomberg_billionaires.json"},
        )

    if not rows:
        return StreamingResponse(
            io.BytesIO(b""),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=bloomberg_billionaires.csv"},
        )

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=bloomberg_billionaires.csv"},
    )


@router.get("/export/db")
def export_db():
    db_path = str(DB_PATH)
    return FileResponse(
        db_path,
        media_type="application/x-sqlite3",
        filename="bloomberg.db",
    )
```

- [ ] **Step 2: Run all tests**

Run: `pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add app/routes/export.py
git commit -m "feat: export endpoints for CSV, JSON, and raw SQLite download"
```

---

### Task 9: Frontend — HTML Shell

**Files:**
- Create: `static/index.html`

- [ ] **Step 1: Create the single-page HTML shell**

```html
<!-- static/index.html -->
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bloomberg Scraper</title>
    <link rel="stylesheet" href="/style.css">
    <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
</head>
<body x-data="app()" x-init="init()">
    <nav>
        <div class="nav-brand">Bloomberg Scraper</div>
        <div class="nav-tabs">
            <button :class="{ active: tab === 'dashboard' }" @click="tab = 'dashboard'">Dashboard</button>
            <button :class="{ active: tab === 'table' }" @click="tab = 'table'; loadTable()">Table</button>
            <button :class="{ active: tab === 'analytics' }" @click="tab = 'analytics'; loadAnalytics()">Analytics</button>
            <button :class="{ active: tab === 'scraper' }" @click="tab = 'scraper'; loadScraper()">Scraper</button>
            <button :class="{ active: tab === 'export' }" @click="tab = 'export'">Export</button>
        </div>
        <div class="nav-status">
            <span class="status-dot" :class="scraperStatus.status"></span>
            <span x-text="scraperStatus.last_success ? 'Last: ' + formatDate(scraperStatus.last_success) : 'No data yet'"></span>
        </div>
    </nav>

    <!-- Dashboard Tab -->
    <main x-show="tab === 'dashboard'">
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-label">Total Wealth</div>
                <div class="stat-value" x-text="formatWealth(dashboard.total_wealth)"></div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Billionaires</div>
                <div class="stat-value" x-text="dashboard.count"></div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Snapshots</div>
                <div class="stat-value" x-text="dashboard.snapshots"></div>
            </div>
        </div>
    </main>

    <!-- Table Tab -->
    <main x-show="tab === 'table'">
        <div class="filters">
            <input type="text" placeholder="Search by name..." x-model="tableFilters.q" @input.debounce.300ms="loadTable()">
            <select x-model="tableFilters.country" @change="loadTable()">
                <option value="">All Countries</option>
                <template x-for="c in countries"><option :value="c" x-text="c"></option></template>
            </select>
            <select x-model="tableFilters.industry" @change="loadTable()">
                <option value="">All Industries</option>
                <template x-for="i in industries"><option :value="i" x-text="i"></option></template>
            </select>
            <select x-model="tableFilters.gender" @change="loadTable()">
                <option value="">All Genders</option>
                <option value="male">Male</option>
                <option value="female">Female</option>
            </select>
        </div>
        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        <th @click="sortTable('rank')">#</th>
                        <th @click="sortTable('common_name')">Name</th>
                        <th @click="sortTable('net_worth_usd')">Net Worth</th>
                        <th @click="sortTable('last_change_usd')">Daily Δ</th>
                        <th @click="sortTable('ytd_change_usd')">YTD Δ</th>
                        <th>Country</th>
                        <th>Industry</th>
                        <th @click="sortTable('age')">Age</th>
                    </tr>
                </thead>
                <tbody>
                    <template x-for="b in tableData.data" :key="b.person_id">
                        <tr>
                            <td x-text="b.rank"></td>
                            <td x-text="b.common_name"></td>
                            <td x-text="formatWealth(b.net_worth_usd)"></td>
                            <td :class="b.last_change_usd >= 0 ? 'positive' : 'negative'" x-text="formatChange(b.last_change_usd)"></td>
                            <td :class="b.ytd_change_usd >= 0 ? 'positive' : 'negative'" x-text="formatChange(b.ytd_change_usd)"></td>
                            <td x-text="b.citizenship"></td>
                            <td x-text="b.industry"></td>
                            <td x-text="b.age"></td>
                        </tr>
                    </template>
                </tbody>
            </table>
        </div>
        <div class="pagination">
            <span x-text="`Page ${tableData.page} of ${tableData.pages}`"></span>
            <button @click="tableFilters.page--; loadTable()" :disabled="tableData.page <= 1">Prev</button>
            <button @click="tableFilters.page++; loadTable()" :disabled="tableData.page >= tableData.pages">Next</button>
        </div>
    </main>

    <!-- Analytics Tab -->
    <main x-show="tab === 'analytics'">
        <div class="chart-controls">
            <div class="presets">
                <span>Quick:</span>
                <button @click="setPreset('top5')">Top 5</button>
                <button @click="setPreset('top10')">Top 10</button>
                <button @click="setPreset('tech')">Tech</button>
                <button @click="setPreset('women')">Women</button>
            </div>
            <div class="person-search">
                <div class="tags">
                    <template x-for="(p, idx) in selectedPeople" :key="p.person_id">
                        <span class="tag" :style="`background:${chartColors[idx % chartColors.length]}`">
                            <span x-text="p.common_name"></span>
                            <span class="tag-remove" @click="removePerson(idx)">×</span>
                        </span>
                    </template>
                    <input type="text" placeholder="Add person..." x-model="personQuery" @input.debounce.300ms="searchPeople()">
                </div>
                <div class="autocomplete" x-show="searchResults.length > 0">
                    <template x-for="r in searchResults" :key="r.person_id">
                        <div class="autocomplete-item" @click="addPerson(r)">
                            <span x-text="r.common_name"></span>
                            <span class="muted" x-text="formatWealth(r.net_worth_usd) + ' · #' + r.rank"></span>
                        </div>
                    </template>
                </div>
            </div>
        </div>
        <div class="chart-area">
            <canvas id="wealthChart"></canvas>
        </div>
        <div class="aggregate-grid">
            <div class="aggregate-card">
                <h3>Wealth by Industry</h3>
                <canvas id="industryChart"></canvas>
            </div>
            <div class="aggregate-card">
                <h3>Wealth by Country</h3>
                <canvas id="countryChart"></canvas>
            </div>
            <div class="aggregate-card">
                <h3>Gender Distribution</h3>
                <canvas id="genderChart"></canvas>
            </div>
            <div class="aggregate-card">
                <h3>Age Distribution</h3>
                <canvas id="ageChart"></canvas>
            </div>
        </div>
    </main>

    <!-- Scraper Tab -->
    <main x-show="tab === 'scraper'">
        <div class="scraper-status-card">
            <div>
                <strong>Scraper Status</strong>
                <div>
                    <span class="status-dot" :class="scraperStatus.status"></span>
                    <span x-text="scraperStatus.status === 'running' ? 'Running...' : 'Idle — next run ' + (scraperStatus.next_run ? formatDate(scraperStatus.next_run) : 'not scheduled')"></span>
                </div>
            </div>
            <button class="btn-primary" @click="triggerScrape()" :disabled="scraperStatus.status === 'running'">Run Now</button>
        </div>
        <div class="schedule-card">
            <h3>Schedule</h3>
            <div class="form-row">
                <label>Run at:</label>
                <div class="time-inputs">
                    <template x-for="(t, idx) in schedule.times" :key="idx">
                        <div class="time-input-group">
                            <input type="time" :value="t" @change="schedule.times[idx] = $event.target.value">
                            <button class="btn-small" @click="schedule.times.splice(idx, 1)">×</button>
                        </div>
                    </template>
                    <button class="btn-small" @click="schedule.times.push('12:00')">+ Add time</button>
                </div>
            </div>
            <div class="form-row">
                <label>Timezone:</label>
                <select x-model="schedule.timezone">
                    <option>UTC</option>
                    <option>Europe/Berlin</option>
                    <option>US/Eastern</option>
                    <option>US/Pacific</option>
                    <option>Asia/Tokyo</option>
                </select>
            </div>
            <button class="btn-primary" @click="saveSchedule()">Save</button>
        </div>
        <div class="runs-card">
            <h3>Recent Runs</h3>
            <table>
                <thead><tr><th>Time</th><th>Status</th><th>Records</th><th>Duration</th></tr></thead>
                <tbody>
                    <template x-for="run in scraperRuns" :key="run.id">
                        <tr>
                            <td x-text="formatDate(run.started_at)"></td>
                            <td :class="run.status === 'success' ? 'positive' : 'negative'" x-text="run.status"></td>
                            <td x-text="run.record_count || '—'"></td>
                            <td x-text="run.duration_ms ? (run.duration_ms / 1000).toFixed(1) + 's' : '—'"></td>
                        </tr>
                    </template>
                </tbody>
            </table>
        </div>
    </main>

    <!-- Export Tab -->
    <main x-show="tab === 'export'">
        <div class="export-card">
            <h3>Download Data</h3>
            <div class="form-row">
                <label>Scope:</label>
                <div class="radio-group">
                    <label><input type="radio" value="latest" x-model="exportScope"> Latest snapshot</label>
                    <label><input type="radio" value="range" x-model="exportScope"> Date range</label>
                    <label><input type="radio" value="all" x-model="exportScope"> All data</label>
                </div>
            </div>
            <div class="form-row" x-show="exportScope === 'range'">
                <label>Range:</label>
                <input type="date" x-model="exportFrom">
                <input type="date" x-model="exportTo">
            </div>
            <div class="form-row">
                <label>Format:</label>
                <div class="radio-group">
                    <label><input type="radio" value="csv" x-model="exportFormat"> CSV</label>
                    <label><input type="radio" value="json" x-model="exportFormat"> JSON</label>
                </div>
            </div>
            <button class="btn-primary" @click="downloadExport()">⬇ Download</button>
            <p class="muted">API: <code x-text="exportUrl()"></code></p>
        </div>
        <div class="export-card">
            <h3>Download Database</h3>
            <p>Download the raw SQLite file for local analysis.</p>
            <a href="/api/export/db" class="btn-primary" download>⬇ Download bloomberg.db</a>
        </div>
    </main>

    <script src="/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Commit**

```bash
git add static/index.html
git commit -m "feat: frontend HTML shell with all tabs"
```

---

### Task 10: Frontend — JavaScript (app.js)

**Files:**
- Create: `static/app.js`

- [ ] **Step 1: Create app.js**

```javascript
// static/app.js
function app() {
    return {
        tab: 'dashboard',
        dashboard: { total_wealth: 0, count: 0, snapshots: 0, latest_scrape: null },
        tableData: { data: [], total: 0, page: 1, pages: 1 },
        tableFilters: { q: '', country: '', industry: '', gender: '', page: 1, sort: 'rank' },
        countries: [],
        industries: [],
        scraperStatus: { status: 'idle', next_run: null, last_success: null },
        scraperRuns: [],
        schedule: { times: ['08:00'], timezone: 'UTC', enabled: true },
        selectedPeople: [],
        personQuery: '',
        searchResults: [],
        chartColors: ['#4ecdc4', '#ff6b6b', '#6c5ce7', '#fdcb6e', '#a29bfe', '#00b894', '#e17055', '#0984e3', '#d63031', '#6ab04c'],
        wealthChart: null,
        exportScope: 'latest',
        exportFormat: 'csv',
        exportFrom: '',
        exportTo: '',

        async init() {
            const [dashRes, statusRes] = await Promise.all([
                fetch('/api/dashboard').then(r => r.json()),
                fetch('/api/scraper/status').then(r => r.json()),
            ]);
            this.dashboard = dashRes;
            this.scraperStatus = statusRes;
            this.loadFilterOptions();
        },

        async loadFilterOptions() {
            const [indRes, cntRes] = await Promise.all([
                fetch('/api/analytics/by-industry').then(r => r.json()),
                fetch('/api/analytics/by-country').then(r => r.json()),
            ]);
            this.industries = indRes.map(r => r.industry).filter(Boolean);
            this.countries = cntRes.map(r => r.country).filter(Boolean);
        },

        async loadTable() {
            const p = this.tableFilters;
            const params = new URLSearchParams();
            if (p.q) params.set('q', p.q);
            if (p.country) params.set('country', p.country);
            if (p.industry) params.set('industry', p.industry);
            if (p.gender) params.set('gender', p.gender);
            params.set('sort', p.sort);
            params.set('page', p.page);
            this.tableData = await fetch(`/api/billionaires?${params}`).then(r => r.json());
        },

        sortTable(col) {
            if (this.tableFilters.sort === col) {
                this.tableFilters.sort = `-${col}`;
            } else {
                this.tableFilters.sort = col;
            }
            this.tableFilters.page = 1;
            this.loadTable();
        },

        async loadAnalytics() {
            const [indRes, cntRes, demoRes] = await Promise.all([
                fetch('/api/analytics/by-industry').then(r => r.json()),
                fetch('/api/analytics/by-country').then(r => r.json()),
                fetch('/api/analytics/demographics').then(r => r.json()),
            ]);
            this.renderBarChart('industryChart', indRes.slice(0, 8), 'industry', 'total_wealth');
            this.renderBarChart('countryChart', cntRes.slice(0, 8), 'country', 'total_wealth');
            this.renderDoughnut('genderChart', demoRes.gender);
            this.renderAgeChart('ageChart', demoRes.age_distribution);
        },

        async loadScraper() {
            const [statusRes, runsRes, schedRes] = await Promise.all([
                fetch('/api/scraper/status').then(r => r.json()),
                fetch('/api/scraper/runs').then(r => r.json()),
                fetch('/api/scraper/schedule').then(r => r.json()),
            ]);
            this.scraperStatus = statusRes;
            this.scraperRuns = runsRes;
            this.schedule = schedRes;
        },

        async triggerScrape() {
            await fetch('/api/scraper/run', { method: 'POST' });
            this.scraperStatus.status = 'running';
            setTimeout(() => this.loadScraper(), 5000);
        },

        async saveSchedule() {
            await fetch('/api/scraper/schedule', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(this.schedule),
            });
            this.loadScraper();
        },

        async searchPeople() {
            if (this.personQuery.length < 2) { this.searchResults = []; return; }
            this.searchResults = await fetch(`/api/search?q=${encodeURIComponent(this.personQuery)}`).then(r => r.json());
        },

        addPerson(person) {
            if (!this.selectedPeople.find(p => p.person_id === person.person_id)) {
                this.selectedPeople.push(person);
                this.loadWealthChart();
            }
            this.personQuery = '';
            this.searchResults = [];
        },

        removePerson(idx) {
            this.selectedPeople.splice(idx, 1);
            this.loadWealthChart();
        },

        async setPreset(preset) {
            let params = '';
            if (preset === 'top5') params = '?q=&sort=rank&page=1';
            else if (preset === 'top10') params = '?q=&sort=rank&page=1';
            else if (preset === 'tech') params = '?industry=Technology&sort=rank&page=1';
            else if (preset === 'women') params = '?gender=female&sort=rank&page=1';
            const res = await fetch(`/api/billionaires${params}`).then(r => r.json());
            const limit = preset === 'top5' ? 5 : preset === 'top10' ? 10 : 5;
            this.selectedPeople = res.data.slice(0, limit).map(b => ({
                person_id: b.person_id, common_name: b.common_name,
                net_worth_usd: b.net_worth_usd, rank: b.rank,
            }));
            this.loadWealthChart();
        },

        async loadWealthChart() {
            if (this.selectedPeople.length === 0) return;
            const datasets = [];
            for (let i = 0; i < this.selectedPeople.length; i++) {
                const p = this.selectedPeople[i];
                const history = await fetch(`/api/billionaires/${p.person_id}/history`).then(r => r.json());
                datasets.push({
                    label: p.common_name,
                    data: history.map(h => ({ x: h.scraped_at, y: h.net_worth_usd })),
                    borderColor: this.chartColors[i % this.chartColors.length],
                    fill: false, tension: 0.1,
                });
            }
            const ctx = document.getElementById('wealthChart');
            if (this.wealthChart) this.wealthChart.destroy();
            this.wealthChart = new Chart(ctx, {
                type: 'line',
                data: { datasets },
                options: {
                    responsive: true,
                    scales: {
                        x: { type: 'category' },
                        y: { ticks: { callback: v => this.formatWealth(v) } },
                    },
                },
            });
        },

        renderBarChart(canvasId, data, labelKey, valueKey) {
            const ctx = document.getElementById(canvasId);
            new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: data.map(d => d[labelKey]),
                    datasets: [{ data: data.map(d => d[valueKey]), backgroundColor: this.chartColors }],
                },
                options: { indexAxis: 'y', responsive: true, plugins: { legend: { display: false } } },
            });
        },

        renderDoughnut(canvasId, data) {
            const ctx = document.getElementById(canvasId);
            new Chart(ctx, {
                type: 'doughnut',
                data: {
                    labels: data.map(d => d.gender),
                    datasets: [{ data: data.map(d => d.count), backgroundColor: ['#4ecdc4', '#ff6b6b', '#ccc'] }],
                },
                options: { responsive: true },
            });
        },

        renderAgeChart(canvasId, data) {
            const ctx = document.getElementById(canvasId);
            new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: data.map(d => d.bracket),
                    datasets: [{ data: data.map(d => d.count), backgroundColor: '#4ecdc4' }],
                },
                options: { responsive: true, plugins: { legend: { display: false } } },
            });
        },

        downloadExport() {
            const params = new URLSearchParams();
            params.set('format', this.exportFormat);
            params.set('scope', this.exportScope);
            if (this.exportScope === 'range') {
                params.set('from_date', this.exportFrom);
                params.set('to_date', this.exportTo);
            }
            window.location.href = `/api/export?${params}`;
        },

        exportUrl() {
            const params = new URLSearchParams();
            params.set('format', this.exportFormat);
            params.set('scope', this.exportScope);
            return `GET /api/export?${params}`;
        },

        formatWealth(v) {
            if (!v) return '$0';
            if (v >= 1e12) return `$${(v / 1e12).toFixed(1)}T`;
            if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
            if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
            return `$${v.toLocaleString()}`;
        },

        formatChange(v) {
            if (!v) return '$0';
            const sign = v >= 0 ? '+' : '';
            if (Math.abs(v) >= 1e9) return `${sign}$${(v / 1e9).toFixed(1)}B`;
            if (Math.abs(v) >= 1e6) return `${sign}$${(v / 1e6).toFixed(1)}M`;
            return `${sign}$${v.toLocaleString()}`;
        },

        formatDate(d) {
            if (!d) return '';
            return new Date(d).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
        },
    };
}
```

- [ ] **Step 2: Commit**

```bash
git add static/app.js
git commit -m "feat: frontend JavaScript with Alpine.js stores and Chart.js"
```

---

### Task 11: Frontend — CSS

**Files:**
- Create: `static/style.css`

- [ ] **Step 1: Create style.css**

```css
/* static/style.css */
* { margin: 0; padding: 0; box-sizing: border-box; }

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #f8f9fa;
    color: #1a1a2e;
}

nav {
    background: #1a1a2e;
    color: white;
    display: flex;
    align-items: center;
    padding: 0 24px;
    height: 56px;
    gap: 24px;
}

.nav-brand { font-weight: bold; font-size: 16px; white-space: nowrap; }

.nav-tabs { display: flex; gap: 4px; }
.nav-tabs button {
    background: none; border: none; color: rgba(255,255,255,0.6);
    padding: 8px 14px; border-radius: 6px; cursor: pointer; font-size: 14px;
}
.nav-tabs button.active { background: rgba(255,255,255,0.1); color: white; }
.nav-tabs button:hover { color: white; }

.nav-status { margin-left: auto; font-size: 12px; color: rgba(255,255,255,0.7); display: flex; align-items: center; gap: 6px; }

.status-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.status-dot.idle { background: #4ecdc4; }
.status-dot.running { background: #fdcb6e; animation: pulse 1s infinite; }
.status-dot.failed { background: #ff6b6b; }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }

main { max-width: 1200px; margin: 24px auto; padding: 0 24px; }

.stats-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 24px; }
.stat-card { background: white; padding: 20px; border-radius: 8px; border: 1px solid #e0e0e0; }
.stat-label { font-size: 12px; color: #888; margin-bottom: 4px; }
.stat-value { font-size: 24px; font-weight: bold; }

.filters { display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
.filters input, .filters select {
    padding: 8px 12px; border: 1px solid #ddd; border-radius: 6px; font-size: 14px;
}
.filters input { width: 200px; }

.table-container { background: white; border-radius: 8px; border: 1px solid #e0e0e0; overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
thead tr { background: #fafafa; border-bottom: 2px solid #e0e0e0; }
th { text-align: left; padding: 12px; color: #666; cursor: pointer; white-space: nowrap; }
td { padding: 10px 12px; border-bottom: 1px solid #f0f0f0; }
tbody tr:hover { background: #f8f9ff; }

.positive { color: #4ecdc4; }
.negative { color: #ff6b6b; }

.pagination { display: flex; align-items: center; gap: 12px; margin-top: 12px; justify-content: flex-end; font-size: 13px; }
.pagination button { padding: 6px 12px; border: 1px solid #ddd; border-radius: 4px; background: white; cursor: pointer; }
.pagination button:disabled { opacity: 0.4; cursor: default; }

.chart-controls { margin-bottom: 16px; }
.presets { display: flex; gap: 6px; align-items: center; margin-bottom: 12px; font-size: 13px; }
.presets button { padding: 5px 12px; border: 1px solid #ddd; border-radius: 4px; background: white; cursor: pointer; font-size: 12px; }
.presets button:hover { border-color: #4ecdc4; }

.person-search { position: relative; }
.tags { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; background: #fafafa; border: 1px solid #ddd; border-radius: 6px; padding: 8px; }
.tags input { border: none; outline: none; background: transparent; font-size: 13px; min-width: 120px; }
.tag { color: white; padding: 3px 10px; border-radius: 12px; font-size: 12px; display: flex; align-items: center; gap: 4px; }
.tag-remove { cursor: pointer; opacity: 0.8; }

.autocomplete { position: absolute; top: 100%; left: 0; right: 0; background: white; border: 1px solid #ddd; border-radius: 6px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); z-index: 10; margin-top: 4px; }
.autocomplete-item { padding: 10px 14px; cursor: pointer; display: flex; justify-content: space-between; font-size: 13px; }
.autocomplete-item:hover { background: #f0f8ff; }

.chart-area { background: white; padding: 20px; border-radius: 8px; border: 1px solid #e0e0e0; margin-bottom: 24px; }

.aggregate-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.aggregate-card { background: white; padding: 16px; border-radius: 8px; border: 1px solid #e0e0e0; }
.aggregate-card h3 { font-size: 13px; margin-bottom: 12px; }

.scraper-status-card, .schedule-card, .runs-card, .export-card {
    background: white; padding: 20px; border-radius: 8px; border: 1px solid #e0e0e0; margin-bottom: 16px;
}
.scraper-status-card { display: flex; justify-content: space-between; align-items: center; }

.form-row { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.form-row label { color: #666; width: 80px; font-size: 13px; }
.form-row select, .form-row input[type="time"], .form-row input[type="date"] {
    padding: 6px 12px; border: 1px solid #ddd; border-radius: 4px; font-size: 13px;
}
.radio-group { display: flex; gap: 12px; font-size: 13px; }

.time-inputs { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.time-input-group { display: flex; align-items: center; gap: 4px; }

.btn-primary {
    background: #1a1a2e; color: white; border: none; padding: 10px 20px;
    border-radius: 6px; font-weight: bold; cursor: pointer; font-size: 14px; text-decoration: none; display: inline-block;
}
.btn-primary:hover { background: #2a2a4e; }
.btn-small { background: none; border: 1px solid #ddd; padding: 4px 8px; border-radius: 4px; cursor: pointer; font-size: 12px; }

.muted { color: #888; font-size: 12px; }
code { background: #f0f0f0; padding: 2px 6px; border-radius: 3px; font-size: 12px; }

h3 { font-size: 15px; margin-bottom: 12px; }

@media (max-width: 768px) {
    .stats-grid { grid-template-columns: 1fr; }
    .aggregate-grid { grid-template-columns: 1fr; }
    .nav-tabs { overflow-x: auto; }
}
```

- [ ] **Step 2: Commit**

```bash
git add static/style.css
git commit -m "feat: frontend CSS layout and theme"
```

---

### Task 12: Integration Test — Start Server & Verify

**Files:** None new — verification only

- [ ] **Step 1: Run all tests**

Run: `pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 2: Start the server and verify manually**

Run: `uvicorn app.main:app --reload`
Expected: Server starts on http://127.0.0.1:8000

- [ ] **Step 3: Verify API responds**

Run: `curl http://localhost:8000/api/dashboard`
Expected: `{"total_wealth":0,"count":0,"snapshots":0,"latest_scrape":null}`

- [ ] **Step 4: Trigger a scrape and verify data**

Run: `curl -X POST http://localhost:8000/api/scraper/run`
Expected: `{"status":"started"}`

Wait 5 seconds, then:

Run: `curl http://localhost:8000/api/dashboard`
Expected: `total_wealth` and `count` are non-zero (500 billionaires loaded)

- [ ] **Step 5: Verify frontend loads**

Open http://localhost:8000 in browser.
Expected: Dashboard tab shows stats, Table tab shows billionaires, all tabs functional.

- [ ] **Step 6: Commit final state**

```bash
git add -A
git commit -m "feat: bloomberg scraper web dashboard complete"
```
