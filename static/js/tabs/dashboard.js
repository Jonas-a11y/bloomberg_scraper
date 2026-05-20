// static/js/tabs/dashboard.js
function dashboardMixin() {
    return {
        dashboard: { total_wealth: 0, count: 0, snapshots: 0, latest_scrape: null },

        async loadDashboard() {
            this.dashboard = await fetch('/api/dashboard').then(r => r.json());
        },
    };
}
