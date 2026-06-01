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
    };
}
