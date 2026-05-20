// static/js/tabs/scraper.js
function scraperMixin() {
    return {
        scraperStatus: { status: 'idle', next_run: null, last_success: null },
        scraperRuns: [],
        backfill: { running: false, done: 0, total: 0, errors: 0, coverage: { persons: 0, rows: 0 } },
        backfillTimer: null,
        syncing: false,
        syncMessage: '',
        schedule: { times: ['08:00'], timezone: 'UTC', enabled: true },

        async loadScraper() {
            const [statusRes, runsRes, schedRes, backfillRes] = await Promise.all([
                fetch('/api/scraper/status').then(r => r.json()),
                fetch('/api/scraper/runs').then(r => r.json()),
                fetch('/api/scraper/schedule').then(r => r.json()),
                fetch('/api/scraper/backfill-history').then(r => r.json()),
            ]);
            this.scraperStatus = statusRes;
            this.scraperRuns = runsRes;
            this.schedule = schedRes;
            this.backfill = backfillRes;
            if (backfillRes.running && !this.backfillTimer) {
                this.backfillTimer = setInterval(async () => {
                    this.backfill = await fetch('/api/scraper/backfill-history').then(r => r.json());
                    if (!this.backfill.running) {
                        clearInterval(this.backfillTimer);
                        this.backfillTimer = null;
                    }
                }, 3000);
            }
        },

        async triggerScrape() {
            await fetch('/api/scraper/run', { method: 'POST' });
            this.scraperStatus.status = 'running';
            setTimeout(() => this.loadScraper(), 5000);
        },

        async triggerBackfill() {
            await fetch('/api/scraper/backfill-history', { method: 'POST' });
            this.loadScraper();
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

        nextRunForTime(timeStr) {
            const [h, m] = timeStr.split(':').map(Number);
            const now = new Date();
            const next = new Date();
            next.setHours(h, m, 0, 0);
            if (next <= now) next.setDate(next.getDate() + 1);
            return next.toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
        },
    };
}
