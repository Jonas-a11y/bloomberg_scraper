// static/app.js
// Thin Alpine root that composes per-tab mixins. Each mixin lives in
// static/js/tabs/* and returns a partial state + methods object. They are
// merged into one Alpine reactive instance so cross-tab `this` access works.
const VALID_TABS = ['dashboard', 'table', 'analytics', 'families', 'scraper', 'export'];

function app() {
    return {
        ...dashboardMixin(),
        ...tableMixin(),
        ...analyticsMixin(),
        ...familiesMixin(),
        ...scraperMixin(),
        ...exportMixin(),
        ...panelMixin(),

        tab: 'dashboard',

        async init() {
            this.applyHash();
            window.addEventListener('hashchange', () => this.applyHash());

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
            this.$nextTick(() => this.renderDashboardCharts());

            this.$watch('tab', t => this.updateHash({ tab: t }));
        },

        applyHash() {
            const params = new URLSearchParams(location.hash.slice(1));
            const t = params.get('tab');
            if (t && VALID_TABS.includes(t) && t !== this.tab) {
                this.tab = t;
                if (t === 'table') this.loadTable();
                else if (t === 'analytics') this.loadAnalytics();
                else if (t === 'families') this.loadFamilies();
                else if (t === 'scraper') this.loadScraper();
            }
            const personId = params.get('person');
            if (personId && (!this.panelPerson || String(this.panelPerson.person_id) !== personId)) {
                this.openPanel(parseInt(personId, 10));
            }
            const entityId = params.get('entity');
            if (entityId && (!this.entityPanel || String(this.entityPanel.id) !== entityId)) {
                this.openEntityPanel(parseInt(entityId, 10));
            }
        },

        updateHash(patch) {
            const params = new URLSearchParams(location.hash.slice(1));
            for (const [k, v] of Object.entries(patch)) {
                if (v === null || v === undefined || v === '') params.delete(k);
                else params.set(k, v);
            }
            const next = params.toString();
            const newHash = next ? `#${next}` : '';
            if (newHash !== location.hash) history.replaceState(null, '', newHash || ' ');
        },

        formatWealth, formatChange, formatDate,
        rangeCutoff, filterRange,
    };
}
