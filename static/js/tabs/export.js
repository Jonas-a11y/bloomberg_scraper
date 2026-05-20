// static/js/tabs/export.js
const EXPORT_FIELDS_BY_GROUP = {
    identity: ['person_id', 'common_name', 'full_name', 'first_name', 'last_name', 'middle_name', 'slug'],
    demographics: ['citizenship', 'age', 'birth_year', 'gender', 'gender_confidence'],
    financial: ['scraped_at', 'rank', 'net_worth_usd', 'last_change_usd', 'last_change_pct', 'ytd_change_usd', 'ytd_change_pct'],
    assets: ['public_assets_total', 'private_assets_total', 'cash_assets_total', 'public_assets_json', 'private_assets_json', 'cash_asset_value', 'liabilities_value', 'liabilities_note'],
    personal: ['industry', 'biography', 'overview', 'net_worth_summary', 'schools_json', 'facts_json', 'milestones_json'],
    metadata: ['confidence'],
};

function exportMixin() {
    return {
        exportScope: 'latest',
        exportFormat: 'csv',
        exportFrom: '',
        exportTo: '',
        historyFrom: '',
        historyTo: '',
        historyPersonId: '',
        historyFormat: 'csv',
        masterFields: [],
        allFields: [],
        defaultFields: [],
        fieldsByGroup: EXPORT_FIELDS_BY_GROUP,

        exportHref() {
            const params = new URLSearchParams();
            params.set('scope', this.exportScope);
            if (this.exportScope === 'range') {
                params.set('from_date', this.exportFrom);
                params.set('to_date', this.exportTo);
            }
            if (this.masterFields.length > 0) {
                params.set('fields', this.masterFields.join(','));
            }
            const ext = this.exportFormat === 'json' ? 'json' : 'csv';
            return `/api/export/bloomberg_billionaires.${ext}?${params}`;
        },

        historyExportHref() {
            const params = new URLSearchParams();
            if (this.historyFrom) params.set('from_date', this.historyFrom);
            if (this.historyTo) params.set('to_date', this.historyTo);
            if (this.historyPersonId) params.set('person_id', this.historyPersonId);
            const ext = this.historyFormat === 'json' ? 'json' : 'csv';
            const qs = params.toString();
            return `/api/export/wealth_history.${ext}${qs ? '?' + qs : ''}`;
        },

        selectAllFields() { this.masterFields = [...this.allFields]; },
        selectDefaultFields() { this.masterFields = [...this.defaultFields]; },
        deselectAllFields() { this.masterFields = []; },

        fieldLabel(f) {
            return f.replace(/_/g, ' ').replace(/\bjson\b/g, '(JSON)').replace(/\busd\b/g, '(USD)').replace(/\bpct\b/g, '(%)');
        },
    };
}
