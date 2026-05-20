// static/js/tabs/analytics.js
function analyticsMixin() {
    return {
        chartColors: CHART_COLORS,
        chartInstances: {},
        wealthHistories: {},
        wealthRange: '6M',
        wealthChart: null,
        concentrationData: null,
        concentrationRange: '5Y',
        selectedPeople: [],
        personQuery: '',
        searchResults: [],

        async loadAnalytics() {
            const [indRes, cntRes, demoRes] = await Promise.all([
                fetch('/api/analytics/by-industry').then(r => r.json()),
                fetch('/api/analytics/by-country').then(r => r.json()),
                fetch('/api/analytics/demographics').then(r => r.json()),
            ]);
            renderBarChart(this.chartInstances, 'industryChart', indRes.slice(0, 8), 'industry', 'total_wealth');
            renderBarChart(this.chartInstances, 'countryChart', cntRes.slice(0, 8), 'country', 'total_wealth');
            renderDoughnut(this.chartInstances, 'genderChart', demoRes.gender);
            renderAgeChart(this.chartInstances, 'ageChart', demoRes.age_distribution);
            if (!this.concentrationData) {
                this.concentrationData = await fetch('/api/analytics/concentration').then(r => r.json());
            }
            this.renderConcentrationChart();
        },

        setConcentrationRange(range) {
            this.concentrationRange = range;
            this.renderConcentrationChart();
        },

        concentrationCutoff(range) {
            const now = new Date();
            const d = new Date(now);
            if (range === '1Y') d.setFullYear(d.getFullYear() - 1);
            else if (range === '3Y') d.setFullYear(d.getFullYear() - 3);
            else if (range === '5Y') d.setFullYear(d.getFullYear() - 5);
            else if (range === '10Y') d.setFullYear(d.getFullYear() - 10);
            else return null;
            return d.toISOString().split('T')[0];
        },

        renderConcentrationChart() {
            const ctx = document.getElementById('concentrationChart');
            if (!ctx || !this.concentrationData) return;
            const cutoff = this.concentrationCutoff(this.concentrationRange);
            const filtered = cutoff ? this.concentrationData.filter(d => d.date >= cutoff) : this.concentrationData;
            const labels = filtered.map(d => d.date);
            const top1Pct = filtered.map(d => d.total ? (d.top_1 / d.total) * 100 : 0);
            const top10Pct = filtered.map(d => d.total ? (d.top_10 / d.total) * 100 : 0);
            const top100Pct = filtered.map(d => d.total ? (d.top_100 / d.total) * 100 : 0);
            if (this.chartInstances.concentrationChart) this.chartInstances.concentrationChart.destroy();
            this.chartInstances.concentrationChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels,
                    datasets: [
                        { label: 'Top 100', data: top100Pct, borderColor: '#6c5ce7', fill: false, tension: 0.1, pointRadius: 0 },
                        { label: 'Top 10', data: top10Pct, borderColor: '#4ecdc4', fill: false, tension: 0.1, pointRadius: 0 },
                        { label: 'Top 1', data: top1Pct, borderColor: '#ff6b6b', fill: false, tension: 0.1, pointRadius: 0 },
                    ],
                },
                options: {
                    responsive: true,
                    animation: false,
                    scales: {
                        x: { type: 'category', ticks: { maxTicksLimit: 12 } },
                        y: { ticks: { callback: v => v.toFixed(0) + '%' } },
                    },
                },
            });
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
            const fresh = {};
            for (const p of this.selectedPeople) {
                fresh[p.person_id] = this.wealthHistories[p.person_id]
                    || await fetch(`/api/billionaires/${p.person_id}/history`).then(r => r.json());
            }
            this.wealthHistories = fresh;
            this.renderWealthChart();
        },

        setWealthRange(range) {
            this.wealthRange = range;
            this.renderWealthChart();
        },

        renderWealthChart() {
            const datasets = this.selectedPeople.map((p, i) => {
                const history = this.wealthHistories[p.person_id] || [];
                const filtered = filterRange(history, this.wealthRange);
                return {
                    label: p.common_name,
                    data: filtered.map(h => ({ x: h.scraped_at, y: h.net_worth_usd })),
                    borderColor: this.chartColors[i % this.chartColors.length],
                    fill: false, tension: 0.1, pointRadius: 0,
                };
            });
            const ctx = document.getElementById('wealthChart');
            if (this.wealthChart) this.wealthChart.destroy();
            this.wealthChart = new Chart(ctx, {
                type: 'line',
                data: { datasets },
                options: {
                    responsive: true,
                    animation: false,
                    scales: {
                        x: { type: 'category' },
                        y: { ticks: { callback: v => formatWealth(v) } },
                    },
                },
            });
        },
    };
}
