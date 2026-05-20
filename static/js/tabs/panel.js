// static/js/tabs/panel.js
// Person detail side panel (opens from table or graph clicks).
function panelMixin() {
    return {
        panelOpen: false,
        panelPerson: null,
        panelHistory: [],
        panelRange: '6M',
        panelSchools: [],
        panelMilestones: [],
        panelFacts: [],
        panelAssets: { public: [], private: [] },
        panelChart: null,

        async openPanel(personId) {
            const [detail, history] = await Promise.all([
                fetch(`/api/billionaires/${personId}`).then(r => r.json()),
                fetch(`/api/billionaires/${personId}/history`).then(r => r.json()),
            ]);
            this.panelPerson = detail;
            this.panelHistory = history;
            this.panelSchools = detail.schools_json ? JSON.parse(detail.schools_json) : [];
            this.panelMilestones = detail.milestones_json ? JSON.parse(detail.milestones_json) : [];
            this.panelFacts = detail.facts_json ? JSON.parse(detail.facts_json) : [];
            this.panelAssets = {
                public: detail.public_assets_json ? JSON.parse(detail.public_assets_json) : [],
                private: detail.private_assets_json ? JSON.parse(detail.private_assets_json) : [],
            };
            this.panelOpen = true;
            this.$nextTick(() => this.renderPanelChart());
        },

        setPanelRange(range) {
            this.panelRange = range;
            this.renderPanelChart();
        },

        renderPanelChart() {
            const ctx = document.getElementById('panelChart');
            if (!ctx) return;
            const filtered = filterRange(this.panelHistory, this.panelRange);
            if (this.panelChart) this.panelChart.destroy();
            this.panelChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: filtered.map(h => h.scraped_at.split('T')[0]),
                    datasets: [{
                        label: 'Net Worth',
                        data: filtered.map(h => h.net_worth_usd),
                        borderColor: '#4ecdc4',
                        fill: false, tension: 0.1, pointRadius: 0,
                    }],
                },
                options: {
                    responsive: true,
                    animation: false,
                    plugins: { legend: { display: false } },
                    scales: { y: { ticks: { callback: v => formatWealth(v) } } },
                },
            });
        },
    };
}
