// static/js/tabs/scraper.js
// Single control surface for every data-loading action: daily Bloomberg
// scrape, the one-time backfills (wealth history, Wikipedia news,
// Forbes via Wikipedia, Forbes via Kaggle, Wikidata network refresh),
// and the cron schedule.
function scraperMixin() {
    return {
        scraperStatus: { status: 'idle', next_run: null, last_success: null },
        scraperRuns: [],
        backfill: { running: false, done: 0, total: 0, errors: 0, coverage: { persons: 0, rows: 0 } },
        backfillTimer: null,
        syncing: false,
        syncMessage: '',
        schedule: { times: ['08:00'], timezone: 'UTC', enabled: true },

        // ─── New: jobs added since the original Scraper panel ──────────
        // News refresh — pulls GDELT news for the last 30d
        newsRefresh: { running: false, done: 0, total: 0, errors: 0, saved: 0 },
        // News backfill — Wikipedia citations historical timeline
        newsBackfill: { running: false, done: 0, total: 0, errors: 0, saved: 0, current: null },
        // Forbes Wikipedia scrape (legacy fallback) and Kaggle import
        forbesWikiBusy: false,
        forbesWikiMessage: '',
        forbesKaggleBusy: false,
        forbesKaggleMessage: '',
        // Wikidata network refresh — rebuilds family/entity/holdings graph
        networkRefresh: { running: false, stage: '', done: 0, total: 0 },
        // Bootstrap pipeline — sequentially runs every data-load job
        bootstrap: {
            running: false, step: null, step_index: 0, step_total: 0,
            started_at: null, finished_at: null, step_results: [],
        },
        _jobPollers: {},      // canvas of setInterval ids per job

        async loadScraper() {
            const [statusRes, runsRes, schedRes, backfillRes,
                   newsRefreshRes, newsBackfillRes, networkRes,
                   bootstrapRes] = await Promise.all([
                fetch('/api/scraper/status').then(r => r.json()),
                fetch('/api/scraper/runs').then(r => r.json()),
                fetch('/api/scraper/schedule').then(r => r.json()),
                fetch('/api/scraper/backfill-history').then(r => r.json()),
                fetch('/api/scraper/refresh-news').then(r => r.json()).catch(() => ({})),
                fetch('/api/scraper/backfill-news').then(r => r.json()).catch(() => ({})),
                fetch('/api/families/refresh').then(r => r.json()).catch(() => ({})),
                fetch('/api/scraper/bootstrap').then(r => r.json()).catch(() => ({})),
            ]);
            this.scraperStatus = statusRes;
            this.scraperRuns = runsRes;
            this.schedule = schedRes;
            this.backfill = backfillRes;
            this.newsRefresh = newsRefreshRes || this.newsRefresh;
            this.newsBackfill = newsBackfillRes || this.newsBackfill;
            this.networkRefresh = networkRes || this.networkRefresh;
            this.bootstrap = bootstrapRes && Object.keys(bootstrapRes).length
                ? bootstrapRes : this.bootstrap;

            // Resume polling for any already-running job
            if (backfillRes.running) this._pollBackfill();
            if (newsRefreshRes?.running) this._pollNewsRefresh();
            if (newsBackfillRes?.running) this._pollNewsBackfill();
            if (networkRes?.running) this._pollNetworkRefresh();
            if (bootstrapRes?.running) this._pollBootstrap();
        },

        async triggerScrape() {
            await fetch('/api/scraper/run', { method: 'POST' });
            this.scraperStatus.status = 'running';
            setTimeout(() => this.loadScraper(), 5000);
        },

        async triggerBackfill() {
            await fetch('/api/scraper/backfill-history', { method: 'POST' });
            this._pollBackfill();
        },

        _pollBackfill() {
            if (this._jobPollers.backfill) return;
            this._jobPollers.backfill = setInterval(async () => {
                this.backfill = await fetch('/api/scraper/backfill-history').then(r => r.json());
                if (!this.backfill.running) {
                    clearInterval(this._jobPollers.backfill);
                    delete this._jobPollers.backfill;
                }
            }, 3000);
        },

        async syncHistory() {
            this.syncing = true;
            this.syncMessage = '';
            try {
                const res = await fetch('/api/scraper/sync-history', { method: 'POST' });
                const data = await res.json();
                this.syncMessage = `Synced ${data.added.toLocaleString()} rows from snapshots.`;
                this.backfill.coverage = data.coverage;
            } finally {
                this.syncing = false;
            }
        },

        // ─── News refresh (GDELT, last 30 days) ─────────────────────────

        async triggerNewsRefresh() {
            const r = await fetch('/api/scraper/refresh-news', { method: 'POST' }).then(r => r.json());
            if (r.status === 'started') {
                this.newsRefresh.running = true;
                this._pollNewsRefresh();
            } else {
                // Already running — just sync state
                this.newsRefresh = r;
                this._pollNewsRefresh();
            }
        },

        _pollNewsRefresh() {
            if (this._jobPollers.newsRefresh) return;
            this._jobPollers.newsRefresh = setInterval(async () => {
                this.newsRefresh = await fetch('/api/scraper/refresh-news').then(r => r.json());
                if (!this.newsRefresh.running) {
                    clearInterval(this._jobPollers.newsRefresh);
                    delete this._jobPollers.newsRefresh;
                }
            }, 1500);
        },

        // ─── News backfill (Wikipedia citations) ────────────────────────

        async triggerNewsBackfill() {
            const r = await fetch('/api/scraper/backfill-news?only_new=true', { method: 'POST' }).then(r => r.json());
            if (r.status === 'started') {
                this.newsBackfill.running = true;
                this._pollNewsBackfill();
            } else {
                this.newsBackfill = r;
                this._pollNewsBackfill();
            }
        },

        _pollNewsBackfill() {
            if (this._jobPollers.newsBackfill) return;
            this._jobPollers.newsBackfill = setInterval(async () => {
                this.newsBackfill = await fetch('/api/scraper/backfill-news').then(r => r.json());
                if (!this.newsBackfill.running) {
                    clearInterval(this._jobPollers.newsBackfill);
                    delete this._jobPollers.newsBackfill;
                }
            }, 1500);
        },

        // ─── Forbes Wikipedia scrape (legacy) ──────────────────────────

        async triggerForbesWiki() {
            this.forbesWikiBusy = true;
            this.forbesWikiMessage = 'Started — runs in the background.';
            try {
                await fetch('/api/scraper/forbes-backfill?start=2002&end=2024', {
                    method: 'POST',
                });
            } catch (e) {
                this.forbesWikiMessage = 'Failed: ' + e;
            } finally {
                this.forbesWikiBusy = false;
            }
        },

        // ─── Forbes Kaggle import ──────────────────────────────────────

        async triggerForbesKaggle() {
            this.forbesKaggleBusy = true;
            this.forbesKaggleMessage = 'Downloading + importing… ';
            try {
                const r = await fetch('/api/scraper/forbes-kaggle', { method: 'POST' }).then(r => r.json());
                if (r.status === 'ok') {
                    this.forbesKaggleMessage = `Imported ${r.total_imported.toLocaleString()} rows, linked ${r.total_linked} to existing persons.`;
                } else {
                    this.forbesKaggleMessage = 'Error: ' + (r.error || 'unknown');
                }
            } catch (e) {
                this.forbesKaggleMessage = 'Failed: ' + e;
            } finally {
                this.forbesKaggleBusy = false;
            }
        },

        // ─── Network (Wikidata graph) refresh ──────────────────────────

        async triggerNetworkRefresh() {
            const r = await fetch('/api/families/refresh', { method: 'POST' }).then(r => r.json()).catch(() => null);
            if (r) this.networkRefresh = r;
            this._pollNetworkRefresh();
        },

        _pollNetworkRefresh() {
            if (this._jobPollers.networkRefresh) return;
            this._jobPollers.networkRefresh = setInterval(async () => {
                this.networkRefresh = await fetch('/api/families/refresh').then(r => r.json());
                if (!this.networkRefresh.running) {
                    clearInterval(this._jobPollers.networkRefresh);
                    delete this._jobPollers.networkRefresh;
                }
            }, 3000);
        },

        async saveSchedule() {
            const res = await fetch('/api/scraper/schedule', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(this.schedule),
            });
            if (res.ok) {
                this.schedule = await res.json();
            }
        },

        // ─── Bootstrap pipeline ─────────────────────────────────────────
        // Sequentially runs every data-load step. The backend tracks
        // per-step status; we poll every 2s while it's running.
        async triggerBootstrap() {
            await fetch('/api/scraper/bootstrap', { method: 'POST' });
            this.bootstrap.running = true;
            this._pollBootstrap();
        },

        _pollBootstrap() {
            if (this._jobPollers.bootstrap) return;
            this._jobPollers.bootstrap = setInterval(async () => {
                try {
                    this.bootstrap = await fetch('/api/scraper/bootstrap').then(r => r.json());
                } catch (e) {
                    // Server hiccup — keep polling.
                }
                if (!this.bootstrap.running) {
                    clearInterval(this._jobPollers.bootstrap);
                    delete this._jobPollers.bootstrap;
                    // Refresh sibling cards so any data the bootstrap
                    // produced shows up immediately.
                    this.loadScraper();
                }
            }, 2000);
        },

        nextRunForTime(timeStr) {
            const [h, m] = timeStr.split(':').map(Number);
            const now = new Date();
            const next = new Date();
            next.setHours(h, m, 0, 0);
            if (next <= now) next.setDate(next.getDate() + 1);
            return next.toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
        },

        // ─── Generic job progress helpers ──────────────────────────────
        // Used by the news refresh / news backfill / bootstrap cards.
        // The state shape is consistent across jobs:
        //   { running, done, total, started_at, errors?, saved?, current? }
        jobPercent(s) {
            if (!s || !s.total) return 0;
            return Math.min(100, Math.round((s.done / s.total) * 100));
        },

        jobProgressLabel(s) {
            if (!s || !s.total) return '0 / 0';
            const pct = this.jobPercent(s);
            return `${s.done.toLocaleString()} / ${s.total.toLocaleString()} (${pct}%)`;
        },

        // ETA from the elapsed time per item so far. Returns "—" until
        // we have at least 2 items done (otherwise the rate is too noisy).
        jobETA(s) {
            if (!s || !s.running || !s.started_at || !s.total || s.done < 2) {
                return '—';
            }
            const startedMs = new Date(s.started_at).getTime();
            const elapsedMs = Date.now() - startedMs;
            if (elapsedMs <= 0) return '—';
            const perItem = elapsedMs / s.done;
            const remainingMs = perItem * (s.total - s.done);
            return this.fmtDuration(remainingMs);
        },

        fmtDuration(ms) {
            if (!isFinite(ms) || ms < 0) return '—';
            const s = Math.round(ms / 1000);
            if (s < 60) return `${s}s`;
            const m = Math.round(s / 60);
            if (m < 60) return `${m}m`;
            const h = Math.floor(m / 60), mm = m % 60;
            return `${h}h ${mm}m`;
        },
    };
}
