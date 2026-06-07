// static/app.js
// Thin Alpine root that composes per-tab mixins. Each mixin lives in
// static/js/tabs/* and returns a partial state + methods object. They are
// merged into one Alpine reactive instance so cross-tab `this` access works.
// Analytics is now a section inside Insights; old `tab=analytics` URLs
// transparently redirect.
const VALID_TABS = ['dashboard', 'table', 'families', 'scraper', 'export', 'profile', 'insights'];

function app() {
    return {
        ...dashboardMixin(),
        ...tableMixin(),
        ...analyticsMixin(),
        ...familiesMixin(),
        ...scraperMixin(),
        ...exportMixin(),
        ...panelMixin(),
        ...profileMixin(),
        ...insightsMixin(),

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

            this.$watch('tab', t => this.updateHash({ tab: t }, { push: true }));
        },

        applyHash() {
            const params = new URLSearchParams(location.hash.slice(1));
            let t = params.get('tab');
            // Backwards compat: old shared links to ?tab=analytics now
            // route to Insights, where those charts live.
            if (t === 'analytics') t = 'insights';
            if (t && VALID_TABS.includes(t) && t !== this.tab) {
                this.tab = t;
                if (t === 'table') this.loadTable();
                else if (t === 'families') this.loadFamilies();
                else if (t === 'scraper') this.loadScraper();
                else if (t === 'insights') this.loadInsights();
            }
            if (t === 'profile') {
                const id = params.get('id');
                if (id && (!this.profile || String(this.profile.person_id) !== id)) {
                    this.loadProfile(parseInt(id, 10));
                }
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

        updateHash(patch, { push = false } = {}) {
            // `push: true` creates a new history entry so the browser back
            // button traverses the navigation. Use it for tab changes and
            // opening profiles. Default is replaceState (in-place updates
            // like opening a side panel or scrubbing a slider).
            const params = new URLSearchParams(location.hash.slice(1));
            for (const [k, v] of Object.entries(patch)) {
                if (v === null || v === undefined || v === '') params.delete(k);
                else params.set(k, v);
            }
            const next = params.toString();
            const newHash = next ? `#${next}` : '';
            if (newHash === location.hash) return;
            if (push) history.pushState(null, '', newHash || ' ');
            else history.replaceState(null, '', newHash || ' ');
        },

        formatWealth, formatChange, formatDate,
        rangeCutoff, filterRange,
    };
}
