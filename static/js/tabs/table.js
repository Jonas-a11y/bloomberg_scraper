// static/js/tabs/table.js
const TABLE_COLUMNS = [
    { key: 'rank',           label: '#',           sortable: true,  default: true },
    { key: 'common_name',    label: 'Name',        sortable: true,  default: true },
    { key: 'net_worth_usd',  label: 'Net Worth',   sortable: true,  default: true, format: 'wealth' },
    { key: 'last_change_usd',label: 'Daily',       sortable: true,  default: true, format: 'change' },
    { key: 'ytd_change_usd', label: 'YTD',         sortable: true,  default: true, format: 'change' },
    { key: 'citizenship',    label: 'Country',     sortable: false, default: true },
    { key: 'industry',       label: 'Industry',    sortable: false, default: true },
    { key: 'age',            label: 'Age',         sortable: true,  default: true },
    { key: 'gender',         label: 'Gender',      sortable: false, default: false },
    { key: 'birth_year',     label: 'Born',        sortable: false, default: false },
    { key: 'last_change_pct',label: 'Daily %',     sortable: false, default: false, format: 'percent' },
    { key: 'ytd_change_pct', label: 'YTD %',       sortable: false, default: false, format: 'percent' },
];

function tableMixin() {
    return {
        tableData: { data: [], total: 0 },
        tableFilters: { q: '', country: '', industry: '', gender: '', sort: 'rank' },
        countries: [],
        industries: [],
        tableColumns: TABLE_COLUMNS,
        visibleColumns: TABLE_COLUMNS.filter(c => c.default).map(c => c.key),
        showColumnPicker: false,
        // Time travel: when `tableAsOfDate` is non-empty we render the
        // historical ranking at that date via /api/billionaires/as-of
        // instead of the live snapshot. The slider's bounds come from the
        // data-range endpoint.
        tableAsOfDate: '',
        tableAsOfMin: '',
        tableAsOfMax: '',
        tableDiffFrom: '',
        tableDiffTo: '',
        tableDiffData: null,
        tableDiffMode: false,
        // How many rows to show in each diff column. Defaults to 6 for a
        // quick scan; "Show more" expands to 50 (the API cap).
        tableDiffShowCount: 6,

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

            // Cancel any prior fetch — the slider can quickly supersede
            // its own request and we don't want a stale response to
            // overwrite the latest. AbortController also cuts the
            // server-side query short, saving DB time.
            if (this._asOfFetchAbort) {
                this._asOfFetchAbort.abort();
            }
            const ac = new AbortController();
            this._asOfFetchAbort = ac;

            try {
                // Time-travel mode: route to /as-of when a historical date is
                // selected. Filters apply server-side either way.
                if (this.tableAsOfDate) {
                    params.set('date', this.tableAsOfDate);
                    params.set('limit', '500');
                    this.tableData = await fetch(
                        `/api/billionaires/as-of?${params}`, { signal: ac.signal },
                    ).then(r => r.json());
                } else {
                    this.tableData = await fetch(
                        `/api/billionaires?${params}`, { signal: ac.signal },
                    ).then(r => r.json());
                }
            } catch (e) {
                // Aborted by a newer request — that's expected during
                // rapid slider movement; let the latest fetch win.
                if (e.name !== 'AbortError') throw e;
            } finally {
                if (this._asOfFetchAbort === ac) this._asOfFetchAbort = null;
            }
        },

        async loadTableAsOfRange() {
            // Pull the legal range once on first table render so the slider
            // knows its min/max. Cached after first call.
            if (this.tableAsOfMin) return;
            const r = await fetch('/api/billionaires/data-range').then(r => r.json());
            this.tableAsOfMin = (r.min_date || '').slice(0, 10);
            this.tableAsOfMax = (r.max_date || '').slice(0, 10);
            // Default the diff-mode dates to "5 years ago" → "today" so the
            // diff button is immediately usable.
            if (!this.tableDiffFrom && this.tableAsOfMax) {
                const max = new Date(this.tableAsOfMax);
                const from = new Date(max);
                from.setFullYear(max.getFullYear() - 5);
                const minD = new Date(this.tableAsOfMin);
                this.tableDiffFrom = (from < minD ? minD : from)
                    .toISOString().slice(0, 10);
                this.tableDiffTo = this.tableAsOfMax;
            }
        },

        setTableAsOf(value) {
            // Commit point — used by year-preset buttons and the
            // slider's `change` event (mouseup / keyboard release).
            // Cancels any debounced fetch from `scrubTableAsOf` so we
            // don't fire twice on the same final position.
            if (this._asOfFetchTimer) {
                clearTimeout(this._asOfFetchTimer);
                this._asOfFetchTimer = null;
            }
            this.tableAsOfDate = value || '';
            this.loadTable();
        },

        // While the user drags the time-travel slider we want instant
        // visual feedback (the date label updates) but NOT a fetch on
        // every pixel — sliding from 2001 → today otherwise fires
        // hundreds of /api/billionaires/as-of requests, each scanning
        // wealth_history. Strategy:
        //   - update tableAsOfDate immediately (the date readout
        //     follows the cursor)
        //   - debounce the fetch by 250ms (one fetch when the slider
        //     stops moving for a beat)
        //   - cancel any in-flight fetch from a previous position so
        //     we don't waste socket time on results we'll discard
        _asOfFetchTimer: null,
        _asOfFetchAbort: null,
        scrubTableAsOf(value) {
            this.tableAsOfDate = value || '';
            if (this._asOfFetchTimer) {
                clearTimeout(this._asOfFetchTimer);
            }
            this._asOfFetchTimer = setTimeout(() => {
                this._asOfFetchTimer = null;
                this.loadTable();
            }, 250);
        },

        clearTableAsOf() {
            this.tableAsOfDate = '';
            this.loadTable();
        },

        async runTableDiff() {
            if (!this.tableDiffFrom || !this.tableDiffTo) return;
            this.tableDiffShowCount = 6;
            const params = new URLSearchParams({
                from_date: this.tableDiffFrom,
                to_date: this.tableDiffTo,
                top: '100',
            });
            this.tableDiffData = await fetch(`/api/billionaires/diff?${params}`).then(r => r.json());
        },

        toggleTableDiffMode() {
            this.tableDiffMode = !this.tableDiffMode;
            this.tableDiffShowCount = 6;
            if (this.tableDiffMode) this.runTableDiff();
        },

        sortTable(col) {
            this.tableFilters.sort = (this.tableFilters.sort === col) ? `-${col}` : col;
            this.loadTable();
        },

        sortIndicator(col) {
            const s = this.tableFilters.sort;
            if (s === col) return ' ↑';
            if (s === `-${col}`) return ' ↓';
            return '';
        },

        isColumnVisible(key) {
            return this.visibleColumns.includes(key);
        },

        toggleColumn(key) {
            const i = this.visibleColumns.indexOf(key);
            if (i >= 0) this.visibleColumns.splice(i, 1);
            else this.visibleColumns.push(key);
        },

        formatCell(row, col) {
            const v = row[col.key];
            if (v === null || v === undefined || v === '') return '—';
            if (col.format === 'wealth') return formatWealth(v);
            if (col.format === 'change') return formatChange(v);
            if (col.format === 'percent') return (v * 100).toFixed(2) + '%';
            return v;
        },

        cellClass(row, col) {
            if (col.format === 'change') return row[col.key] >= 0 ? 'positive' : 'negative';
            if (col.format === 'percent') return row[col.key] >= 0 ? 'positive' : 'negative';
            return '';
        },

        topMovers(direction, limit = 5) {
            const data = this.tableData.data || [];
            const valid = data.filter(d => d.last_change_usd !== null && d.last_change_usd !== undefined);
            const sorted = [...valid].sort((a, b) =>
                direction === 'up' ? b.last_change_usd - a.last_change_usd
                                   : a.last_change_usd - b.last_change_usd);
            return sorted.slice(0, limit);
        },

        // ─── Time-travel UX helpers ──────────────────────────────────────

        tableYearTicks() {
            // Year labels under the snapshot slider. We pick ~6-8 evenly
            // spaced years across the legal range so the slider has visual
            // anchors instead of looking blank.
            const min = this.tableAsOfMin || '2001-01-01';
            const max = this.tableAsOfMax || new Date().toISOString().slice(0, 10);
            const minY = parseInt(min.slice(0, 4), 10);
            const maxY = parseInt(max.slice(0, 4), 10);
            const span = maxY - minY;
            if (span <= 0) return [minY];
            const target = 7;
            const step = Math.max(1, Math.round(span / (target - 1)));
            const ticks = [];
            for (let y = minY; y <= maxY; y += step) ticks.push(y);
            // Always include the max year as the rightmost tick
            if (ticks[ticks.length - 1] !== maxY) ticks.push(maxY);
            return ticks;
        },

        tableYearPresets() {
            // Quick-jump chips. "Now" + a few historically interesting
            // anchors that the data covers.
            const max = this.tableAsOfMax || new Date().toISOString().slice(0, 10);
            const today = max;
            const presets = [
                { label: 'Now', date: '' },
                { label: '2024', date: '2024-06-01' },
                { label: '2020', date: '2020-06-01' },
                { label: '2015', date: '2015-06-01' },
                { label: '2010', date: '2010-06-01' },
                { label: '2005', date: '2005-06-01' },
            ];
            // Filter to only presets whose date is within the legal range
            const min = this.tableAsOfMin || '2001-01-01';
            return presets.filter(p => !p.date || (p.date >= min && p.date <= max));
        },

        tableComparePresets() {
            // Compare-mode quick spans. Each sets both `from` and `to`.
            const max = this.tableAsOfMax || new Date().toISOString().slice(0, 10);
            const min = this.tableAsOfMin || '2001-01-01';
            const today = new Date(max);
            const yearsAgo = (n) => {
                const d = new Date(today);
                d.setFullYear(today.getFullYear() - n);
                const iso = d.toISOString().slice(0, 10);
                return iso < min ? min : iso;
            };
            return [
                { label: '1 year', from: yearsAgo(1), to: max },
                { label: '5 years', from: yearsAgo(5), to: max },
                { label: '10 years', from: yearsAgo(10), to: max },
                { label: '20 years', from: yearsAgo(20), to: max },
                { label: 'All-time', from: min, to: max },
            ];
        },

        setComparePreset(p) {
            this.tableDiffFrom = p.from;
            this.tableDiffTo = p.to;
            this.runTableDiff();
        },

        diffHeadline() {
            // One-line summary: "Bill Gates ($40B) → Elon Musk ($726B)" so
            // the user gets the punchline before scanning columns.
            const d = this.tableDiffData;
            if (!d) return '';
            const fromYear = d.from_date?.slice(0, 4) || '';
            const toYear = d.to_date?.slice(0, 4) || '';
            const big = d.top_gainers?.[0];
            if (!big) return `${fromYear} → ${toYear}`;
            // formatChange already prepends a sign, so we don't add our own.
            return `${big.common_name}: ${formatChange(big.worth_change)} ` +
                   `(${fromYear} → ${toYear})`;
        },

        diffTotalGains() {
            const top = (this.tableDiffData?.top_gainers || []).slice(0, 10);
            const sum = top.reduce((s, r) => s + Math.max(0, r.worth_change || 0), 0);
            return formatChange(sum);
        },

        diffTotalLosses() {
            const top = (this.tableDiffData?.top_losers || []).slice(0, 10);
            const sum = top.reduce((s, r) => s + Math.min(0, r.worth_change || 0), 0);
            return formatChange(sum);
        },
    };
}
