// static/js/tabs/table.js
function tableMixin() {
    return {
        tableData: { data: [], total: 0 },
        tableFilters: { q: '', country: '', industry: '', gender: '', sort: 'rank' },
        countries: [],
        industries: [],

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
            this.tableFilters.sort = (this.tableFilters.sort === col) ? `-${col}` : col;
            this.loadTable();
        },
    };
}
