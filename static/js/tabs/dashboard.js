// static/js/tabs/dashboard.js
function dashboardMixin() {
    return {
        dashboard: {
            total_wealth: 0, count: 0, snapshots: 0, latest_scrape: null,
            country_leaderboard: [], industry_leaderboard: [],
            movement: null, wealth_age: [], concentration_trend: [],
        },
        dashChartInstances: {},
        ageSmoothing: 2,

        async loadDashboard() {
            this.dashboard = await fetch('/api/dashboard').then(r => r.json());
            this.$nextTick(() => this.renderDashboardCharts());
        },

        renderDashboardCharts() {
            const d = this.dashboard;
            if (d.country_leaderboard && d.country_leaderboard.length) {
                renderBarChart(this.dashChartInstances, 'dashCountryChart',
                    d.country_leaderboard.slice(0, 8), 'country', 'total_wealth');
            }
            if (d.industry_leaderboard && d.industry_leaderboard.length) {
                renderBarChart(this.dashChartInstances, 'dashIndustryChart',
                    d.industry_leaderboard.slice(0, 8), 'industry', 'total_wealth');
            }
            if (d.wealth_age && d.wealth_age.length) {
                renderAgeWealthLine(this.dashChartInstances, 'dashAgeLineChart', d.wealth_age, this.ageSmoothing);
            }
            if (d.concentration_trend && d.concentration_trend.length) {
                renderConcentrationLine(this.dashChartInstances, 'dashConcentrationChart',
                    d.concentration_trend);
            }
        },
    };
}
