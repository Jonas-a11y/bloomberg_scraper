# TODO — future ideas, improvements, refactors

A living wishlist. Roughly grouped by intent. Order within each section is rough priority (top = most useful / lowest effort first), but feel free to cherry‑pick.

---

## 🚀 New features

### Data & analytics
- [ ] **Custom alerts**: subscribe to a person, country, industry or rank threshold and receive an email / webhook / RSS entry when something interesting happens (entered top 10, lost >5 B in a day, died, etc.).
- [ ] **Predicted next‑day rank** based on the last 30 days of volatility + the asset mix (a tiny linear model is enough to be fun).
- [ ] **Asset‑level drill‑down**: click a person's "public assets" bar to see each holding (ticker, shares, value, % of net worth) over time, not just the daily total.
- [ ] **Currency switch** — render every number in EUR / GBP / JPY / CHF using the existing `market/fx.py` rates.
- [ ] **Real estate & art** — Wikidata has yacht/jet/property entries (`P1071`, `P276`) for many billionaires; surface as a "lifestyle" sub‑tab.
- [ ] **Crypto holdings** — pull from public on‑chain attributions (Arkham, etherscan tags) for the obvious names (CZ, SBF, the Winklevii…).
- [ ] **Survival curve** in the cohort tile: instead of just "still listed / dropped / died", plot a Kaplan‑Meier curve for each starting cohort year.
- [ ] **Inheritance / dynasty view**: project current wealth forward to the named heirs in Wikidata `P40`. "Who will be on the Bloomberg index in 2050?"
- [ ] **Income vs. wealth gap**: cross‑reference with World Bank median income to compute "years of median income" per billionaire per country.
- [ ] **Backtest a portfolio**: pick three billionaires, weight by 2010 net worth, see how their combined wealth tracked vs. the S&P 500.

### UX & UI
- [ ] **Dark mode** toggle (the palette is mostly there — finish it and remember the choice in localStorage).
- [ ] **Internationalisation** beyond the German screenshot — pull strings into a JSON dictionary, add `de`/`fr`/`es`.
- [ ] **Permalinks** for every view: the time‑travel slider, the heatmap pair, the family graph path — encode in the URL hash so links survive a refresh and are shareable.
- [ ] **Mobile polish**: the table tab and the family graph are unusable below ~600 px. At minimum: collapsible filter bar, swipe between tabs, a "graph too dense, please use desktop" notice.
- [ ] **Keyboard navigation**: `j`/`k` to move through rows, `/` to focus the search box, `←`/`→` to nudge the time‑travel slider one day, `Esc` to close modals.
- [ ] **Profile share image**: generate an OG image per person (rank, net worth, avatar) so links unfurl nicely on Twitter / Slack.
- [ ] **In‑chart annotations**: when news markers cluster, group them and show a "5 events on this day" pill rather than overlapping dots.
- [ ] **Compare more than two people** on the wealth‑over‑time chart (currently capped at the pair panel).
- [ ] **Saved views** — let the user pin a particular filter combo to the nav.

### Network graph
- [ ] **Community detection** (Louvain / Leiden) — colour the network by community so the tech families, the Indian conglomerates, the European retail dynasties etc. visually separate.
- [ ] **Edge weighting by recency** — a 1970s board seat shouldn't render as thick as a current spouse.
- [ ] **Hover ‑> mini profile** instead of "click to open panel"; reduces friction during exploration.
- [ ] **Export the subgraph** as a `.graphml` / `.gexf` for Gephi nerds.

---

## 🛠 Performance & infrastructure

- [ ] **Move off SQLite for the production instance**: DuckDB for analytics queries, or Postgres if a writer story is needed. The 1.8 M‑row `wealth_history` joins are already CPU‑bound on cold cache.
- [ ] **Materialised views** for the heaviest insights (`top_over_time_series`, `concentration`, `wealth_correlation`) — refresh on the post‑scrape job, kill the on‑disk JSON cache hack.
- [ ] **Replace polling with WebSockets / SSE** for the scraper panel — the 1.5 s polling loops are 90 % of the in‑flight requests on a busy session.
- [ ] **HTTP cache headers** (`ETag`, `Last-Modified`) on the read endpoints — let the browser short‑circuit when the underlying snapshot hasn't changed.
- [ ] **CDN‑friendly asset hashing** — `static/style.css` is 1.4 k lines and shipped unminified on every request.
- [ ] **Health endpoint** that's actually meaningful (last successful scrape age, DB write‑ahead size, scheduler liveness) for uptime monitoring.
- [ ] **Prometheus / OpenTelemetry metrics** — request counts, cache hit ratios, scrape durations, GDELT rate‑limit waits. Right now the only signal is `logger.info`.
- [ ] **Circuit breaker** around Wikidata, Yahoo and GDELT — when the upstream is dead, fail fast instead of stacking up 6‑hour backfills behind a wedged request.
- [ ] **Backup rotation** — a daily `VACUUM INTO` + cloud upload would prevent a single bad disk from wiping years of history.

---

## 🧹 Refactor / tech debt

