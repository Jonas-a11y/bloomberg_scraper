// static/js/tabs/panel.js
// Person + entity detail side panels (opens from table or graph clicks).

// Wikidata role labels are machine-shaped — humanize them for display.
// The map covers the common roles we emit; anything else gets underscores
// turned into spaces as a sane fallback.
const ROLE_LABELS = {
    educated_at: 'studied at', alum_of: 'alum',
    employer: 'works at', employs: 'employee',
    member_of: 'member', includes: 'has member',
    board_member: 'board member', board_of: 'board includes',
    founded: 'founder', founded_by: 'founder',
    chair: 'chair', chaired_by: 'chaired by',
    owner_of: 'owner', owned_by: 'owner',
    notable_work: 'created', created_by: 'creator',
    political_party: 'party', has_member: 'member',
    affiliated_with: 'affiliated',
    participant_in: 'participated', had_participant: 'participant',
    holds: 'holds shares', owns: 'owns',
    subsidiary_of: 'subsidiary of', has_subsidiary: 'parent of',
    part_of: 'part of', has_part: 'includes',
};
function humanizeRole(role) {
    if (!role) return '';
    return ROLE_LABELS[role] || role.replace(/_/g, ' ');
}

// Entity-kind labels for the related list. "other" reads as junk to a
// reader, but in practice it's almost always a person Wikidata classifies
// as P31=human — show that explicitly.
const ENTITY_KIND_LABELS = {
    company: 'company', school: 'school', board: 'board',
    organization: 'organization', work: 'work', stock: 'stock',
    private_company: 'private co.', party: 'political party',
    place: 'place', position: 'position', award: 'award',
    event: 'event', other: 'person',
};
function humanizeKind(kind) {
    return ENTITY_KIND_LABELS[kind] || kind || 'entity';
}

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

        entityPanelOpen: false,
        entityPanel: null,

        humanizeRole, humanizeKind,

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
            if (this.updateHash) this.updateHash({ person: personId, entity: null });
            this.$nextTick(() => this.renderPanelChart());
        },

        async openEntityPanel(entityId) {
            const detail = await fetch(`/api/entities/${entityId}`).then(r => r.json());
            this.entityPanel = detail;
            this.entityPanelOpen = true;
            if (this.updateHash) this.updateHash({ entity: entityId, person: null });
        },

        closePanel() {
            this.panelOpen = false;
            if (this.updateHash) this.updateHash({ person: null });
        },

        closeEntityPanel() {
            this.entityPanelOpen = false;
            if (this.updateHash) this.updateHash({ entity: null });
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
                    interaction: { mode: 'index', intersect: false },
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            callbacks: {
                                label: ctx => `${ctx.dataset.label}: ${formatWealth(ctx.parsed.y)}`,
                            },
                        },
                    },
                    scales: { y: { ticks: { callback: v => formatWealth(v) } } },
                },
            });
        },
    };
}
