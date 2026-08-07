# Bloomberg Billionaires Scraper

A self-hosted FastAPI app that scrapes the [Bloomberg Billionaires Index](https://www.bloomberg.com/billionaires/), stores daily snapshots in SQLite, enriches them with Wikidata, Wikipedia, Forbes, GDELT and Yahoo Finance, and serves a single‑page dashboard for browsing, comparing and analysing the world's 500 wealthiest people over a 25‑year window.

🌐 **Live instance:** <http://router.jonas-giessler.de:45241>

> Heads-up: the live instance runs from a residential connection and may occasionally be offline or slow. The container image is published to `jonasg03/bloomberg-scraper:latest` for `linux/amd64` and `linux/arm64` if you'd rather host it yourself.

---

## What it does

- **Scrapes** the Bloomberg Billionaires Index on a configurable cron schedule (curl_cffi with Chrome impersonation, exponential‑backoff retries).
- **Stores** every snapshot in `bloomberg.db` — rank, net worth, daily/YTD deltas, public/private/cash asset breakdowns, biography, schools, milestones.
- **Backfills** per‑person wealth history straight from the Bloomberg profile pages.
- **Time‑travels** back to 2001 by merging Bloomberg daily history with a Kaggle Forbes 1997–2023 dataset and Wikipedia's annual "World's Billionaires" pages.
- **Enriches** people via Wikidata (photos, birth/death dates, occupations, family ties), via Wikipedia citations (milestones, dated news), and via GDELT (live news with importance scoring).
- **Builds a network graph** in a separate `network.db` — family edges (spouse/parent/child/sibling) plus shared employers, schools, boards and co‑held companies as bridge entities.
- **Computes analytics**: Gini coefficient, top‑1/10/100 concentration, country/industry migration, bar‑chart‑race over time, pairwise log‑return correlations (~125k pairs on 500 people in <1s), public‑market deep dives by country and industry via Yahoo Screener + FX conversion.
- **Exports** any subset to CSV / JSON, including the full SQLite database.

---

## Screenshots

A handful of features at a glance — all of these are tabs of the same single‑page app:

| | |
|---|---|
| ![Dashboard](insights-tab-final.png) | ![Time-travel](time-travel-2001-2024-diff.png) |
| Cross‑filtered insights with cohort tiles, Gini, top‑10 concentration. | Time‑travel slider scrubs from 2001 to today; rankings reconstructed server‑side. |
| ![Bar chart race](race-continuous.png) | ![Correlation heatmap](corr-500-canvas.png) |
| Continuous monthly bar‑chart race across 25 years. | 500×500 pairwise correlation heatmap, canvas‑rendered. |
| ![Profile with news](musk-news-card-2022.png) | ![Family graph](compare-pair-page-brin.png) |
| Per‑person profile with news markers pinned to the wealth curve. | Wikidata family + entity graph with shortest‑path finder. |

---

## Quickstart

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Then open <http://localhost:8000>.

On first start the app initialises both SQLite databases, applies migrations, kicks off the scheduler (default: 08:00 UTC) and warms the insights cache. With an empty DB use the **Scraper** tab's *Bootstrap* button to pre‑load Kaggle Forbes + Wikipedia + Wikidata + GDELT history in one go.

### Docker

```bash
docker run -p 8000:8000 -v ./data:/app/data jonasg03/bloomberg-scraper:latest
```

Mount `/app/data` to persist `bloomberg.db` and `network.db` across container restarts.

### Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `SCRAPER_PASSWORD` | Locks every write endpoint behind a session cookie. Leave unset for frictionless local dev. | unset (auth off) |
| `SCRAPER_SESSION_SECRET` | Stable cookie‑signing secret across workers/restarts. | random per process |

The Kaggle import expects credentials at `~/.kaggle/kaggle.json`.

---

## Tabs of the app

1. **Dashboard** — total wealth, billionaire count, newcomers / drop‑offs, wealth by age, concentration trend.
2. **Table** — filterable/sortable roster with a time‑travel slider (2001 → today) and a compare mode that diffs any two dates.
3. **Insights** — bar‑chart race, count over time, inequality, cohort survival, pairwise correlation heatmap, geographic migration Sankey, pair compare, concentration over time, gender/age splits.
4. **Network** — Wikidata‑derived family + entity graph (vis.js, force‑directed, freezes after 5s), shortest‑path finder, two‑person comparison.
5. **Market** — top public companies by country and by industry, treemaps, share‑class collapsing, FX‑converted to USD.
6. **Scraper** — admin panel: run‑now, schedule editor, history backfill, news refresh, Forbes/Kaggle import, full bootstrap, insights‑cache warm. Password‑gated if `SCRAPER_PASSWORD` is set.
7. **Export** — CSV/JSON with a column picker; scopes: latest snapshot, date range, full history; raw SQLite download too.

---

## For the nerds

Source code: <https://github.com/Jonas-a11y/bloomberg_scraper>

**Stack.** FastAPI + Uvicorn behind a `GZipMiddleware` (500‑byte threshold; turns ~600 KB history payloads into ~80 KB). Two SQLite databases in WAL mode with a 30 s lock timeout: `bloomberg.db` for the time series and `network.db` for the Wikidata graph, kept separate so a graph rebuild never blocks a scrape. APScheduler runs the cron jobs, plus a chain of post‑scrape follow‑ups (history backfill at +5 s, Wikidata catch‑up at +10 s, news refresh at +20 s, insights warm at +120 s). The frontend is a single 2.5 k‑line HTML page using Alpine.js for state, Chart.js for the regular charts and vis.js for the network graph — no build step.

**Scraping.** `curl_cffi` impersonates Chrome to clear the bot wall, regex‑extracts the `window.top500` blob from the HTML and flattens it into 55 columns. Per‑person daily history is pulled from each profile page's `window.profileData.stats` (throttled 1.5 s). Gender is inferred from pronouns in the biography and then overwritten by Wikidata's authoritative `P21` once the QID is resolved. Outliers in wealth history — revaluation artefacts and back‑office corrections — are flagged when a single day moves >+60 % or <−50 % on a ≥1 B prior, then confirmed by checking for reversion within five days; the API can serve either the raw or the cleaned series.

**Caching.** Anything expensive lives in the `insights_cache` table as gzip‑compressed JSON keyed by the endpoint and its sorted params. TTL is 6 h with a stale‑while‑revalidate strategy: a cold miss computes inline, every subsequent hit returns the stored payload immediately and refreshes in a background thread if the entry is stale. The whole cache warms on startup and after every successful scrape via ~23 precompute specs.

**Data sources.** Bloomberg for live wealth and profile details. Kaggle's Forbes 1997–2023 dataset for dense annual history (preferred over the Wikipedia scrape, which is vandalism‑prone). Wikipedia's *World's Billionaires* articles as the fallback for years past the Kaggle cutoff. Wikidata for QIDs, family relations (`P22`/`P25`/`P26`/`P40`/`P3373`/`P1038`), entities (employer, school, board, awards), photos and birth/death dates — the QID resolver was recently widened from ~4 % to ~80 % coverage by adding nickname expansions, diacritic variants and business‑hint candidate ranking. Yahoo Screener + yfinance for live market caps and FX, with a Wikidata SPARQL fallback for thin regions. GDELT 2.0 Doc API for news from Feb 2015 onward, plus Wikipedia citation harvesting for older milestones; both feeds get importance scores boosted by keywords (IPO, death, lawsuit, acquisition, …) and trusted‑source bonuses.

**Performance.** Wealth correlation is vectorised in NumPy — a 500×500 daily‑log‑return matrix with masked dot products turns ~125 000 pair correlations into a <1 s response. The yearly insights query was rewritten from correlated subqueries (~30 s on 1.8 M rows) to a single `GROUP BY` (~1 s). Yahoo's per‑ticker `info` calls are fanned out across 30 threads with a 6 h in‑memory cache; the screener pages 250 results at a time and oversamples non‑US regions to defeat regional caps. Market by‑industry sweeps 12 regions in parallel and then drops foreign mirrors (e.g. `NVD.DE` once `NVDA` is captured) by checking each ticker's HQ country. Share classes (`GOOGL`+`GOOG`, `BRK-A`+`BRK-B`) are collapsed by normalised name with an 85 % market‑cap tolerance so genuinely separate companies are never merged.

**Network graph.** Every node in the family/entity graph is by definition a ≥2‑person connector — false‑positive QIDs naturally drop out because a graph algorithm only writes an edge once both endpoints are known. Wikidata is the source of truth for family ties; Bloomberg's own asset holdings are folded in as synthetic bridge entities (`T:MSFT` for public tickers, `PRIV:<name>` for private companies) with `source='bloomberg'` so they survive Wikidata refreshes. The vis.js layout runs forceAtlas2Based for ~5 s, then freezes — a deliberate trade‑off after the live‑physics version drifted forever.

**Auth.** A single shared password (`SCRAPER_PASSWORD`) gates every write endpoint via a HttpOnly `SameSite=strict` session cookie. Constant‑time compare on login, sha256‑signed cookies, 30‑day max‑age. If the env var is unset, auth is off — convenient locally, dangerous in public.

**Deployment.** Docker image is pushed to `jonasg03/bloomberg-scraper:latest` on every push to `main` via GitHub Actions for `linux/amd64` and `linux/arm64`. The live instance runs from a home router on a single SQLite file; concurrency is handled by WAL mode rather than a real RDBMS, which is honest about the scale this thing operates at.
