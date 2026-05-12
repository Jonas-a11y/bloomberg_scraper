# Bloomberg Scraper — Web Dashboard Design

## Overview

A web-based dashboard for automated Bloomberg Billionaires Index scraping with scheduling, data exploration, and historical analysis. Designed for a small hosted team with a focus on data science use cases.

## Stack

- **Backend:** FastAPI (Python)
- **Scheduler:** APScheduler (in-process BackgroundScheduler)
- **Database:** SQLite (single file, downloadable)
- **Frontend:** Single HTML page + Alpine.js (reactivity) + Chart.js (charts)
- **No build step** — static files served by FastAPI, JS libs from CDN

## Project Structure

```
bloomberg_scraper/
├── app/
│   ├── main.py            # FastAPI app, routes, static file serving
│   ├── scraper.py         # Scrape logic wrapped as a callable
│   ├── scheduler.py       # APScheduler setup, schedule CRUD
│   ├── database.py        # SQLite connection, schema, queries
│   └── models.py          # Pydantic models for API request/response
├── static/
│   ├── index.html         # Single-page app shell (all tabs)
│   ├── app.js             # Alpine.js components + Chart.js charts
│   └── style.css          # Minimal custom CSS
├── data/
│   └── bloomberg.db       # SQLite database
├── scrape_bloomberg.py    # Standalone CLI script (kept)
├── build_master_dataset.py
└── requirements.txt
```

## UI Layout

Top navigation bar with 5 tabs: **Dashboard**, **Table**, **Analytics**, **Scraper**, **Export**.

Tab panels shown/hidden via Alpine.js `x-show` — no client-side router.

### Dashboard Tab

- 3 stat cards: Total Wealth, Billionaire Count, Snapshot Count
- Summary chart: total wealth over time (last 30 days)
- Last scrape status indicator in top-right corner

### Table Tab

- Filter row: search by name, country dropdown, industry dropdown, gender dropdown, snapshot date picker
- Sortable table: rank, name, net worth, daily change, YTD change, country, industry, age
- Clickable rows expand to detail view
- Pagination (50 per page)

### Analytics Tab

- Chart type selector: Wealth Over Time, Rank Changes, Biggest Movers (Day), Biggest Movers (YTD)
- Time range: Last 7 days, 30 days, 90 days, All time
- Person selection: search input with autocomplete (shows name + net worth + rank), selected people as colored removable tags, quick preset buttons (Top 5, Top 10, Tech, Women) above the search
- Aggregate panels below the main chart:
  - Wealth by Industry (horizontal bar chart)
  - Wealth by Country (horizontal bar chart)
  - Gender Distribution (donut chart)
  - Age Distribution (histogram)
- Snapshot Comparison: pick two dates, shows biggest rank/wealth movers and who entered/dropped off

### Scraper Tab

- Status banner: current state (Idle/Running/Failed), next run time with countdown, "Run Now" button
- Schedule config: frequency dropdown, time pickers for specific run times with "+ Add time" button, timezone selector, Save button
- Recent runs table: time, status (success/failed), records count, duration

### Export Tab

- Scope: Latest snapshot / Date range / All data
- Optional filters: country, industry, top N
- Format: CSV or JSON
- Download button
- API endpoint shown for programmatic access
- Separate "Download Database" button for the raw SQLite file

## Database Schema

```sql
CREATE TABLE billionaires (
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

CREATE INDEX idx_billionaires_person_scraped ON billionaires(person_id, scraped_at);
CREATE INDEX idx_billionaires_scraped ON billionaires(scraped_at);

CREATE TABLE scrape_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   DATETIME NOT NULL,
    finished_at  DATETIME,
    status       TEXT NOT NULL,  -- 'running', 'success', 'failed'
    record_count INTEGER,
    duration_ms  INTEGER,
    error        TEXT
);

CREATE TABLE schedule_config (
    id       INTEGER PRIMARY KEY DEFAULT 1,
    times    TEXT NOT NULL DEFAULT '["08:00"]',  -- JSON array of "HH:MM"
    timezone TEXT NOT NULL DEFAULT 'UTC',
    enabled  BOOLEAN NOT NULL DEFAULT 1
);
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/dashboard` | Summary stats (total wealth, count, snapshots) |
| GET | `/api/billionaires` | Paginated list. Params: `country`, `industry`, `gender`, `snapshot`, `sort`, `page`, `q` |
| GET | `/api/billionaires/{person_id}/history` | Time series for one person |
| GET | `/api/snapshots` | List of available snapshot dates |
| GET | `/api/snapshots/compare` | Diff two snapshots. Params: `from`, `to` |
| GET | `/api/analytics/by-industry` | Aggregate wealth by industry |
| GET | `/api/analytics/by-country` | Aggregate wealth by country |
| GET | `/api/analytics/demographics` | Gender split, age distribution |
| GET | `/api/search` | Autocomplete search. Params: `q` |
| GET | `/api/export` | Download data. Params: `format` (csv/json), `scope` (latest/range/all), `country`, `industry`, `top` |
| GET | `/api/export/db` | Download raw SQLite file |
| GET | `/api/scraper/status` | Current scraper status, next run time |
| GET | `/api/scraper/runs` | Recent run history |
| POST | `/api/scraper/run` | Trigger immediate scrape |
| GET | `/api/scraper/schedule` | Get schedule config |
| PUT | `/api/scraper/schedule` | Update schedule config (times, timezone, enabled) |

## Scheduler Behavior

- APScheduler `BackgroundScheduler` runs inside the FastAPI process
- On startup: reads `schedule_config` from SQLite, registers cron jobs for each configured time
- On schedule update via API: reschedules in-memory + persists to DB
- Each scrape run:
  1. Insert `scrape_runs` row with status='running'
  2. Execute scrape (existing `curl_cffi` logic)
  3. Bulk-insert 500 billionaire rows
  4. Update run row: status='success', record_count, duration_ms
  5. On failure: status='failed', error message stored
- On server restart: picks up persisted schedule from DB

## Error Handling

- Bloomberg returns 403 / bot detection: mark run as failed, log error, wait for next scheduled run (no immediate retry)
- Partial data (<400 records): mark as failed, discard partial results
- Frontend: shows last successful scrape time prominently, recent failures visible in Scraper tab run history

## Frontend Architecture

```
index.html — tab shell, Alpine.js x-show panels
app.js (~150-200 lines):
  ├── Alpine stores (one per tab, lazy-loaded on tab switch)
  ├── Chart.js instances
  └── Utility functions (debounced search, currency formatting)
style.css (~100 lines):
  ├── Layout (nav, grid, cards)
  └── Theme (colors, typography, responsive basics)
```

No build pipeline. Alpine.js and Chart.js loaded from CDN. All static files served by FastAPI's `StaticFiles` mount.

## Dependencies

```
fastapi
uvicorn
apscheduler
curl-cffi
pandas
```
