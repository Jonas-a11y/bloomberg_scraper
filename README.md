# Bloomberg Billionaires Scraper

A self-hosted web app that scrapes the [Bloomberg Billionaires Index](https://www.bloomberg.com/billionaires/) and tracks wealth data over time.

## Features

- **Automated scraping** with configurable schedule (multiple times per day, any timezone)
- **Historical tracking** — stores daily snapshots of rank, net worth, and asset breakdowns for all 500 billionaires
- **Dashboard** with aggregate stats, filterable table, and Chart.js analytics
- **Configurable export** — download CSV/JSON with selectable fields (biography, schools, milestones, assets, etc.)
- **SQLite database** — lightweight, no external DB required

## Quickstart

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` in your browser.

## Docker

```bash
docker build -t bloomberg-scraper .
docker run -p 8000:8000 -v ./data:/app/data bloomberg-scraper
```

Mount `/app/data` to persist the database across container restarts.

## Data Source

The scraper fetches the Bloomberg Billionaires Index page and extracts the `window.top500` JSON object embedded in the HTML. This contains detailed data for the top 500 billionaires including net worth, assets, biography, milestones, schools, and more.

## Database Schema

- **persons** — static personal data (name, citizenship, biography, schools, milestones)
- **snapshots** — time-series financial data (rank, net worth, daily/YTD changes, asset breakdowns)
- **scrape_runs** — log of scrape attempts with status and duration
- **schedule_config** — scrape schedule settings

## Tech Stack

- FastAPI + Uvicorn
- SQLite
- Alpine.js + Chart.js (frontend)
- curl_cffi (scraping with browser impersonation)
- APScheduler (cron-like scheduling)
