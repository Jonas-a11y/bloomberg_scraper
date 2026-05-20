// static/app.js
// Thin Alpine root that composes per-tab mixins. Each mixin lives in
// static/js/tabs/* and returns a partial state + methods object. They are
// merged into one Alpine reactive instance so cross-tab `this` access works.
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

        formatWealth, formatChange, formatDate,
        rangeCutoff, filterRange,
    };
}
