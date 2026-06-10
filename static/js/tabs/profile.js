// static/js/tabs/profile.js
// Full-page profile view. Hits /api/persons/{id}/profile and renders the
// combined Bloomberg + family + entity payload. Routed via #tab=profile&id=N.
function profileMixin() {
    return {
        profile: null,
        profileChart: null,
        // Wealth-composition donut on the Assets card. Separate from
        // profileChart (which is the time series) so opening / closing
        // the profile doesn't fight Chart.js for the same canvas.
        profileAssetsDonutChart: null,
        profileRange: 'ALL',
        // News markers on/off — persists in localStorage so the user's
        // preference holds across page reloads. Default ON (most users
        // want to see news context next to wealth movements).
        profileShowNews: localStorage.getItem('profileShowNews') !== '0',
        profileSchools: [],
        profileMilestones: [],
        profileFacts: [],
        profileAssets: { public: [], private: [] },
        // News card state. Defaults: All years, first 20 shown, "show more"
        // expands by 20 each time.
        profileNewsYearFilter: 'all',
        profileNewsLimit: 20,
        // Set to true while a background news fetch is in flight for this
        // profile — UI shows a "Fetching news…" spinner instead of an empty
        // state. Cleared when poll sees articles or after a timeout.
        profileNewsFetching: false,
        _profileNewsPollHandle: null,

        async loadProfile(personId) {
            if (!personId) return;
            this.profile = null;
            this.profileNewsYearFilter = 'all';
            this.profileNewsLimit = 20;
            // Clear any previous poll if we're hopping between profiles.
            if (this._profileNewsPollHandle) {
                clearInterval(this._profileNewsPollHandle);
                this._profileNewsPollHandle = null;
            }
            const data = await fetch(`/api/persons/${personId}/profile`).then(r => r.json());
            if (data.error) { this.profile = { error: data.error }; return; }
            this.profile = data;
            // If the server kicked off a background news fetch, poll the
            // profile every few seconds until news arrives so the user sees
            // articles appear without manually reloading.
            if (data.news_fetch_pending) {
                this.profileNewsFetching = true;
                this.startNewsPoll(personId);
            } else {
                this.profileNewsFetching = false;
            }
            this.profileSchools = data.schools_json ? JSON.parse(data.schools_json) : [];
            this.profileMilestones = data.milestones_json ? JSON.parse(data.milestones_json) : [];
            this.profileFacts = data.facts_json ? JSON.parse(data.facts_json) : [];
            this.profileAssets = {
                public: data.public_assets_json ? JSON.parse(data.public_assets_json) : [],
                private: data.private_assets_json ? JSON.parse(data.private_assets_json) : [],
            };
            this.$nextTick(() => {
                this.renderProfileChart();
                this.renderProfileAssetsDonut();
            });
        },

        setProfileRange(range) {
            this.profileRange = range;
            this.renderProfileChart();
        },

        toggleProfileNews() {
            this.profileShowNews = !this.profileShowNews;
            localStorage.setItem('profileShowNews', this.profileShowNews ? '1' : '0');
            this.renderProfileChart();
        },

        renderProfileChart() {
            if (!this.profile || !this.profile.history) return;
            const ctx = document.getElementById('profileChart');
            if (!ctx) return;
            if (this.profileChart) this.profileChart.destroy();
            const points = (this.profile.history || []).map(h => ({
                x: h.date || h.scraped_at, y: h.net_worth_usd,
            }));
            const filtered = filterRange(points.map(p => ({ scraped_at: p.x, net_worth_usd: p.y })),
                                          this.profileRange);

            // News markers ride the wealth curve. Their y-coordinate is the
            // wealth at the article date — that one is real (the article was
            // published on a specific day, and we have the wealth that day).
            // Bloomberg milestones are NOT shown here: they're year-only and
            // pinning them to a synthetic mid-year wealth value would imply
            // a connection that doesn't exist. The "Milestones" card lists
            // them as a year-indexed timeline instead.
            const newsMarkers = this.profileShowNews
                ? this.profileNewsMarkers(filtered)
                : [];

            const datasets = [{
                label: 'Net worth',
                data: filtered.map(f => ({ x: f.scraped_at, y: f.net_worth_usd })),
                borderColor: '#4ecdc4',
                backgroundColor: 'rgba(78, 205, 196, 0.15)',
                fill: true, tension: 0.1, pointRadius: 0, borderWidth: 2,
                order: 2,
            }];
            if (newsMarkers.length) {
                datasets.push({
                    type: 'scatter',
                    label: 'News',
                    data: newsMarkers.map(n => ({ x: n.x, y: n.y, title: n.title, url: n.url, source: n.source, date: n.date })),
                    backgroundColor: '#f39c12',
                    borderColor: '#fff',
                    borderWidth: 1,
                    pointRadius: 4,
                    pointHoverRadius: 8,
                    // Slightly narrower catch zone: the previous 25 was
                    // wide enough that scrolling along the wealth line
                    // would constantly snap into a news marker even when
                    // hovering 3 weeks away from one. ~14 keeps the dot
                    // forgiving without feeling sticky.
                    pointHitRadius: 14,
                    showLine: false,
                    order: 0,
                });
            }

            this.profileChart = new Chart(ctx, {
                type: 'line',
                data: { datasets },
                options: {
                    responsive: true, maintainAspectRatio: false, animation: false,
                    // 'x' mode picks up every point that overlaps the
                    // cursor's x — including markers whose pointHitRadius
                    // widens the catch zone. Hovering anywhere in that
                    // x-range shows wealth + nearby milestones/news at once.
                    interaction: { mode: 'x', intersect: false },
                    onClick: (evt, els) => {
                        // Click a news dot → open the article in a new tab.
                        const news = els.find(e =>
                            this.profileChart.data.datasets[e.datasetIndex].label === 'News'
                        );
                        if (!news) return;
                        const item = this.profileChart.data.datasets[news.datasetIndex].data[news.index];
                        if (item?.url) window.open(item.url, '_blank', 'noopener');
                    },
                    onHover: (evt, els) => {
                        // Pointer cursor when hovering any marker — gives the
                        // user a visual cue that News dots are clickable.
                        const target = evt.native?.target;
                        if (target) {
                            target.style.cursor = els.length ? 'pointer' : 'default';
                        }
                    },
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            // The wealth dataset has daily points; with
                            // `mode: 'x'` Chart.js can pick up multiple
                            // adjacent days that share the same screen
                            // pixel and emit two near-identical wealth
                            // rows. We dedupe to one wealth row per
                            // tooltip render. News rows are kept as-is
                            // (they're title-only, no wealth duplication).
                            filter: (item, _idx, items) => {
                                if (item.dataset.label !== 'Net worth') return true;
                                const firstWealthIdx = items.findIndex(
                                    i => i.dataset.label === 'Net worth'
                                );
                                return _idx === firstWealthIdx;
                            },
                            callbacks: {
                                title: items => {
                                    const item = items[0];
                                    if (item.dataset.label === 'News') {
                                        return item.raw.date + ' — news';
                                    }
                                    return item.label;
                                },
                                label: c => {
                                    if (c.dataset.label === 'News') {
                                        const src = c.raw.source ? ` (${c.raw.source})` : '';
                                        return c.raw.title + src;
                                    }
                                    return formatWealth(c.parsed.y);
                                },
                            },
                        },
                    },
                    scales: {
                        x: { type: 'category', ticks: { maxTicksLimit: 8 } },
                        y: { ticks: { callback: v => formatWealth(v) } },
                    },
                },
            });
        },

        // Wealth composition donut: public / private / cash slice.
        // Liabilities NOT included — they reduce net worth but
        // aren't a positive segment of the asset pie. Mirrors the
        // helper used in the pair-comparison panel for consistency.
        profileAssetSegments(p) {
            // Bloomberg's `net_worth_usd` is their proprietary Billionaires
            // Index estimate. Public/Private/Cash totals are the disclosed
            // breakdown but they often don't sum to net_worth — the gap is
            // unallocated value (executive comp packages, modelled estimates,
            // recently-revised holdings not yet in the per-asset table).
            // We surface that gap as an "Other" segment so the donut sums to
            // the headline net-worth figure and the user isn't left wondering
            // where the missing tens of billions are.
            if (!p) return [];
            const pub = p.public_assets_total || 0;
            const priv = p.private_assets_total || 0;
            const cash = p.cash_assets_total || 0;
            const disclosed = pub + priv + cash;
            // Net of liabilities — that's what `net_worth_usd` reflects.
            const liab = p.liabilities_value || 0;
            const netWorth = p.net_worth_usd || 0;
            // gap = the slice of net worth NOT explained by disclosed
            // assets (after adding liabilities back, since liabilities
            // already reduced net_worth). Floor at 0 — we don't render
            // a negative segment when our totals overshoot.
            const gap = Math.max(0, netWorth + liab - disclosed);
            // If everything we have sums to ~net_worth (within 2%), don't
            // bother showing a tiny rounding sliver.
            const showGap = gap > 0.02 * Math.max(netWorth, 1);
            const segs = [
                { label: 'Public',  color: '#5ad1c7', value: pub },
                { label: 'Private', color: '#a37fdc', value: priv },
                { label: 'Cash',    color: '#6ec1e4', value: cash },
            ];
            if (showGap) {
                segs.push({
                    label: 'Other',
                    color: '#bdc3c7',
                    value: gap,
                });
            }
            const total = segs.reduce((s, x) => s + x.value, 0);
            if (!total) return [];
            return segs
                .filter(s => s.value > 0)
                .map(s => ({ ...s, share: s.value / total }));
        },

        renderProfileAssetsDonut() {
            const segs = this.profileAssetSegments(this.profile);
            if (!segs.length) return;
            const canvas = document.getElementById('profileAssetsDonut');
            if (!canvas) return;
            // Defer when not yet painted (Alpine x-show race).
            if (canvas.clientWidth === 0 || canvas.clientHeight === 0) {
                requestAnimationFrame(() => this.renderProfileAssetsDonut());
                return;
            }
            // Pin canvas size manually — Chart.js + flex-parent =
            // squished donut on first paint when responsive=true.
            const SIZE = 200;
            const dpr = window.devicePixelRatio || 1;
            canvas.style.width  = `${SIZE}px`;
            canvas.style.height = `${SIZE}px`;
            canvas.width  = SIZE * dpr;
            canvas.height = SIZE * dpr;
            if (this.profileAssetsDonutChart) this.profileAssetsDonutChart.destroy();
            this.profileAssetsDonutChart = new Chart(canvas, {
                type: 'doughnut',
                data: {
                    labels: segs.map(s => s.label),
                    datasets: [{
                        data: segs.map(s => s.value),
                        backgroundColor: segs.map(s => s.color),
                        borderColor: '#fff',
                        borderWidth: 2,
                    }],
                },
                options: {
                    responsive: false,
                    maintainAspectRatio: true,
                    devicePixelRatio: dpr,
                    cutout: '60%',
                    animation: { duration: 250 },
                    plugins: {
                        legend: { display: false }, // we render our own
                        tooltip: {
                            callbacks: {
                                label: (ctx) => {
                                    const total = ctx.dataset.data.reduce((a, b) => a + b, 0);
                                    const pct = total ? (ctx.parsed / total * 100).toFixed(1) : 0;
                                    return `${ctx.label}: ${formatWealth(ctx.parsed)} (${pct}%)`;
                                },
                            },
                        },
                    },
                },
            });
        },

        profileNewsMarkers(filtered) {
            // Only day-precision news rides the chart curve. Year/month-only
            // citations have placeholder days (YYYY-06-15) that would land
            // on a wealth value that was never real for that date — those
            // stay in the news card list, not on the chart.
            const news = (this.profile?.news || []).filter(
                n => (n.date_precision || 'day') === 'day'
            );
            if (!news.length || !filtered?.length) return [];
            const dates = filtered.map(f => ({
                x: f.scraped_at,
                y: f.net_worth_usd,
                t: new Date(f.scraped_at).getTime(),
            }));
            const minT = dates[0].t;
            const maxT = dates[dates.length - 1].t;
            const TOP_PER_YEAR = 3;
            const byYear = {};
            for (const n of news) {
                const t = new Date(n.article_date).getTime();
                if (isNaN(t) || t < minT || t > maxT) continue;
                const year = n.article_date.slice(0, 4);
                (byYear[year] = byYear[year] || []).push({ ...n, t });
            }
            const markers = [];
            for (const year of Object.keys(byYear)) {
                const sorted = byYear[year].sort((a, b) => (b.importance || 0) - (a.importance || 0));
                for (const n of sorted.slice(0, TOP_PER_YEAR)) {
                    let best = dates[0];
                    let bestDelta = Math.abs(dates[0].t - n.t);
                    for (const d of dates) {
                        const delta = Math.abs(d.t - n.t);
                        if (delta < bestDelta) { best = d; bestDelta = delta; }
                    }
                    markers.push({
                        x: best.x, y: best.y,
                        title: n.title, url: n.url,
                        source: n.source, date: n.article_date,
                    });
                }
            }
            return markers;
        },

        openProfile(personId) {
            // Setting `tab` triggers the $watch which pushes a history
            // entry; we do that first, then patch in the id with replace
            // so back-button traversal hits the previous tab cleanly.
            this.tab = 'profile';
            this.updateHash({ id: personId, person: null, entity: null });
            this.panelOpen = false;
            this.entityPanelOpen = false;
            this.loadProfile(personId);
        },

        profileGoBack() {
            // If we got here via in-app navigation, the browser has a
            // history entry to go back to. If the user landed on a
            // profile URL directly (deep-link, refresh, or shared link),
            // there's nothing to go back to — fall back to the table.
            //
            // We detect "no history entry to go back to" via the
            // `pageshow` flag set on init; simpler: try history.back()
            // and after a tick check if we're still on profile, then
            // route to table.
            if (history.length > 1) {
                history.back();
                // Guard: if back() didn't change the tab within 250ms
                // (e.g. history was a same-tab profile-id swap that
                // looped us right back), nudge to table.
                setTimeout(() => {
                    if (this.tab === 'profile') this.tab = 'table';
                }, 250);
            } else {
                this.tab = 'table';
            }
        },

        profileFamilyByKind() {
            if (!this.profile || !this.profile.family) return {};
            const grouped = {};
            for (const f of this.profile.family) {
                (grouped[f.kind] = grouped[f.kind] || []).push(f);
            }
            return grouped;
        },

        profileEntitiesByKind() {
            if (!this.profile || !this.profile.entity_links) return {};
            const grouped = {};
            for (const e of this.profile.entity_links) {
                const k = e.entity_kind || 'other';
                (grouped[k] = grouped[k] || []).push(e);
            }
            return grouped;
        },

        profileAgeDisplay() {
            // Use age-at-death when we have both DOB and DOD, otherwise fall
            // back to Bloomberg's current age. Returns null when neither works.
            const meta = this.profile?.wikidata_metadata || {};
            if (meta.birth_date && meta.death_date) {
                const b = new Date(meta.birth_date);
                const d = new Date(meta.death_date);
                if (!isNaN(b) && !isNaN(d)) {
                    let age = d.getFullYear() - b.getFullYear();
                    const m = d.getMonth() - b.getMonth();
                    if (m < 0 || (m === 0 && d.getDate() < b.getDate())) age--;
                    return age;
                }
            }
            return this.profile?.age ?? null;
        },

        profileDetailLine() {
            const p = this.profile || {};
            const age = this.profileAgeDisplay();
            const meta = p.wikidata_metadata || {};
            const isDeceased = !!meta.death_date;
            const parts = [];
            if (p.is_active && p.rank) parts.push('#' + p.rank);
            else if (!p.is_active && p.rank) parts.push('Former #' + p.rank);
            if (p.citizenship) parts.push(p.citizenship);
            if (age != null) parts.push(isDeceased ? `Age ${age} at death` : `Age ${age}`);
            if (p.industry) parts.push(p.industry);
            return parts.join(' · ');
        },

        profileNewsYears() {
            // Distinct years across all loaded news, newest first. Used to
            // build the year-tab filter on the news card.
            const news = this.profile?.news || [];
            const years = new Set();
            for (const n of news) {
                if (n.article_date) years.add(n.article_date.slice(0, 4));
            }
            return [...years].sort((a, b) => b.localeCompare(a));
        },

        profileNewsRangeLabel() {
            // "1999 — 2026 · 200 articles" subtitle for the news card so
            // visitors don't assume the data is recent-only.
            const news = this.profile?.news || [];
            if (!news.length) return '';
            const years = this.profileNewsYears();
            if (!years.length) return `${news.length} articles`;
            const lo = years[years.length - 1];
            const hi = years[0];
            const range = lo === hi ? lo : `${lo} — ${hi}`;
            return `${range} · ${news.length} articles`;
        },

        profileNewsForCard() {
            // Apply year filter then limit. When filter is 'all' we show
            // newest-first; when filtered to one year we surface the most
            // important stories in that year first since the user likely
            // wants the highlights, not every routine mention.
            const news = this.profile?.news || [];
            let filtered = news;
            if (this.profileNewsYearFilter !== 'all') {
                filtered = news.filter(
                    n => n.article_date?.startsWith(this.profileNewsYearFilter)
                );
                filtered = [...filtered].sort(
                    (a, b) => (b.importance || 0) - (a.importance || 0),
                );
            }
            return filtered.slice(0, this.profileNewsLimit);
        },

        profileNewsHasMore() {
            const news = this.profile?.news || [];
            const total = this.profileNewsYearFilter === 'all'
                ? news.length
                : news.filter(n => n.article_date?.startsWith(this.profileNewsYearFilter)).length;
            return total > this.profileNewsLimit;
        },

        formatNewsDate(n) {
            // Respect the citation's stated precision: "2018", "March 2018",
            // or "2018-03-15". The placeholder-day value (06-15 or DD=15)
            // would be misleading if shown verbatim.
            if (!n?.article_date) return '';
            const p = n.date_precision || 'day';
            if (p === 'year') return n.article_date.slice(0, 4);
            if (p === 'month') return n.article_date.slice(0, 7);
            return n.article_date;
        },

        profileTrackingStart() {
            // First date Bloomberg has wealth history for this person —
            // shown as a chart footnote so readers understand the chart's
            // left edge isn't "when their fortune began".
            const h = this.profile?.history;
            if (!h?.length) return null;
            const first = h[0];
            const d = first.date || first.scraped_at;
            if (!d) return null;
            // Format as a human date; locale-aware "Aug 9, 2012".
            try {
                return new Date(d).toLocaleDateString(undefined, {
                    year: 'numeric', month: 'short', day: 'numeric',
                });
            } catch (_) {
                return d.slice(0, 10);
            }
        },

        profileRangeChange() {
            // Net worth gain/loss across the currently-selected range.
            // Returns { absolute, percent, from, to, fromDate, toDate } or
            // null if the range has fewer than two points. Useful as a
            // headline number — "Musk: +57% over 1Y" — without making
            // users squint at the chart endpoints.
            const h = this.profile?.history;
            if (!h?.length) return null;
            const points = h.map(p => ({
                scraped_at: p.date || p.scraped_at,
                net_worth_usd: p.net_worth_usd,
            }));
            const filtered = filterRange(points, this.profileRange);
            if (filtered.length < 2) return null;
            const from = filtered[0].net_worth_usd;
            const to = filtered[filtered.length - 1].net_worth_usd;
            if (!from) return null;
            return {
                absolute: to - from,
                percent: ((to - from) / Math.abs(from)) * 100,
                from, to,
                fromDate: filtered[0].scraped_at,
                toDate: filtered[filtered.length - 1].scraped_at,
            };
        },

        startNewsPoll(personId) {
            // Backfill takes ~1-3s for a single Wikipedia fetch. Poll every
            // 4s for up to 60s. Stop as soon as the news array is non-empty
            // — at that point articles flow into the card naturally via
            // Alpine reactivity.
            const start = Date.now();
            this._profileNewsPollHandle = setInterval(async () => {
                if (Date.now() - start > 60000 || this.profile?.person_id !== personId) {
                    clearInterval(this._profileNewsPollHandle);
                    this._profileNewsPollHandle = null;
                    this.profileNewsFetching = false;
                    return;
                }
                try {
                    const data = await fetch(`/api/persons/${personId}/profile`).then(r => r.json());
                    if (data.news?.length) {
                        this.profile = data;
                        this.profileNewsFetching = false;
                        clearInterval(this._profileNewsPollHandle);
                        this._profileNewsPollHandle = null;
                        this.$nextTick(() => this.renderProfileChart());
                    }
                } catch (_) { /* swallow — next tick retries */ }
            }, 4000);
        },

        async refreshProfileNews() {
            // Manual "Refresh news" button — re-runs the backfill+refresh
            // for this person even if they're already marked backfilled.
            const id = this.profile?.person_id;
            if (!id) return;
            this.profileNewsFetching = true;
            try {
                await fetch(`/api/persons/${id}/refresh-news`, { method: 'POST' });
                this.startNewsPoll(id);
            } catch (_) {
                this.profileNewsFetching = false;
            }
        },

        profileOutOfRangeCount() {
            // Count news with importance ≥ threshold that fall outside the
            // currently-selected range. Drives the "X events outside this
            // range" hint so users don't miss historical highlights when
            // they're zoomed in. Milestones aren't counted — they're shown
            // in their own card with year-only resolution.
            if (this.profileRange === 'ALL' || !this.profile) return 0;
            const cutoff = rangeCutoff(this.profileRange);
            if (!cutoff) return 0;
            const cutoffT = cutoff.getTime();
            const NEWS_THRESHOLD = 8;
            let count = 0;
            for (const n of this.profile.news || []) {
                if ((n.importance || 0) < NEWS_THRESHOLD) continue;
                const t = new Date(n.article_date).getTime();
                if (!isNaN(t) && t < cutoffT) count++;
            }
            return count;
        },

        profileEntityIdByName(name) {
            // Match a free-text name (Bloomberg school, private asset, etc.)
            // against the Wikidata entity_links so the surrounding card can
            // deep-link to the entity panel when there's a confident match.
            // Loose match: exact (case-insensitive) or one name is a substring
            // of the other. Returns the entity_id or null.
            if (!name || !this.profile?.entity_links) return null;
            const target = name.toLowerCase();
            for (const link of this.profile.entity_links) {
                const candidate = (link.name || '').toLowerCase();
                if (!candidate) continue;
                if (candidate === target) return link.entity_id;
                if (target.includes(candidate) || candidate.includes(target)) return link.entity_id;
            }
            return null;
        },
    };
}
