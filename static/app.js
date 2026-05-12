// static/app.js
function app() {
    return {
        tab: 'dashboard',
        dashboard: { total_wealth: 0, count: 0, snapshots: 0, latest_scrape: null },
        tableData: { data: [], total: 0 },
        tableFilters: { q: '', country: '', industry: '', gender: '', sort: 'rank' },
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
        chartInstances: {},
        exportScope: 'latest',
        exportFormat: 'csv',
        exportFrom: '',
        exportTo: '',
        masterFields: [],
        allFields: [],
        defaultFields: [],
        fieldsByGroup: {
            identity: ['person_id', 'common_name', 'full_name', 'first_name', 'last_name', 'middle_name', 'slug'],
            demographics: ['citizenship', 'age', 'birth_year', 'gender', 'gender_confidence'],
            financial: ['scraped_at', 'rank', 'net_worth_usd', 'last_change_usd', 'last_change_pct', 'ytd_change_usd', 'ytd_change_pct'],
            assets: ['public_assets_total', 'private_assets_total', 'cash_assets_total', 'public_assets_json', 'private_assets_json', 'cash_asset_value', 'liabilities_value', 'liabilities_note'],
            personal: ['industry', 'sector', 'biography', 'overview', 'net_worth_summary', 'schools_json', 'facts_json', 'milestones_json'],
            metadata: ['confidence'],
        },

        async init() {
            const [dashRes, statusRes, fieldsRes] = await Promise.all([
                fetch('/api/dashboard').then(r => r.json()),
                fetch('/api/scraper/status').then(r => r.json()),
                fetch('/api/export/fields').then(r => r.json()),
            ]);
            this.dashboard = dashRes;
            this.scraperStatus = statusRes;
            this.allFields = fieldsRes.fields;
            this.defaultFields = fieldsRes.defaults;
            this.masterFields = [...fieldsRes.defaults];
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
            this.tableData = await fetch(`/api/billionaires?${params}`).then(r => r.json());
        },

        sortTable(col) {
            if (this.tableFilters.sort === col) {
                this.tableFilters.sort = `-${col}`;
            } else {
                this.tableFilters.sort = col;
            }
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
            if (this.chartInstances[canvasId]) this.chartInstances[canvasId].destroy();
            this.chartInstances[canvasId] = new Chart(ctx, {
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
            if (this.chartInstances[canvasId]) this.chartInstances[canvasId].destroy();
            this.chartInstances[canvasId] = new Chart(ctx, {
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
            if (this.chartInstances[canvasId]) this.chartInstances[canvasId].destroy();
            this.chartInstances[canvasId] = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: data.map(d => d.bracket),
                    datasets: [{ data: data.map(d => d.count), backgroundColor: '#4ecdc4' }],
                },
                options: { responsive: true, plugins: { legend: { display: false } } },
            });
        },

        exportHref() {
            const params = new URLSearchParams();
            params.set('scope', this.exportScope);
            if (this.exportScope === 'range') {
                params.set('from_date', this.exportFrom);
                params.set('to_date', this.exportTo);
            }
            if (this.masterFields.length > 0 && this.masterFields.length < this.allFields.length) {
                params.set('fields', this.masterFields.join(','));
            }
            const ext = this.exportFormat === 'json' ? 'json' : 'csv';
            return `/api/export/bloomberg_billionaires.${ext}?${params}`;
        },

        selectAllFields() {
            this.masterFields = [...this.allFields];
        },

        selectDefaultFields() {
            this.masterFields = [...this.defaultFields];
        },

        deselectAllFields() {
            this.masterFields = [];
        },

        fieldLabel(f) {
            return f.replace(/_/g, ' ').replace(/\bjson\b/g, '(JSON)').replace(/\busd\b/g, '(USD)').replace(/\bpct\b/g, '(%)');
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
