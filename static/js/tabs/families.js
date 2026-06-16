// static/js/tabs/families.js
const FAMILIES_INDUSTRY_COLOR = {
    Technology: '#4ecdc4', Finance: '#6c5ce7',
    'Real Estate': '#fdcb6e', Retail: '#ff9f43',
};
const FAMILIES_ENTITY_COLOR = {
    company: '#0984e3', school: '#00b894',
    board: '#e17055', organization: '#e17055',
    work: '#b2bec3', stock: '#9b59b6',
    private_company: '#d35400', party: '#c0392b',
    other: '#95a5a6',
};
const FAMILIES_EDGE_COLOR = {
    spouse: '#ff6b6b',
    father: '#4ecdc4', mother: '#4ecdc4', child: '#4ecdc4',
    sibling: '#fdcb6e',
    relative: '#a29bfe',
};
// Ordered stages used by run_refresh() in app/family/refresh.py. Only `resolve`
// reports done/total — the others are batched SPARQL fires, so the bar jumps
// when the stage flips. Order here drives the percent calculation.
const FAMILIES_REFRESH_STAGES = [
    { key: 'resolve', label: 'Resolve' },
    { key: 'relations', label: 'Family' },
    { key: 'entities', label: 'Entities' },
    { key: 'labels', label: 'Labels' },
    { key: 'entity_edges', label: 'Edges' },
    { key: 'second_tier', label: '2nd-tier' },
    { key: 'holdings', label: 'Holdings' },
];

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
        metricsData: null,
        compareResult: null,

        async loadFamilies() {
            const [graph, status, metrics] = await Promise.all([
                fetch('/api/families').then(r => r.json()),
                fetch('/api/families/refresh').then(r => r.json()),
                fetch('/api/families/metrics').then(r => r.json()),
            ]);
            this.familiesGraph = graph;
            this.familiesRefresh = status;
            this.metricsData = metrics;
            this.$nextTick(() => this.renderFamilyGraph());
            if (status.running && !this.familiesTimer) {
                this.familiesTimer = setInterval(async () => {
                    this.familiesRefresh = await fetch('/api/families/refresh').then(r => r.json());
                    if (!this.familiesRefresh.running) {
                        clearInterval(this.familiesTimer);
                        this.familiesTimer = null;
                        const [g, m] = await Promise.all([
                            fetch('/api/families').then(r => r.json()),
                            fetch('/api/families/metrics').then(r => r.json()),
                        ]);
                        this.familiesGraph = g;
                        this.metricsData = m;
                        this.renderFamilyGraph();
                    }
                }, 3000);
            }
        },

        async triggerFamilyRefresh() {
            await fetch('/api/families/refresh', { method: 'POST' });
            this.loadFamilies();
        },

        refreshStages: FAMILIES_REFRESH_STAGES,

        refreshStageIndex() {
            const stage = this.familiesRefresh.stage;
            const i = FAMILIES_REFRESH_STAGES.findIndex(s => s.key === stage);
            return i >= 0 ? i : -1;
        },

        refreshPercent() {
            const i = this.refreshStageIndex();
            const total = FAMILIES_REFRESH_STAGES.length;
            if (i < 0) return this.familiesRefresh.running ? 1 : 0;
            let frac = 0;
            if (this.familiesRefresh.stage === 'resolve' && this.familiesRefresh.total > 0) {
                frac = Math.min(this.familiesRefresh.done / this.familiesRefresh.total, 1);
            }
            return Math.round(((i + frac) / total) * 100);
        },

        refreshStageClass(key) {
            const i = this.refreshStageIndex();
            const j = FAMILIES_REFRESH_STAGES.findIndex(s => s.key === key);
            if (i < 0 || j < 0) return '';
            if (j < i) return 'stage-done';
            if (j === i) return 'stage-active';
            return '';
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
            this.compareResult = null;
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

        async comparePath() {
            this.pathMessage = '';
            this.pathChain = [];
            this.compareResult = null;
            this.pathHighlightIds = [];
            if (!this.pathFromPerson || !this.pathToPerson) return;
            const url = `/api/families/compare?a=${this.pathFromPerson.person_id}&b=${this.pathToPerson.person_id}`;
            const res = await fetch(url);
            if (!res.ok) {
                this.pathMessage = 'Could not compare these two people.';
                return;
            }
            const data = await res.json();
            this.compareResult = data;
            if (data.path && data.path.length > 0) {
                this.pathHighlightIds = data.path.map(s => s.kind === 'person' ? `p${s.id}` : `e${s.id}`);
                this.renderFamilyGraph();
            }
        },

        clearPath() {
            this.pathChain = [];
            this.pathHighlightIds = [];
            this.pathMessage = '';
            this.compareResult = null;
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
            const entityEdges = g.entity_edges || [];
            if (familyEdges.length === 0 && entityLinks.length === 0) return;

            const connectedPersons = new Set();
            for (const e of familyEdges) { connectedPersons.add(e.source); connectedPersons.add(e.target); }
            for (const l of entityLinks) { connectedPersons.add(l.person_id); }

            const visiblePersons = this.familiesHideIsolated
                ? personNodes.filter(n => connectedPersons.has(n.id))
                : personNodes;

            const usedEntityIds = new Set(entityLinks.map(l => l.entity_id));
            // Also keep entities that participate in entity↔entity edges where the
            // other endpoint is already visible (so company-of-company chains show).
            for (const ee of entityEdges) {
                if (usedEntityIds.has(ee.source) || usedEntityIds.has(ee.target)) {
                    usedEntityIds.add(ee.source); usedEntityIds.add(ee.target);
                }
            }
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
                const titleLines = [`${e.name} (${e.kind})`];
                if (e.description) titleLines.push(e.description);
                if (e.country) titleLines.push(e.country);
                return {
                    id: nodeId,
                    label: e.name,
                    title: titleLines.join('\n'),
                    value: 12,
                    shape: 'square',
                    color: isHighlighted ? '#ff6b6b' : (FAMILIES_ENTITY_COLOR[e.kind] || '#95a5a6'),
                    borderWidth: isHighlighted ? 3 : 0,
                    font: { size: 10, color: '#555' },
                    _kind: 'entity', _entityId: e.id,
                };
            });

            const familyEdgeVis = familyEdges.map(e => ({
                from: `p${e.source}`, to: `p${e.target}`,
                color: { color: FAMILIES_EDGE_COLOR[e.kind] || '#999', opacity: 0.7 },
                title: e.kind,
                width: e.kind === 'spouse' ? 2 : 1,
                arrows: (e.kind === 'child' || e.kind === 'father' || e.kind === 'mother') ? 'to' : undefined,
            }));
            const entityLinkVis = entityLinks.map(l => ({
                from: `p${l.person_id}`, to: `e${l.entity_id}`,
                color: { color: '#bbb', opacity: 0.5 },
                title: l.role, dashes: true, width: 1,
            }));
            // Render entity↔entity edges only when both endpoints are visible.
            const entityEdgeVis = entityEdges
                .filter(ee => usedEntityIds.has(ee.source) && usedEntityIds.has(ee.target))
                .map(ee => ({
                    from: `e${ee.source}`, to: `e${ee.target}`,
                    color: { color: '#888', opacity: 0.55 },
                    title: ee.kind, dashes: [4, 4], width: 1, arrows: 'to',
                }));

            const data = {
                nodes: new vis.DataSet([...personVis, ...entityVis]),
                edges: new vis.DataSet([...familyEdgeVis, ...entityLinkVis, ...entityEdgeVis]),
            };
            const options = {
                nodes: {
                    shape: 'dot',
                    font: { size: 11, color: '#1a1a2e' },
                    borderWidth: 0,
                    scaling: { min: 8, max: 38 },
                    // shadow gives the network depth without the constant
                    // micro-motion that physics-driven layouts use to convey
                    // hierarchy
                    shadow: { enabled: true, color: 'rgba(0,0,0,0.10)', size: 4, x: 0, y: 1 },
                },
                edges: {
                    smooth: { type: 'continuous', roundness: 0.2 },
                    selectionWidth: 1.5,
                },
                physics: {
                    // barnesHut converges faster than forceAtlas2Based on
                    // sparse multi-cluster graphs (which is what we have:
                    // many small family pods linked by a few entity nodes).
                    // forceAtlas pulled disconnected components toward the
                    // origin, leaving them piled up in the centre.
                    solver: 'barnesHut',
                    barnesHut: {
                        gravitationalConstant: -2200,
                        centralGravity: 0.12,
                        springLength: 110,
                        springConstant: 0.04,
                        damping: 0.55,           // higher damping → settles faster
                        avoidOverlap: 0.3,
                    },
                    // adaptiveTimestep lets vis ramp up the integrator step
                    // size once kinetic energy drops, hitting the
                    // stabilization target in fewer ticks
                    adaptiveTimestep: true,
                    stabilization: {
                        enabled: true,
                        iterations: 400,
                        updateInterval: 50,
                        // Run iterations OFFSCREEN before the first paint —
                        // that's what removes the visible "spinning" on
                        // page open. The user only sees the final layout.
                        fit: true,
                        onlyDynamicEdges: false,
                    },
                },
                interaction: {
                    hover: true,
                    tooltipDelay: 150,
                    // Freeze the layout when the user lets go of a node so
                    // dragging one billionaire doesn't wobble half the graph.
                    dragNodes: true,
                    dragView: true,
                    zoomView: true,
                },
            };

            if (this.familiesNetwork) this.familiesNetwork.destroy();

            // Hide the container until vis finishes stabilizing — that's
            // the simplest way to make the graph appear in its final layout
            // instead of flying in. Restored on stabilizationIterationsDone.
            container.style.opacity = '0';
            container.style.transition = 'opacity 240ms ease';

            this.familiesNetwork = new vis.Network(container, data, options);

            // Once the layout has settled, fade the canvas in AND turn
            // physics off. With physics off, the graph stays still:
            //   - opening the page doesn't show drift
            //   - hovering doesn't nudge neighbours
            //   - a node the user drags stays where they put it
            // Re-enabled briefly on path-highlight / data refresh so new
            // nodes find their place, then frozen again.
            this.familiesNetwork.once('stabilizationIterationsDone', () => {
                this.familiesNetwork.setOptions({ physics: { enabled: false } });
                container.style.opacity = '1';
            });
            // Belt-and-braces: if stabilization never fires (e.g. tiny graph
            // that converges in <1 tick), reveal anyway after a short delay.
            setTimeout(() => { container.style.opacity = '1'; }, 800);

            this.familiesNetwork.on('click', params => {
                if (params.nodes.length === 0) return;
                const nodeId = params.nodes[0];
                if (typeof nodeId !== 'string') return;
                if (nodeId.startsWith('p')) {
                    this.openPanel(parseInt(nodeId.slice(1), 10));
                } else if (nodeId.startsWith('e')) {
                    this.openEntityPanel(parseInt(nodeId.slice(1), 10));
                }
            });

            // Double-click on empty space resets the view to fit all
            // nodes — handy after the user has zoomed in or dragged
            // nodes around. Animated for polish.
            this.familiesNetwork.on('doubleClick', params => {
                if (params.nodes.length === 0) {
                    this.familiesNetwork.fit({
                        animation: { duration: 500, easingFunction: 'easeInOutQuad' },
                    });
                }
            });
        },
    };
}
