// static/js/tabs/profile.js
// Full-page profile view. Hits /api/persons/{id}/profile and renders the
// combined Bloomberg + family + entity payload. Routed via #tab=profile&id=N.
function profileMixin() {
    return {
        profile: null,
        profileChart: null,
        profileRange: 'ALL',
        profileSchools: [],
        profileMilestones: [],
        profileFacts: [],
        profileAssets: { public: [], private: [] },

        async loadProfile(personId) {
            if (!personId) return;
            this.profile = null;
            const data = await fetch(`/api/persons/${personId}/profile`).then(r => r.json());
            if (data.error) { this.profile = { error: data.error }; return; }
            this.profile = data;
            this.profileSchools = data.schools_json ? JSON.parse(data.schools_json) : [];
            this.profileMilestones = data.milestones_json ? JSON.parse(data.milestones_json) : [];
            this.profileFacts = data.facts_json ? JSON.parse(data.facts_json) : [];
            this.profileAssets = {
                public: data.public_assets_json ? JSON.parse(data.public_assets_json) : [],
                private: data.private_assets_json ? JSON.parse(data.private_assets_json) : [],
            };
            this.$nextTick(() => this.renderProfileChart());
        },

        setProfileRange(range) {
            this.profileRange = range;
            this.renderProfileChart();
        },

        renderProfileChart() {
            if (!this.profile || !this.profile.history) return;
            const ctx = document.getElementById('profileChart');
            if (!ctx) return;
            if (this.profileChart) this.profileChart.destroy();
            const points = (this.profile.history || []).map(h => ({
                x: h.date || h.scraped_at, y: h.net_worth_usd,
            }));
            const filtered = filterRange(points.map(p => ({ scraped_at: p.x, net_worth_usd: p.y })),
                                          this.profileRange);
            this.profileChart = new Chart(ctx, {
                type: 'line',
                data: {
                    datasets: [{
                        label: 'Net worth',
                        data: filtered.map(f => ({ x: f.scraped_at, y: f.net_worth_usd })),
                        borderColor: '#4ecdc4',
                        backgroundColor: 'rgba(78, 205, 196, 0.15)',
                        fill: true, tension: 0.1, pointRadius: 0, borderWidth: 2,
                    }],
                },
                options: {
                    responsive: true, maintainAspectRatio: false, animation: false,
                    interaction: { mode: 'index', intersect: false },
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            callbacks: {
                                label: c => formatWealth(c.parsed.y),
                            },
                        },
                    },
                    scales: {
                        x: { type: 'category', ticks: { maxTicksLimit: 8 } },
                        y: { ticks: { callback: v => formatWealth(v) } },
                    },
                },
            });
        },

        openProfile(personId) {
            this.tab = 'profile';
            this.updateHash({ tab: 'profile', id: personId, person: null, entity: null });
            this.panelOpen = false;
            this.entityPanelOpen = false;
            this.loadProfile(personId);
        },

        profileFamilyByKind() {
            if (!this.profile || !this.profile.family) return {};
            const grouped = {};
            for (const f of this.profile.family) {
                (grouped[f.kind] = grouped[f.kind] || []).push(f);
            }
            return grouped;
        },

        profileEntitiesByKind() {
            if (!this.profile || !this.profile.entity_links) return {};
            const grouped = {};
            for (const e of this.profile.entity_links) {
                const k = e.entity_kind || 'other';
                (grouped[k] = grouped[k] || []).push(e);
            }
            return grouped;
        },

        profileAgeDisplay() {
            // Use age-at-death when we have both DOB and DOD, otherwise fall
            // back to Bloomberg's current age. Returns null when neither works.
            const meta = this.profile?.wikidata_metadata || {};
            if (meta.birth_date && meta.death_date) {
                const b = new Date(meta.birth_date);
                const d = new Date(meta.death_date);
                if (!isNaN(b) && !isNaN(d)) {
                    let age = d.getFullYear() - b.getFullYear();
                    const m = d.getMonth() - b.getMonth();
                    if (m < 0 || (m === 0 && d.getDate() < b.getDate())) age--;
                    return age;
                }
            }
            return this.profile?.age ?? null;
        },

        profileDetailLine() {
            const p = this.profile || {};
            const age = this.profileAgeDisplay();
            const meta = p.wikidata_metadata || {};
            const isDeceased = !!meta.death_date;
            const parts = [];
            if (p.is_active && p.rank) parts.push('#' + p.rank);
            else if (!p.is_active && p.rank) parts.push('Former #' + p.rank);
            if (p.citizenship) parts.push(p.citizenship);
            if (age != null) parts.push(isDeceased ? `Age ${age} at death` : `Age ${age}`);
            if (p.industry) parts.push(p.industry);
            return parts.join(' · ');
        },
    };
}