- [ ] **Split `static/index.html` (2552 lines)** into per‑tab partials and load them on demand. The single‑file approach has hit its limit.
- [ ] **Split `static/style.css` (1394 lines)** into per‑component files; consider adopting CSS layers or a tiny utility set.
- [ ] **Split `app/scheduler.py` (991 lines)** into `scheduler/cron.py`, `scheduler/news.py`, `scheduler/wikidata.py`, `scheduler/bootstrap.py`. Right now retry logic, state machines and cache warmup all live together.
- [ ] **Split `app/family/queries.py` (818 lines)** into `graph.py` / `paths.py` / `metrics.py` / `profile.py`.
- [ ] **Split `app/database.py` (733 lines)** — extract `migrations.py` and `connection.py`.
- [ ] **Split `app/routes/billionaires.py` (712 lines)** into `routes/billionaires.py`, `routes/profile.py`, `routes/time_travel.py`.
- [ ] **Replace bare `except Exception:` blocks** in the scheduler with typed catches; add Sentry (or even just a `logger.error` audit log table) so silent failures stop being silent.
- [ ] **Env‑var override** for DB paths (`BLOOMBERG_DB_PATH`, `NETWORK_DB_PATH`); the hardcoded `Path("data/bloomberg.db")` is a footgun for multi‑instance deployments.
- [ ] **Stop committing the test suite to `.gitignore`** — the tests are useful documentation; either publish them or move them outside the public repo entirely.
- [ ] **Add `pyproject.toml`** with `ruff` + `black` + `mypy` config; a 30‑second lint pass would catch most of the issues found in code review.
- [ ] **Delete or repurpose stray scripts** at the repo root: `radargraph.py` looks unrelated to the project; `scrape_bloomberg.py` and `scrape_bloomberg.ipynb` duplicate logic that now lives in `app/scraper.py`.
- [ ] **Move the dozens of `*.png` screenshots** out of the repo root into `docs/screenshots/` — currently they bloat every clone by ~25 MB.

---

## ✅ Quality, testing & CI

- [ ] **Run the test suite in CI** (GitHub Actions) — the Dockerfile job currently only builds and pushes. A test stage gates `main`.
- [ ] **Add an integration test** that boots the app against a fixture DB and hits every public endpoint for a 200 + non‑empty body. Catches route‑level regressions cheaply.
- [ ] **Snapshot tests** for the bigger insights payloads — even just hashing the JSON would flag accidental schema changes.
- [ ] **Type‑check the `insights/` and `market/` packages** with mypy in strict mode (start there — they have the clearest in/out contracts).
- [ ] **Property‑based tests** (`hypothesis`) on the outlier detector and the share‑class collapser — both have non‑obvious edge cases.
- [ ] **Playwright smoke test** for the dashboard, the time‑travel slider, and the heatmap pair‑click flow. Most regressions in this app are UI‑side, not backend.

---

## 🔐 Security & ops

- [ ] **Rate‑limit the public endpoints** — the live instance is single‑tenant, but the analytics endpoints are expensive enough that a bored scraper could DoS it cheaply.
- [ ] **Login throttling** on `POST /api/scraper/auth` — currently unlimited tries.
- [ ] **Move `SCRAPER_PASSWORD` to a stronger flow** — a magic link or `passkey`/WebAuthn would be more honest for a public deployment than a shared password.
- [ ] **CSP header** — the frontend already loads from a fixed set of CDNs, so locking it down is mostly a one‑liner.
- [ ] **Audit log** for every write endpoint hit (`who`, `when`, `what`) — a single new table, useful when a future admin user is added.

---

## 📚 Documentation

- [ ] **API reference** generated from the FastAPI OpenAPI schema (it's already there at `/docs`; just link it from the README).
- [ ] **Architecture diagram** — a single Mermaid graph in `docs/architecture.md` showing Bloomberg → scraper → SQLite → enrichers → FastAPI → SPA.
- [ ] **Data dictionary** for every column in `persons`, `snapshots`, `wealth_history`, `news_articles`, `historical_rankings`, plus the two `network.db` tables. Helpful for anyone building dashboards on top.
- [ ] **Changelog / release notes** — currently the only public history is `git log`. A short `CHANGELOG.md` with "what's new" entries would help users who self‑host.
- [ ] **Contributing guide** — Python version, how to run tests locally, how to seed a dev DB without scraping live.

---

## 🪐 Wild ideas

- [ ] **"Six degrees of Elon Musk"** game mode on the network graph — pick a random pair, race the clock to find the connecting path.
- [ ] **Twitter / Mastodon bot** that posts the day's biggest mover with the wealth chart as an image at 18:00 CET.
- [ ] **LLM‑written narrative** for each profile: feed the snapshot + news timeline to a model and generate a 2‑paragraph "what's happening with this person right now" summary.
- [ ] **Treemap zoom into history** — animate a country/industry treemap year by year, 2001 → today, with smooth tweens.
- [ ] **"What if" sandbox** — let the user delete a billionaire and watch the rest of the rankings shift; useful for thinking about concentration counterfactuals.
