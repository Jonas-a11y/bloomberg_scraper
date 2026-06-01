// static/js/format.js
// Pure formatting + range helpers — no Alpine state, no side effects.
// Loaded as global functions so any mixin can use them via `this.formatWealth(...)`.

function formatWealth(v) {
    if (!v) return '$0';
    const fmt = (n) => Number.isInteger(n) ? n.toString() : n.toFixed(1);
    if (v >= 1e12) return `$${fmt(v / 1e12)}T`;
    if (v >= 1e9) return `$${fmt(v / 1e9)}B`;
    if (v >= 1e6) return `$${fmt(v / 1e6)}M`;
    return `$${v.toLocaleString()}`;
}

function formatChange(v) {
    if (!v) return '$0';
    const sign = v >= 0 ? '+' : '';
    if (Math.abs(v) >= 1e9) return `${sign}$${(v / 1e9).toFixed(1)}B`;
    if (Math.abs(v) >= 1e6) return `${sign}$${(v / 1e6).toFixed(1)}M`;
    return `${sign}$${v.toLocaleString()}`;
}

function formatDate(d) {
    if (!d) return '';
    return new Date(d).toLocaleString(undefined, {
        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
}

function rangeCutoff(range) {
    const now = new Date();
    if (range === 'YTD') return new Date(now.getFullYear(), 0, 1);
    if (range === '6M') { const d = new Date(now); d.setMonth(d.getMonth() - 6); return d; }
    if (range === '1Y') { const d = new Date(now); d.setFullYear(d.getFullYear() - 1); return d; }
    if (range === '3Y') { const d = new Date(now); d.setFullYear(d.getFullYear() - 3); return d; }
    return null;
}

function filterRange(history, range) {
    const cutoff = rangeCutoff(range);
    if (!cutoff) return history;
    const cutoffStr = cutoff.toISOString().split('T')[0];
    return history.filter(h => (h.scraped_at || '').split('T')[0] >= cutoffStr);
}
