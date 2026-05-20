// static/js/tabs/families.js
const FAMILIES_INDUSTRY_COLOR = {
    Technology: '#4ecdc4', Finance: '#6c5ce7',
    'Real Estate': '#fdcb6e', Retail: '#ff9f43',
};
const FAMILIES_ENTITY_COLOR = {
    company: '#0984e3', school: '#00b894',
    board: '#e17055', organization: '#e17055',
    work: '#b2bec3', other: '#95a5a6',
};
const FAMILIES_EDGE_COLOR = {
    spouse: '#ff6b6b',
    father: '#4ecdc4', mother: '#4ecdc4', child: '#4ecdc4',
    sibling: '#fdcb6e',
    relative: '#a29bfe',
};

function familiesMixin() {
    return {
        familiesGraph: null,
        familiesNetwork: null,
        familiesHideIsolated: true,
        familiesRefresh: { running: false, stage: null, done: 0, total: 0, qids_resolved: 0, edges_added: 0, entities_added: 0, entity_links_added: 0, message: null },
        familiesTimer: null,
        pathFromPerson: null, pathFromQuery: '', pathFromResults: [],
        pathToPerson: null, pathToQuery: '', pathToResults: [],
        pathChain: [], pathMessage: '', pathHighlightIds: [],

        async loadFamilies() {
            const [graph, status] = await Promise.all([
                fetch('/api/families').then(r => r.json()),
                fetch('/api/families/refresh').then(r => r.json()),
            ]);
            this.familiesGraph = graph;
            this.familiesRefresh = status;
            this.$nextTick(() => this.renderFamilyGraph());
            if (status.running && !this.familiesTimer) {
                this.familiesTimer = setInterval(async () => {
                    this.familiesRefresh = await fetch('/api/families/refresh').then(r => r.json());
                    if (!this.familiesRefresh.running) {
                        clearInterval(this.familiesTimer);
                        this.familiesTimer = null;
                        this.familiesGraph = await fetch('/api/families').then(r => r.json());
                        this.renderFamilyGraph();
                    }
                }, 3000);
            }
        },

        async triggerFamilyRefresh() {
            await fetch('/api/families/refresh', { method: 'POST' });
            this.loadFamilies();
        },

        hasNetworkData() {
            const g = this.familiesGraph;
            if (!g) return false;
            return (g.edges || []).length > 0 || (g.entity_links || []).length > 0;
        },

        async searchPath(slot) {
            const query = slot === 'from' ? this.pathFromQuery : this.pathToQuery;
            if (query.length < 2) {
                if (slot === 'from') this.pathFromResults = []; else this.pathToResults = [];
                return;
            }
            const results = await fetch(`/api/search?q=${encodeURIComponent(query)}`).then(r => r.json());
            if (slot === 'from') this.pathFromResults = results; else this.pathToResults = results;
        },

        selectPathPerson(slot, person) {
            if (slot === 'from') {
                this.pathFromPerson = person;
                this.pathFromQuery = '';
                this.pathFromResults = [];
            } else {
                this.pathToPerson = person;
                this.pathToQuery = '';
                this.pathToResults = [];
            }
        },

        async findPath() {
            this.pathMessage = '';
            this.pathChain = [];
            this.pathHighlightIds = [];
            if (!this.pathFromPerson || !this.pathToPerson) return;
            const url = `/api/families/path?from=${this.pathFromPerson.person_id}&to=${this.pathToPerson.person_id}`;
            const res = await fetch(url);
            if (!res.ok) {
                this.pathMessage = 'No path found in current graph.';
                this.renderFamilyGraph();
                return;
            }
            const data = await res.json();
            this.pathChain = data.chain;
            this.pathHighlightIds = data.chain.map(s => s.kind === 'person' ? `p${s.id}` : `e${s.id}`);
            this.renderFamilyGraph();
        },

        clearPath() {
            this.pathChain = [];
            this.pathHighlightIds = [];
            this.pathMessage = '';
            this.pathFromPerson = null;
            this.pathToPerson = null;
            this.pathFromQuery = '';
            this.pathToQuery = '';
            this.pathFromResults = [];
            this.pathToResults = [];
            this.renderFamilyGraph();
        },

        renderFamilyGraph() {
            const container = document.getElementById('familyGraph');
            if (!container || !this.familiesGraph) return;
            const g = this.familiesGraph;
            const personNodes = g.nodes || [];
            const familyEdges = g.edges || [];
            const entities = g.entities || [];
            const entityLinks = g.entity_links || [];
            if (familyEdges.length === 0 && entityLinks.length === 0) return;

            const connectedPersons = new Set();
            for (const e of familyEdges) { connectedPersons.add(e.source); connectedPersons.add(e.target); }
            for (const l of entityLinks) { connectedPersons.add(l.person_id); }

            const visiblePersons = this.familiesHideIsolated
                ? personNodes.filter(n => connectedPersons.has(n.id))
                : personNodes;

            const usedEntityIds = new Set(entityLinks.map(l => l.entity_id));
            const visibleEntities = entities.filter(e => usedEntityIds.has(e.id));

            const maxWorth = Math.max(...visiblePersons.map(n => n.net_worth_usd || 0), 1);
            const highlight = new Set(this.pathHighlightIds);

            const personVis = visiblePersons.map(n => {
                const nodeId = `p${n.id}`;
                const isHighlighted = highlight.has(nodeId);
                return {
                    id: nodeId,
                    label: n.name,
                    title: `${n.name}\n#${n.rank || '?'} · ${formatWealth(n.net_worth_usd || 0)}`,
                    value: Math.sqrt((n.net_worth_usd || 0) / maxWorth) * 30 + 8,
                    color: isHighlighted ? '#ff6b6b' : (FAMILIES_INDUSTRY_COLOR[n.industry] || '#a29bfe'),
                    borderWidth: isHighlighted ? 3 : 0,
                    _kind: 'person', _personId: n.id,
                };
            });

            const entityVis = visibleEntities.map(e => {
                const nodeId = `e${e.id}`;
                const isHighlighted = highlight.has(nodeId);
                return {
                    id: nodeId,
                    label: e.name,
                    title: `${e.name} (${e.kind})`,
                    value: 12,
                    shape: 'square',
                    color: isHighlighted ? '#ff6b6b' : (FAMILIES_ENTITY_COLOR[e.kind] || '#95a5a6'),
                    borderWidth: isHighlighted ? 3 : 0,
                    font: { size: 10, color: '#555' },
                    _kind: 'entity',
                };
            });

            const familyEdgeVis = familyEdges.map(e => ({
                from: `p${e.source}`, to: `p${e.target}`,
                color: { color: FAMILIES_EDGE_COLOR[e.kind] || '#999', opacity: 0.7 },
                title: e.kind,
                width: e.kind === 'spouse' ? 2 : 1,
                arrows: (e.kind === 'child' || e.kind === 'father' || e.kind === 'mother') ? 'to' : undefined,
            }));
            const entityEdgeVis = entityLinks.map(l => ({
                from: `p${l.person_id}`, to: `e${l.entity_id}`,
                color: { color: '#bbb', opacity: 0.5 },
                title: l.role, dashes: true, width: 1,
            }));

            const data = {
                nodes: new vis.DataSet([...personVis, ...entityVis]),
                edges: new vis.DataSet([...familyEdgeVis, ...entityEdgeVis]),
            };
            const options = {
                nodes: { shape: 'dot', font: { size: 11, color: '#1a1a2e' }, borderWidth: 0, scaling: { min: 8, max: 38 } },
                edges: { smooth: { type: 'continuous' } },
                physics: {
                    solver: 'forceAtlas2Based',
                    forceAtlas2Based: { gravitationalConstant: -55, springLength: 90, springConstant: 0.06 },
                    stabilization: { iterations: 250 },
                },
                interaction: { hover: true, tooltipDelay: 150 },
            };

            if (this.familiesNetwork) this.familiesNetwork.destroy();
            this.familiesNetwork = new vis.Network(container, data, options);
            this.familiesNetwork.on('click', params => {
                if (params.nodes.length === 0) return;
                const nodeId = params.nodes[0];
                if (typeof nodeId === 'string' && nodeId.startsWith('p')) {
                    this.openPanel(parseInt(nodeId.slice(1), 10));
                }
            });
        },
    };
}
