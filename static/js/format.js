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
    // We render the sign explicitly outside the dollar prefix so negative
    // values read as `-$8.0B`, not `$-8.0B`.
    const sign = v < 0 ? '-' : '+';
    const abs = Math.abs(v);
    if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(1)}B`;
    if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(1)}M`;
    return `${sign}$${abs.toLocaleString()}`;
}

function formatDate(d) {
    if (!d) return '';
    return new Date(d).toLocaleString(undefined, {
        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
}

function formatRelativeTime(d) {
    // "2h ago", "5d ago", "Jan 4". Switches to absolute for anything older
    // than two weeks since "47 days ago" reads worse than the date.
    if (!d) return '';
    const then = new Date(d).getTime();
    if (isNaN(then)) return '';
    const diffSec = Math.max(0, (Date.now() - then) / 1000);
    if (diffSec < 60) return 'just now';
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
    if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
    if (diffSec < 86400 * 14) return `${Math.floor(diffSec / 86400)}d ago`;
    return new Date(d).toLocaleDateString(undefined, {
        month: 'short', day: 'numeric', year: 'numeric',
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
