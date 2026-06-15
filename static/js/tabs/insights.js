// static/js/tabs/insights.js
// Cross-filtered Insights dashboard. Seven charts share a single filter
// state (country, industry, year_from, year_to). Clicking on most charts
// updates a filter and re-renders the others.

// Chart.js instances are kept OFF the Alpine reactive object — Alpine
// would deep-proxy them and Chart's internal save()/restore() context
// calls would NPE. We use a module-level Map keyed by canvas id so the
// instances survive across re-renders and stay non-reactive.
const _insightsCharts = new Map();
function _getChart(id) { return _insightsCharts.get(id); }
function _setChart(id, chart) { _insightsCharts.set(id, chart); }
function _destroyChart(id) {
    const c = _insightsCharts.get(id);
    if (c) {
        try { c.destroy(); } catch (_) { /* already gone */ }
        _insightsCharts.delete(id);
    }
}

// Stable industry → color map. Keys must match the canonical labels
// produced by _normalize_industry() on the backend.
const INDUSTRY_PALETTE = {
    "Technology":           "#4ecdc4",
    "Finance & Investments": "#6c5ce7",
    "Fashion & Retail":     "#fd79a8",
    "Consumer":             "#fd79a8",
    "Manufacturing":        "#fdcb6e",
    "Industrial":           "#fdcb6e",
    "Food & Beverage":      "#55efc4",
    "Real Estate":          "#a29bfe",
    "Diversified":          "#74b9ff",
    "Energy":               "#ff7675",
    "Metals & Mining":      "#b2bec3",
    "Healthcare":           "#00b894",
    "Pharmaceuticals":      "#00b894",
    "Media & Entertainment": "#e17055",
    "Telecom":              "#0984e3",
    "Logistics":            "#7f8fa6",
    "Sports":               "#d63031",
    "Service":              "#81ecec",
    "Construction & Engineering": "#fab1a0",
    "Gambling & Casinos":   "#9b59b6",
    "Automotive":           "#ffeaa7",
    "Other":                "#bbb",
};

// Hash any unknown industry string to a deterministic palette color
// instead of dumping it into "Other" — keeps the visual diversity but
// stays stable across years for the same industry name.
const _FALLBACK_PALETTE = [
    "#16a085", "#2980b9", "#8e44ad", "#27ae60", "#d35400",
    "#c0392b", "#7f8c8d", "#2c3e50", "#e84393", "#00cec9",
];
function _industryColor(ind) {
    if (!ind) return INDUSTRY_PALETTE.Other;
    if (INDUSTRY_PALETTE[ind]) return INDUSTRY_PALETTE[ind];
    let h = 0;
    for (const c of ind) h = (h * 31 + c.charCodeAt(0)) >>> 0;
    return _FALLBACK_PALETTE[h % _FALLBACK_PALETTE.length];
}

// Image cache so we don't re-download avatars on every redraw.
const _avatarCache = new Map();
function _loadAvatar(url) {
    if (!url) return null;
    if (_avatarCache.has(url)) return _avatarCache.get(url);
    const img = new Image();
    // Don't set crossOrigin: Wikimedia doesn't return CORS headers.
    // We never read pixel data back from the canvas, so a tainted
    // context is fine — drawImage works either way.
    img.src = url;
    img.onload = () => {
        img._loaded = true;
        // If an animation isn't running, kick off one frame so the new
        // avatar shows up immediately instead of waiting for the next
        // tween. The renderer is idempotent at t=1.
        if (!_race.raf && _race.canvas && _race.next.length) {
            _race.startedAt = performance.now() - _race.durationMs;
            _race.raf = requestAnimationFrame(_drawRaceFrame);
        }
    };
    img.onerror = () => { _avatarCache.delete(url); };
    _avatarCache.set(url, img);
    return img;
}

// Race renderer state. Lives outside Alpine so it survives re-renders
// and isn't subject to Proxy interception. One renderer per page.
const _race = {
    canvas: null,
    ctx: null,
    raf: null,

    // ─── Continuous-timeline mode (new) ──────────────────────────────
    // The full per-person monthly series; the renderer reads from this
    // every frame and interpolates against the playhead `cursorYm`.
    persons: [],          // [{ person_id, name, industry, image_url, series: [{ym, v}] }]
    startMonths: 0,       // total months in the timeline
    cursorMonths: 0,      // playhead, in fractional months from `startYm`
    startYm: "",          // "YYYY-MM"
    playing: false,
    speedMonthsPerSec: 6, // 6 months per second → ~50s for 25 years
    lastTickAt: 0,
    n: 12,                // visible top-N

    // ─── Smoothed display state ──────────────────────────────────────
    // Each person's currently-displayed (smoothed) rank/value/alpha so we
    // can low-pass-filter the targets and avoid pop-in/pop-out.
    smoothed: new Map(),  // key → { rank, value, alpha, industry, image_url, name, person_id }
    smoothedMaxValue: 1,  // smoothed X-axis max so the scale glides
    onClick: null,
    hitRows: [],
};

function _easeInOutCubic(t) {
    return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
}

// ─── Continuous-timeline renderer ─────────────────────────────────────

// Date helpers: months between two "YYYY-MM" strings, and YYYY-MM at a
// fractional month offset from a base.
function _ymToMonths(ym) {
    if (!ym) return 0;
    const [y, m] = ym.split("-").map(Number);
    return y * 12 + (m - 1);
}
function _monthsToYm(totalMonths) {
    const m = Math.floor(totalMonths);
    const y = Math.floor(m / 12);
    return `${y}-${String((m % 12) + 1).padStart(2, "0")}`;
}

// For a person's monthly anchor list and a fractional month offset from
// the timeline start, return the linearly-interpolated value. Returns
// null when the cursor is too far before the person's first observation
// or after their last (so they fade in / out at the timeline edges
// instead of popping into existence at value=0).
//
// We extend coverage on both sides:
// - Before first obs: hold the first value backwards by up to one year
//   (Forbes annual anchors land at YYYY-12; without this, all of 2001-01
//   through 2001-11 would render as if nobody existed yet).
// - After last obs: hold the last value forward by up to 6 months so a
//   person with a late-year drop-off doesn't vanish before the cursor
//   reaches their final month visually.
const _COVERAGE_BACK_MONTHS = 12;
const _COVERAGE_FWD_MONTHS = 6;

function _valueAt(person, cursorMonths, startMonths) {
    const series = person.series || [];
    if (!series.length) return null;
    const cursorAbs = startMonths + cursorMonths;
    const firstAbs = _ymToMonths(series[0].ym);
    const lastAbs = _ymToMonths(series[series.length - 1].ym);

    // Out of range guards with extended coverage windows.
    if (cursorAbs < firstAbs - _COVERAGE_BACK_MONTHS) return null;
    if (cursorAbs > lastAbs + _COVERAGE_FWD_MONTHS) return null;

    // In the back-coverage window: hold first value
    if (cursorAbs <= firstAbs) return series[0].v;
    // In the fwd-coverage window: hold last value
    if (cursorAbs >= lastAbs) return series[series.length - 1].v;

    // Binary search the bracketing pair
    let lo = 0, hi = series.length - 1;
    while (lo + 1 < hi) {
        const mid = (lo + hi) >> 1;
        const m = _ymToMonths(series[mid].ym);
        if (m <= cursorAbs) lo = mid; else hi = mid;
    }
    const aAbs = _ymToMonths(series[lo].ym);
    const bAbs = _ymToMonths(series[hi].ym);
    if (aAbs === bAbs) return series[lo].v;
    const t = (cursorAbs - aAbs) / (bAbs - aAbs);
    return series[lo].v + (series[hi].v - series[lo].v) * t;
}

// Initialize the renderer with a fresh timeline payload.
function _initRaceTimeline(payload, n) {
    _race.persons = payload.persons || [];
    _race.startYm = payload.start;
    const endMonths = _ymToMonths(payload.end);
    _race.startMonths = _ymToMonths(payload.start);
    _race.totalMonths = Math.max(0, endMonths - _race.startMonths);
    _race.cursorMonths = _race.totalMonths;  // start at the end
    _race.n = n;
    _race.smoothed.clear();
    _race.smoothedMaxValue = 1;
    if (!_race.raf && _race.canvas && _race.ctx) {
        _race.raf = requestAnimationFrame(_drawRaceFrame);
    }
}

// Renders one frame: read the playhead, compute every visible person's
// target rank+value, low-pass smooth toward those targets, draw.
function _drawRaceFrame(now) {
    const r = _race;
    const { canvas, ctx } = r;
    if (!canvas || !ctx) { r.raf = null; return; }
    if (typeof now !== "number") now = performance.now();

    // Advance the cursor when playing. Tie speed to wall-clock so dropped
    // frames don't slow the playback.
    if (r.playing) {
        const dt = Math.min(0.1, (now - (r.lastTickAt || now)) / 1000);
        r.cursorMonths = Math.min(
            r.totalMonths,
            r.cursorMonths + dt * r.speedMonthsPerSec,
        );
        if (r.cursorMonths >= r.totalMonths) {
            r.playing = false;
            r.cursorMonths = r.totalMonths;
        }
    }
    r.lastTickAt = now;

    // Compute (target_value, key) for every person at the current cursor.
    // null means they aren't on the timeline yet / anymore — skip.
    const targets = [];
    for (const p of r.persons) {
        const v = _valueAt(p, r.cursorMonths, r.startMonths);
        if (v == null) continue;
        targets.push({
            key: p.person_id || p.name,
            value: v,
            person_id: p.person_id,
            name: p.name,
            industry: p.industry,
            image_url: p.image_url,
        });
    }
    targets.sort((a, b) => b.value - a.value);
    const visibleN = r.n;

    // Assign target ranks (continuous: 0..N-1 for visible, N+0.5 for off).
    // We give "just outside" persons a halfway-out rank so they slide in
    // and out smoothly when the bottom of the visible band changes hands.
    const targetMap = new Map();
    for (let i = 0; i < targets.length; i++) {
        const t = targets[i];
        const targetRank = i < visibleN ? i : visibleN + 0.5;
        const targetAlpha = i < visibleN ? 1 : 0;
        targetMap.set(t.key, {
            rank: targetRank,
            value: t.value,
            alpha: targetAlpha,
            ...t,
        });
    }
    // Persons currently smoothed but not in targets (off-the-edge): give
    // them rank=visibleN+1, alpha=0 so they fade and slide off.
    for (const [k, sm] of r.smoothed) {
        if (!targetMap.has(k)) {
            targetMap.set(k, {
                ...sm,  // carry industry/image_url/name
                key: k,
                rank: visibleN + 1,
                value: sm.value,  // hold the bar width while sliding off
                alpha: 0,
            });
        }
    }

    // Low-pass filter toward targets. Time-constant tau ~ 350ms produces
    // a buttery-smooth feel without lagging too much. Computed per real
    // dt so it's framerate-independent.
    const dtSec = Math.min(0.05, (now - (r._lastDrawAt || now)) / 1000);
    r._lastDrawAt = now;
    const tau = 0.35;
    const blend = 1 - Math.exp(-dtSec / tau);

    const newSmoothed = new Map();
    let visibleMaxValue = 0;
    for (const [k, t] of targetMap) {
        const prev = r.smoothed.get(k);
        const sm = {
            key: k,
            person_id: t.person_id,
            name: t.name,
            industry: t.industry,
            image_url: t.image_url,
            rank: prev ? prev.rank + (t.rank - prev.rank) * blend : t.rank,
            value: prev ? prev.value + (t.value - prev.value) * blend : t.value,
            alpha: prev ? prev.alpha + (t.alpha - prev.alpha) * blend : t.alpha,
        };
        newSmoothed.set(k, sm);
        if (t.alpha > 0.5 && sm.rank < visibleN + 0.5) {
            visibleMaxValue = Math.max(visibleMaxValue, sm.value);
        }
    }
    r.smoothed = newSmoothed;

    // Smooth the X-axis max with the same low-pass — this is the key
    // fix for "values jump when switching years". As the leader's value
    // grows by 5%, the scale rescales by ~5% over ~350ms instead of
    // snapping mid-frame.
    const targetMax = Math.max(visibleMaxValue, 1e9);
    r.smoothedMaxValue =
        r.smoothedMaxValue + (targetMax - r.smoothedMaxValue) * blend;
    const maxVal = r.smoothedMaxValue;

    // ─── Draw ────────────────────────────────────────────────────────
    const dpr = window.devicePixelRatio || 1;
    const cssW = canvas.clientWidth, cssH = canvas.clientHeight;
    if (canvas.width !== cssW * dpr || canvas.height !== cssH * dpr) {
        canvas.width = cssW * dpr; canvas.height = cssH * dpr;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    ctx.clearRect(0, 0, cssW, cssH);

    const PAD_LEFT = 220, PAD_RIGHT = 80, PAD_TOP = 16;
    const ROW_H = (cssH - PAD_TOP - 16) / visibleN;
    const BAR_H = ROW_H * 0.78;
    const drawableW = cssW - PAD_LEFT - PAD_RIGHT;

    // Sort by interpolated rank so rows are drawn in order (matters for
    // overlap during rank swaps — top draws over bottom).
    const sorted = [...r.smoothed.values()].sort((a, b) => a.rank - b.rank);
    const drawnRows = [];
    for (const v of sorted) {
        if (v.alpha < 0.01) continue;
        if (v.rank > visibleN + 0.6) continue;
        const y = PAD_TOP + v.rank * ROW_H + (ROW_H - BAR_H) / 2;
        const w = (v.value / maxVal) * drawableW;

        ctx.save();
        ctx.globalAlpha = Math.max(0, Math.min(1, v.alpha));

        const color = _industryColor(v.industry);
        ctx.fillStyle = color;
        ctx.beginPath();
        const radius = 4;
        ctx.moveTo(PAD_LEFT, y + BAR_H);
        ctx.lineTo(PAD_LEFT + Math.max(0, w - radius), y + BAR_H);
        ctx.quadraticCurveTo(PAD_LEFT + w, y + BAR_H, PAD_LEFT + w, y + BAR_H - radius);
        ctx.lineTo(PAD_LEFT + w, y + radius);
        ctx.quadraticCurveTo(PAD_LEFT + w, y, PAD_LEFT + w - radius, y);
        ctx.lineTo(PAD_LEFT, y);
        ctx.closePath();
        ctx.fill();

        ctx.fillStyle = '#1a1a2e';
        ctx.font = '600 13px -apple-system, "Segoe UI", sans-serif';
        ctx.textBaseline = 'middle';
        ctx.textAlign = 'left';
        ctx.fillText('$' + (v.value / 1e9).toFixed(1) + 'B', PAD_LEFT + w + 6, y + BAR_H / 2);

        const AV_SZ = Math.min(BAR_H - 4, 32);
        const avX = PAD_LEFT - 4 - AV_SZ;
        const avY = y + (BAR_H - AV_SZ) / 2;
        const img = _loadAvatar(v.image_url);
        ctx.save();
        ctx.beginPath();
        ctx.arc(avX + AV_SZ / 2, avY + AV_SZ / 2, AV_SZ / 2, 0, Math.PI * 2);
        ctx.closePath();
        ctx.fillStyle = '#eee';
        ctx.fill();
        if (img && img._loaded) {
            ctx.clip();
            try { ctx.drawImage(img, avX, avY, AV_SZ, AV_SZ); } catch (_) {}
        } else {
            ctx.fillStyle = '#aaa';
            ctx.font = '600 14px -apple-system, sans-serif';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText((v.name || '?')[0], avX + AV_SZ / 2, avY + AV_SZ / 2 + 1);
        }
        ctx.restore();

        ctx.fillStyle = '#1a1a2e';
        ctx.font = '500 13px -apple-system, "Segoe UI", sans-serif';
        ctx.textBaseline = 'middle';
        ctx.textAlign = 'right';
        const nameMaxW = avX - 6;
        let nm = v.name || '';
        if (ctx.measureText(nm).width > nameMaxW) {
            while (nm.length > 3 && ctx.measureText(nm + '…').width > nameMaxW) {
                nm = nm.slice(0, -1);
            }
            nm += '…';
        }
        ctx.fillText(nm, avX - 4, y + BAR_H / 2);

        ctx.restore();

        drawnRows.push({ key: v.key, y, h: BAR_H, x: PAD_LEFT, w, name: v.name, person_id: v.person_id });
    }
    r.hitRows = drawnRows;

    // Schedule the next frame as long as we're playing OR still tweening
    // toward a static cursor target (smoothing not yet settled).
    let stillSmoothing = false;
    for (const [k, t] of targetMap) {
        const sm = r.smoothed.get(k);
        if (!sm) continue;
        if (Math.abs(sm.rank - t.rank) > 0.01 ||
            Math.abs(sm.value - t.value) > t.value * 0.001 ||
            Math.abs(sm.alpha - t.alpha) > 0.01) {
            stillSmoothing = true; break;
        }
    }
    if (Math.abs(r.smoothedMaxValue - targetMax) > targetMax * 0.001) {
        stillSmoothing = true;
    }

    if (r.playing || stillSmoothing) {
        r.raf = requestAnimationFrame(_drawRaceFrame);
    } else {
        r.raf = null;
    }

    // Notify Alpine to refresh year label / slider / legend. Throttled
    // to ~10Hz so we don't trigger reactivity on every 60Hz frame.
    if (typeof r.onTick === "function") {
        const tickNow = Math.floor(now / 100);
        if (tickNow !== r._lastTickEmit) {
            r._lastTickEmit = tickNow;
            try { r.onTick(); } catch (_) {}
        }
    }
}

// ─── Treemap renderer (squarified) ─────────────────────────────────
// Used by the deep-dive panel's Public market tab. Canvas-based to
// avoid pulling in chartjs-chart-treemap as a dependency. Tile size =
// market cap, color = sector, label = ticker (or initials when the
// tile is too small for the ticker to fit).

// Squarified treemap: greedy algorithm by Bruls/Huijing/van Wijk.
// Items must be pre-sorted by value descending. We split the rect
// into rows that minimize aspect ratio.
function _squarifyTreemap(items, x, y, w, h) {
    // Bruls/Huijing/van Wijk squarified treemap.
    //
    // Build each row ALONG the short side of the remaining rectangle:
    //   - row stacks items along the short axis
    //   - thickness (perpendicular to the short axis) = rowSum / shortSide
    //   - after committing, advance the cursor along the LONG axis
    //
    // Concretely on a 492×380 rect (short = 380, long = 492):
    //   - items in the row stack vertically (along height = 380)
    //   - row width = rowSum / 380, drawn from curX, full short-axis tall
    //   - after commit: curX += width; remW -= width
    //   - the remainder is (492 - width) × 380 — still wider than tall,
    //     so the next row is again vertical; eventually remW < remH
    //     and we flip to horizontal rows
    const out = [];
    const total = items.reduce((s, i) => s + (i.value || 0), 0);
    if (total <= 0 || items.length === 0) return out;

    function _layoutRow(row, shortSide, startX, startY, shortIsHeight) {
        // shortIsHeight=true → short axis is vertical → row is a
        // vertical column of items, thickness measured horizontally.
        // shortIsHeight=false → short axis is horizontal → row is a
        // horizontal strip of items, thickness measured vertically.
        const rowSum = row.reduce((s, i) => s + i.value, 0);
        const rowThickness = rowSum / shortSide;
        let cursor = 0;
        for (const item of row) {
            const itemLen = item.value / rowThickness;
            const tile = shortIsHeight ? {
                x: startX,
                y: startY + cursor,
                w: rowThickness,   // perpendicular to the row direction
                h: itemLen,        // along the row direction (vertical)
            } : {
                x: startX + cursor,
                y: startY,
                w: itemLen,        // along the row direction (horizontal)
                h: rowThickness,
            };
            out.push({ item: item.item, ...tile });
            cursor += itemLen;
        }
        return rowThickness;
    }

    function _worstRatio(row, shortSide) {
        const sum = row.reduce((s, i) => s + i.value, 0);
        if (sum <= 0) return Infinity;
        const minV = Math.min(...row.map(i => i.value));
        const maxV = Math.max(...row.map(i => i.value));
        const s2 = shortSide * shortSide;
        const r2 = sum * sum;
        return Math.max(s2 * maxV / r2, r2 / (s2 * minV));
    }

    const scale = (w * h) / total;
    const scaled = items.map(i => ({
        item: i,
        value: (i.value || 0) * scale,
    })).filter(i => i.value > 0);

    let curX = x, curY = y, remW = w, remH = h;
    let row = [];
    let i = 0;
    while (i < scaled.length) {
        const item = scaled[i];
        const shortSide = Math.min(remW, remH);
        if (shortSide <= 0) break;
        const candidate = [...row, item];
        const candidateWorst = _worstRatio(candidate, shortSide);
        const currentWorst = row.length ? _worstRatio(row, shortSide) : Infinity;
        if (row.length === 0 || candidateWorst <= currentWorst) {
            row = candidate;
            i++;
        } else {
            // Commit the row. The short side becomes the row's
            // along-axis; thickness is consumed off the long side.
            const shortIsHeight = remH <= remW;
            const thickness = _layoutRow(row, shortSide, curX, curY, shortIsHeight);
            if (shortIsHeight) {
                // Row was a vertical strip; advance horizontally.
                curX += thickness; remW -= thickness;
            } else {
                // Row was a horizontal strip; advance vertically.
                curY += thickness; remH -= thickness;
            }
            row = [];
            // Don't advance i — re-evaluate the current item against
            // the new (smaller) remainder.
        }
    }
    if (row.length) {
        const shortSide = Math.min(remW, remH);
        const shortIsHeight = remH <= remW;
        _layoutRow(row, shortSide, curX, curY, shortIsHeight);
    }
    return out;
}

function _drawMarketTreemap(canvas, companies) {
    if (!canvas || !companies || !companies.length) return [];
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const cssW = canvas.clientWidth, cssH = canvas.clientHeight;
    if (canvas.width !== cssW * dpr || canvas.height !== cssH * dpr) {
        canvas.width = cssW * dpr; canvas.height = cssH * dpr;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    ctx.clearRect(0, 0, cssW, cssH);

    // Build the items: only rows with positive market cap. Wikidata
    // rows (no live cap) are appended at the bottom as small same-
    // sized tiles so the user still sees them as "we know about it".
    const withCap = companies.filter(c => (c.market_cap_usd || 0) > 0);
    const items = withCap.map(c => ({
        ...c,
        value: c.market_cap_usd,
    }));
    if (!items.length) return [];

    const tiles = _squarifyTreemap(items, 0, 0, cssW, cssH);

    for (const tile of tiles) {
        const c = tile.item;
        const color = _industryColor(c.sector || c.industry || 'Other');
        ctx.fillStyle = color;
        ctx.fillRect(tile.x, tile.y, tile.w, tile.h);
        ctx.strokeStyle = 'rgba(255,255,255,0.85)';
        ctx.lineWidth = 1;
        ctx.strokeRect(tile.x + 0.5, tile.y + 0.5, tile.w - 1, tile.h - 1);

        // Label: ticker if it fits, else initials, else nothing
        if (tile.w < 22 || tile.h < 16) continue;
        const ticker = c.ticker || (c.name || '?').slice(0, 4).toUpperCase();
        ctx.fillStyle = '#1a1a2e';
        ctx.font = '600 11px -apple-system, "Segoe UI", sans-serif';
        ctx.textBaseline = 'top';
        ctx.textAlign = 'left';
        ctx.fillText(ticker, tile.x + 4, tile.y + 4);
        // Cap underneath when there's room
        if (tile.h >= 32 && tile.w >= 50) {
            ctx.font = '500 10px -apple-system, sans-serif';
            ctx.fillStyle = 'rgba(26,26,46,0.7)';
            const cap = (c.market_cap_usd || 0) / 1e9;
            const capStr = cap >= 1000 ? `$${(cap / 1000).toFixed(1)}T` : `$${cap.toFixed(0)}B`;
            ctx.fillText(capStr, tile.x + 4, tile.y + 18);
        }
    }
    return tiles;
}

function insightsMixin() {
    return {
        // ─── Cross-filter state ──────────────────────────────────────────
        insightsFilters: {
            country: '',
            industry: '',
            yearFrom: 2001,
            yearTo: new Date().getFullYear(),
        },
        // Charts/data
        insightsTopOverTime: null,
        insightsCohortSurvival: null,
        // Which cohort category (still_listed / dropped / died /
        // never_tracked) is expanded. null = all collapsed.
        cohortOpenCategory: null,
        insightsInequality: null,
        insightsCountOverTime: null,
        insightsCorrelation: null,
        insightsGeoMigration: null,
        // Which cross-border-flow row is expanded (null = all collapsed).
        migrationOpenIdx: null,
        // Reactive tick used to drive Alpine re-renders of the year
        // label, slider position, and industry legend. The renderer's
        // onTick callback bumps this every ~100ms while animating.
        insightsRaceTick: 0,
        insightsRaceYearIdx: 0,
        insightsRacePlaying: false,
        _insightsRaceTimer: null,
        insightsCountBy: 'country',
        insightsCohortYear: 2001,
        insightsCorrelationN: 30,
        insightsCorrelationDays: 365,

        // ─── Pair-comparison panel (opened from a heatmap cell click) ────
        comparePairOpen: false,
        comparePairLoading: false,
        comparePair: null,         // { a, b, correlation, history, shared }
        // Per-side biography expansion state. The biography is
        // truncated by default (3 lines via line-clamp); a Read-more
        // button toggles to the full paragraph.
        comparePairBioOpen: { a: false, b: false },

        // ─── Country / Industry deep-dive side panel ─────────────────────
        deepDiveOpen: false,
        deepDiveKind: '',          // 'country' | 'industry'
        deepDiveValue: '',
        deepDiveTab: 'billionaires', // 'billionaires' | 'market'
        deepDiveLoading: false,
        deepDiveBillionaires: null,
        deepDiveMarket: null,
        deepDiveShowList: false,   // treemap is the primary view; list is opt-in
        _deepDiveTreemapTiles: null,

        // ─── Loaders ─────────────────────────────────────────────────────
        async loadInsights() {
            // Fire all the chart endpoints in parallel; render as each
            // returns. The shared filter state is read here; child
            // helpers don't need to take params. The analytics-mixin's
            // loadAnalytics() drives the charts that used to live on the
            // Analytics tab — wealth comparison, concentration, by-
            // industry/country/gender/age — and now share this tab.
            await Promise.all([
                this.loadInsightsTopOverTime(),
                this.loadInsightsCount(),
                this.loadInsightsInequality(),
                this.loadInsightsCohort(),
                this.loadInsightsCorrelation(),
                this.loadInsightsGeoMigration(),
                this.loadAnalytics(),
            ]);
        },

        async loadInsightsTopOverTime() {
            const f = this.insightsFilters;
            const params = new URLSearchParams({
                n: '12', year_from: String(f.yearFrom), year_to: String(f.yearTo),
            });
            if (f.country) params.set('country', f.country);
            if (f.industry) params.set('industry', f.industry);
            // Continuous-timeline endpoint: per-person monthly series.
            // The renderer interpolates between months for one smooth
            // animation from start to end without rescaling jumps.
            const data = await fetch(
                `/api/insights/top-over-time-series?${params}`
            ).then(r => r.json());
            this.insightsTopOverTime = data;
            this.$nextTick(() => this.renderRaceChart());
        },

        async loadInsightsCount() {
            const f = this.insightsFilters;
            const params = new URLSearchParams({
                year_from: String(f.yearFrom), year_to: String(f.yearTo),
                by: this.insightsCountBy,
            });
            const data = await fetch(`/api/insights/count-over-time?${params}`).then(r => r.json());
            this.insightsCountOverTime = data;
            this.$nextTick(() => this.renderCountChart());
        },

        async loadInsightsInequality() {
            const f = this.insightsFilters;
            const params = new URLSearchParams({
                year_from: String(f.yearFrom), year_to: String(f.yearTo),
            });
            if (f.country) params.set('country', f.country);
            if (f.industry) params.set('industry', f.industry);
            const data = await fetch(`/api/insights/inequality?${params}`).then(r => r.json());
            this.insightsInequality = data;
            this.$nextTick(() => this.renderInequalityChart());
        },

        async loadInsightsCohort() {
            const params = new URLSearchParams({
                year: String(this.insightsCohortYear), top: '100',
            });
            this.insightsCohortSurvival = await fetch(
                `/api/insights/cohort-survival?${params}`
            ).then(r => r.json());
            // Switching years clears the open drill-down so the user
            // doesn't see a stale member list under a new cohort.
            this.cohortOpenCategory = null;
        },

        // Click-to-expand on a cohort tile. Clicking the active tile
        // collapses it; clicking a different tile switches.
        toggleCohortCategory(category) {
            if (this.cohortOpenCategory === category) {
                this.cohortOpenCategory = null;
            } else {
                this.cohortOpenCategory = category;
            }
        },

        // Human-readable category label for the drill-down header.
        cohortCategoryLabel(category) {
            return {
                still_listed: 'Still listed',
                dropped: 'Dropped off',
                died: 'Died',
                never_tracked: 'Never tracked',
            }[category] || category || '';
        },

        async loadInsightsCorrelation() {
            const params = new URLSearchParams({
                n: String(this.insightsCorrelationN),
                days: String(this.insightsCorrelationDays),
                threshold: '0.7',
            });
            this.insightsCorrelation = await fetch(
                `/api/insights/wealth-correlation?${params}`
            ).then(r => r.json());
            this.$nextTick(() => this.renderCorrelationHeatmap());
        },

        async loadInsightsGeoMigration() {
            this.insightsGeoMigration = await fetch(
                '/api/insights/geo-migration'
            ).then(r => r.json());
        },

        // ─── Cross-filter actions ────────────────────────────────────────

        setInsightsCountry(country) {
            // Toggle: clicking the active country resets to "all"
            if (this.insightsFilters.country === country) country = '';
            this.insightsFilters.country = country;
            this.loadInsights();
            // Also open the deep-dive panel for the country (skip when
            // clearing the filter).
            if (country) this.openDeepDive('country', country);
        },

        setInsightsIndustry(industry) {
            if (this.insightsFilters.industry === industry) industry = '';
            this.insightsFilters.industry = industry;
            this.loadInsights();
            if (industry) this.openDeepDive('industry', industry);
        },

        clearInsightsFilters() {
            this.insightsFilters = {
                country: '', industry: '',
                yearFrom: 2001, yearTo: new Date().getFullYear(),
            };
            this.loadInsights();
        },

        setInsightsYearRange(from, to) {
            this.insightsFilters.yearFrom = from;
            this.insightsFilters.yearTo = to;
            this.loadInsights();
        },

        // ─── Bar-chart race ──────────────────────────────────────────────

        // The cursor is a fractional month offset into the timeline. The
        // year displayed in the header derives from cursor + start.
        raceCurrentYearLabel() {
            void this.insightsRaceTick;  // create reactivity dependency
            if (!_race.startYm) return '';
            const totalMonths = _race.startMonths + _race.cursorMonths;
            return String(Math.floor(totalMonths / 12));
        },

        raceCurrentMonthLabel() {
            void this.insightsRaceTick;
            if (!_race.startYm) return '';
            const totalMonths = _race.startMonths + _race.cursorMonths;
            return _monthsToYm(totalMonths);
        },

        // Slider position 0..1000 (used for the input range value)
        raceSliderValue() {
            void this.insightsRaceTick;
            if (!_race.totalMonths) return 0;
            return Math.round((_race.cursorMonths / _race.totalMonths) * 1000);
        },

        setRaceSliderValue(v) {
            const clamped = Math.max(0, Math.min(1000, +v));
            _race.cursorMonths = (clamped / 1000) * _race.totalMonths;
            // Pause auto-play when the user scrubs
            _race.playing = false;
            this.insightsRacePlaying = false;
            if (!_race.raf && _race.canvas) {
                _race.raf = requestAnimationFrame(_drawRaceFrame);
            }
        },

        raceIndustriesInFrame() {
            // Distinct industries that are CURRENTLY visible (smoothed
            // alpha > 0.5). Drives the legend below the race chart.
            void this.insightsRaceTick;
            const seen = new Map();
            for (const v of _race.smoothed.values()) {
                if (v.alpha < 0.5) continue;
                const ind = v.industry || 'Other';
                if (!seen.has(ind)) seen.set(ind, _industryColor(ind));
            }
            return [...seen.entries()].map(([name, color]) => ({ name, color }));
        },

        toggleRacePlay() {
            if (_race.playing) {
                _race.playing = false;
                this.insightsRacePlaying = false;
                return;
            }
            // If we're at the end, restart from the beginning
            if (_race.cursorMonths >= _race.totalMonths - 0.01) {
                _race.cursorMonths = 0;
            }
            _race.playing = true;
            _race.lastTickAt = performance.now();
            this.insightsRacePlaying = true;
            if (!_race.raf && _race.canvas) {
                _race.raf = requestAnimationFrame(_drawRaceFrame);
            }
        },

        renderRaceChart() {
            const canvas = document.getElementById('insightsRaceChart');
            if (!canvas) return;
            // Wire canvas + click handler once
            if (!_race.canvas) {
                _race.canvas = canvas;
                _race.ctx = canvas.getContext('2d');
                // Forward animation-frame ticks back into Alpine so the
                // year label, slider position, and legend stay in sync
                // with what's actually drawn.
                _race.onTick = () => { this.insightsRaceTick++; };
                canvas.addEventListener('click', (e) => {
                    const rect = canvas.getBoundingClientRect();
                    const y = e.clientY - rect.top;
                    const x = e.clientX - rect.left;
                    const hit = (_race.hitRows || []).find(
                        r => y >= r.y && y <= r.y + r.h && x >= r.x - 200 && x <= r.x + r.w + 60
                    );
                    if (hit?.person_id) this.openProfile(hit.person_id);
                });
                canvas.addEventListener('mousemove', (e) => {
                    const rect = canvas.getBoundingClientRect();
                    const y = e.clientY - rect.top;
                    const x = e.clientX - rect.left;
                    const hit = (_race.hitRows || []).find(
                        r => y >= r.y && y <= r.y + r.h && x >= r.x - 200 && x <= r.x + r.w + 60
                    );
                    canvas.style.cursor = hit?.person_id ? 'pointer' : 'default';
                });
            }
            // Initialize the timeline. Cursor lands at the end (current
            // year) so the first paint shows the latest snapshot —
            // matches the prior UX where year=N is the default view.
            _initRaceTimeline(this.insightsTopOverTime || { persons: [], start: '', end: '' }, 12);
        },

        // ─── Count over time ─────────────────────────────────────────────

        renderCountChart() {
            const canvas = document.getElementById('insightsCountChart');
            if (!canvas) return;
            const d = this.insightsCountOverTime;
            if (!d) return;
            const years = d.years || (d.series?.map(s => s.year));
            let datasets = [];
            const palette = ['#4ecdc4', '#6c5ce7', '#f39c12', '#ff6b6b',
                             '#95e1d3', '#fd79a8', '#74b9ff', '#a29bfe',
                             '#fdcb6e', '#55efc4'];
            if (d.by === 'total') {
                datasets = [{
                    label: 'Total billionaires',
                    data: d.series.map(s => s.count),
                    borderColor: '#4ecdc4', backgroundColor: 'rgba(78,205,196,0.15)',
                    fill: true, tension: 0.2,
                }];
            } else {
                let i = 0;
                for (const [name, ser] of Object.entries(d.series)) {
                    const color = palette[i++ % palette.length];
                    datasets.push({
                        label: name,
                        data: ser.map(p => p.count),
                        borderColor: color, backgroundColor: color + '22',
                        fill: false, tension: 0.2,
                    });
                }
            }
            // Switching `by` changes the dataset shape entirely, so we
            // tear down the chart in that case. Same structure → in-place.
            _destroyChart('insightsCountChart');
            const chart = new Chart(canvas, {
                type: 'line',
                data: { labels: years, datasets },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    plugins: {
                        legend: { position: 'right', labels: { boxWidth: 12 } },
                        tooltip: { mode: 'index', intersect: false },
                    },
                    onClick: (evt, els) => {
                        if (!els.length) return;
                        const dsLabel = datasets[els[0].datasetIndex].label;
                        if (d.by === 'country') this.setInsightsCountry(dsLabel);
                        else if (d.by === 'industry') this.setInsightsIndustry(dsLabel);
                    },
                },
            });
            _setChart('insightsCountChart', chart);
        },

        // ─── Inequality (Gini over time) ─────────────────────────────────

        renderInequalityChart() {
            const canvas = document.getElementById('insightsInequalityChart');
            if (!canvas) return;
            const d = this.insightsInequality;
            if (!d?.series?.length) return;
            const labels = d.series.map(s => s.year);
            const ginis = d.series.map(s => s.gini);
            const top10s = d.series.map(s => s.top10_share * 100);
            _destroyChart('insightsInequalityChart');
            const chart = new Chart(canvas, {
                type: 'line',
                data: {
                    labels,
                    datasets: [
                        {
                            label: 'Gini (left)',
                            data: ginis,
                            borderColor: '#6c5ce7', backgroundColor: 'rgba(108,92,231,0.15)',
                            yAxisID: 'y', tension: 0.2, fill: true,
                        },
                        {
                            label: 'Top-10 share (%, right)',
                            data: top10s,
                            borderColor: '#f39c12',
                            yAxisID: 'y1', tension: 0.2,
                        },
                    ],
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    interaction: { mode: 'index', intersect: false },
                    plugins: { legend: { position: 'top' } },
                    scales: {
                        y: { type: 'linear', position: 'left', min: 0, max: 1, title: { display: true, text: 'Gini' } },
                        y1: { type: 'linear', position: 'right', min: 0, max: 100, title: { display: true, text: 'Top-10 share %' }, grid: { drawOnChartArea: false } },
                    },
                },
            });
            _setChart('insightsInequalityChart', chart);
        },

        // ─── Correlation heatmap ─────────────────────────────────────────

        renderCorrelationHeatmap() {
            // Canvas-based heatmap. The previous implementation built
            // N² DOM divs, which works for N=30 but stalls the browser
            // at N=200+. A single <canvas> renders 500×500 = 250k
            // cells in milliseconds via fillRect.
            //
            // Tooltip is a separately-positioned <div> driven by
            // mousemove → cell-index math; cheaper than per-cell event
            // listeners and works at any N.
            const canvas = document.getElementById('insightsCorrHeatmap');
            const tooltip = document.getElementById('insightsCorrTooltip');
            if (!canvas || !this.insightsCorrelation) return;
            const { persons, matrix } = this.insightsCorrelation;
            const n = persons.length;
            if (!n) return;

            // Target a heatmap whose total CSS size is ~420px on the
            // long edge, so we always match the surrounding card.
            const SIZE_PX = 420;
            const cellPx = Math.max(1, Math.floor(SIZE_PX / n));
            const totalPx = cellPx * n;

            // Up-scale for retina so the colors don't smear.
            const dpr = window.devicePixelRatio || 1;
            canvas.style.width = `${totalPx}px`;
            canvas.style.height = `${totalPx}px`;
            canvas.width = totalPx * dpr;
            canvas.height = totalPx * dpr;
            const ctx = canvas.getContext('2d');
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

            // Diverging color: red(-1) → white(0) → teal(+1). Same
            // scale as the previous DOM version.
            for (let i = 0; i < n; i++) {
                for (let j = 0; j < n; j++) {
                    const r = matrix[i]?.[j];
                    if (r === null || r === undefined) {
                        ctx.fillStyle = '#eee';
                    } else {
                        const v = Math.max(-1, Math.min(1, r));
                        const hue = v > 0 ? 174 : 0;
                        const sat = Math.abs(v) * 80;
                        const lum = 100 - Math.abs(v) * 50;
                        ctx.fillStyle = `hsl(${hue} ${sat}% ${lum}%)`;
                    }
                    ctx.fillRect(j * cellPx, i * cellPx, cellPx, cellPx);
                }
            }

            // Wire (or rewire) the tooltip handler. We attach onto the
            // canvas itself — single listener regardless of N.
            const onMove = (e) => {
                const rect = canvas.getBoundingClientRect();
                const x = e.clientX - rect.left;
                const y = e.clientY - rect.top;
                const j = Math.floor(x / cellPx);
                const i = Math.floor(y / cellPx);
                if (i < 0 || i >= n || j < 0 || j >= n) {
                    tooltip.style.display = 'none';
                    return;
                }
                const r = matrix[i]?.[j];
                tooltip.innerHTML = (
                    `<strong>${persons[i].name}</strong><br>` +
                    `<strong>${persons[j].name}</strong><br>` +
                    `r = ${r === null || r === undefined ? '—' : r.toFixed(3)}`
                );
                tooltip.style.display = 'block';
                // Position relative to the wrap; nudge so the cursor
                // doesn't cover the box.
                const wrap = canvas.parentElement;
                const wrapRect = wrap.getBoundingClientRect();
                tooltip.style.left = `${e.clientX - wrapRect.left + 12}px`;
                tooltip.style.top = `${e.clientY - wrapRect.top + 12}px`;
            };
            const onLeave = () => { tooltip.style.display = 'none'; };
            canvas.onmousemove = onMove;
            canvas.onmouseleave = onLeave;
            // Click → open the pair-comparison modal. Skip the
            // diagonal (self) and any cell with no overlap data.
            canvas.onclick = (e) => {
                const rect = canvas.getBoundingClientRect();
                const x = e.clientX - rect.left;
                const y = e.clientY - rect.top;
                const j = Math.floor(x / cellPx);
                const i = Math.floor(y / cellPx);
                if (i < 0 || i >= n || j < 0 || j >= n) return;
                if (i === j) return;
                const r = matrix[i]?.[j];
                if (r === null || r === undefined) return;
                this.openComparePair(persons[i], persons[j]);
            };
        },

        // ─── Pair comparison modal ──────────────────────────────────────
        // Opened from a click on a heatmap cell. Reuses the deep-dive
        // panel pattern (overlay + sliding aside) for visual consistency.
        async openComparePair(personA, personB) {
            this.comparePairOpen = true;
            this.comparePairLoading = true;
            this.comparePair = null;
            // Reset bio expansion when switching pair — opening a new
            // pair shouldn't inherit the previous one's "expanded" state.
            this.comparePairBioOpen = { a: false, b: false };
            try {
                const days = this.insightsCorrelationDays || 365;
                const url = `/api/insights/compare-pair?a=${personA.person_id}` +
                            `&b=${personB.person_id}&days=${days}`;
                this.comparePair = await fetch(url).then(r => r.json());
            } finally {
                this.comparePairLoading = false;
            }
            this.$nextTick(() => this.renderComparePairChart());
        },

        // Asset breakdown for the compare panel: returns sized
        // segments (public/private/cash) for the small horizontal
        // bar. Segments with zero value are omitted so the bar
        // doesn't have invisible flex children.
        compareAssetSegments(snap) {
            if (!snap) return [];
            const total = (snap.public_assets_total || 0)
                        + (snap.private_assets_total || 0)
                        + (snap.cash_assets_total || 0);
            if (!total) return [];
            const segs = [
                { label: 'Public', cls: 'public',
                  value: snap.public_assets_total || 0 },
                { label: 'Private', cls: 'private',
                  value: snap.private_assets_total || 0 },
                { label: 'Cash', cls: 'cash',
                  value: snap.cash_assets_total || 0 },
            ];
            return segs
                .filter(s => s.value > 0)
                .map(s => ({ ...s, share: s.value / total }));
        },

        closeComparePair() {
            this.comparePairOpen = false;
            // Tear down the chart so reopening always builds fresh.
            const prev = _insightsCharts.get('comparePairChart');
            if (prev) {
                prev.destroy();
                _insightsCharts.delete('comparePairChart');
            }
        },

        renderComparePairChart() {
            const canvas = document.getElementById('comparePairChart');
            if (!canvas || !this.comparePair?.history?.length) return;
            // Rebuild on every open — same canvas, fresh data.
            const prev = _insightsCharts.get('comparePairChart');
            if (prev) prev.destroy();
            const h = this.comparePair.history;
            const aName = this.comparePair.a.name;
            const bName = this.comparePair.b.name;
            const chart = new Chart(canvas, {
                type: 'line',
                data: {
                    labels: h.map(p => p.date),
                    datasets: [
                        {
                            label: aName,
                            data: h.map(p => p.a_norm),
                            borderColor: '#6c5ce7',
                            backgroundColor: 'rgba(108,92,231,0.08)',
                            borderWidth: 2,
                            fill: false,
                            pointRadius: 0,
                            tension: 0.15,
                        },
                        {
                            label: bName,
                            data: h.map(p => p.b_norm),
                            borderColor: '#5ad1c7',
                            backgroundColor: 'rgba(90,209,199,0.08)',
                            borderWidth: 2,
                            fill: false,
                            pointRadius: 0,
                            tension: 0.15,
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { mode: 'index', intersect: false },
                    plugins: {
                        legend: { position: 'top', align: 'start' },
                        tooltip: {
                            callbacks: {
                                // Show both the indexed value (rebased
                                // to 100) and the underlying USD wealth
                                // for context.
                                afterBody: (items) => {
                                    if (!items?.length) return '';
                                    const idx = items[0].dataIndex;
                                    const row = h[idx];
                                    return [
                                        `${aName}: ${formatWealth(row.a_usd)}`,
                                        `${bName}: ${formatWealth(row.b_usd)}`,
                                    ];
                                },
                                label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y?.toFixed(1)}`,
                            },
                        },
                    },
                    scales: {
                        x: { ticks: { maxTicksLimit: 8 }, grid: { display: false } },
                        y: { title: { display: true, text: 'Indexed (start = 100)' } },
                    },
                },
            });
            _insightsCharts.set('comparePairChart', chart);
        },

        // ─── Migration table view (no map dep) ───────────────────────────

        topMigrationFlows(n = 12) {
            const flows = this.insightsGeoMigration?.flows || [];
            return flows.filter(f => !f.is_self_flow).slice(0, n);
        },

        topResidenceCountries(n = 10) {
            return (this.insightsGeoMigration?.nodes || []).slice(0, n);
        },

        // ─── Deep-dive panel ─────────────────────────────────────────────

        async openDeepDive(kind, value) {
            this.deepDiveKind = kind;       // 'country' or 'industry'
            this.deepDiveValue = value;
            this.deepDiveTab = 'billionaires';
            this.deepDiveOpen = true;
            this.deepDiveBillionaires = null;
            this.deepDiveMarket = null;
            this.deepDiveLoading = true;
            this.deepDiveShowList = false;   // hide the row list on each open
            // Load billionaires synchronously (fast, our DB) and market in
            // the background — Yahoo can take a few seconds for a cold
            // cache, no point making the user wait.
            try {
                this.deepDiveBillionaires = await this.fetchDeepDiveBillionaires();
            } finally {
                this.deepDiveLoading = false;
            }
            this.fetchDeepDiveMarket();  // fire and forget
        },

        closeDeepDive() {
            this.deepDiveOpen = false;
        },

        async fetchDeepDiveBillionaires() {
            // Use the live /api/billionaires endpoint with a server-side
            // filter rather than reusing top-over-time-series. Two
            // reasons:
            //
            // 1. The chart on the Insights tab ("Wealth by industry"
            //    / "Wealth by country") aggregates the latest snapshot
            //    grouped by p.industry / p.citizenship. The user
            //    expects the deep-dive total to match the bar height —
            //    same query, same answer.
            //
            // 2. top-over-time-series pulls monthly wealth history for
            //    every person in the union of yearly top-Ns. That's
            //    tens of MB on the production dataset. The deep-dive
            //    only shows the latest value, so the monthly history
            //    is wasted bandwidth + compute.
            const params = new URLSearchParams();
            if (this.deepDiveKind === 'country') {
                params.set('country', this.deepDiveValue);
            } else {
                params.set('industry', this.deepDiveValue);
            }
            // Largest fortunes first — same order as the bar chart.
            params.set('sort', '-net_worth_usd');
            const data = await fetch(
                `/api/billionaires?${params}`
            ).then(r => r.json());
            const persons = (data.data || []).map(r => ({
                person_id: r.person_id,
                name: r.common_name,
                citizenship: r.citizenship,
                industry: r.industry,
                net_worth_usd: r.net_worth_usd,
            })).filter(p => p.net_worth_usd > 0);
            const total = persons.reduce(
                (s, p) => s + (p.net_worth_usd || 0), 0
            );
            return { persons, total_wealth_usd: total };
        },

        async fetchDeepDiveMarket() {
            this.deepDiveMarket = { loading: true };
            const path = this.deepDiveKind === 'country'
                ? `/api/market/by-country?country=${encodeURIComponent(this.deepDiveValue)}&limit=100`
                : `/api/market/by-industry?industry=${encodeURIComponent(this.deepDiveValue)}&limit=100`;
            try {
                const data = await fetch(path).then(r => r.json());
                this.deepDiveMarket = data;
                // Treemap is the primary view of the market tab; render on
                // the next tick so the canvas is in the DOM.
                this.$nextTick(() => {
                    this.renderDeepDiveTreemap();
                    this.renderDeepDiveDonut();
                });
            } catch (e) {
                this.deepDiveMarket = { error: String(e) };
            }
        },

        renderDeepDiveTreemap() {
            const canvas = document.getElementById('deepDiveTreemap');
            if (!canvas || !this.deepDiveMarket?.companies) return;
            // The canvas may exist in the DOM but still have zero
            // dimensions if its `x-show` parent hasn't been painted yet
            // (Alpine sets `display:none` synchronously on hide). Defer
            // a frame and retry — the canvas will have its computed
            // size by then.
            if (canvas.clientWidth === 0 || canvas.clientHeight === 0) {
                requestAnimationFrame(() => this.renderDeepDiveTreemap());
                return;
            }
            this._deepDiveTreemapTiles = _drawMarketTreemap(
                canvas, this.deepDiveMarket.companies,
            );
            // Wire up hover + click. We re-bind on every render — the
            // canvas instance is the same so we use a flag to attach
            // listeners once.
            if (!canvas._hoverWired) {
                canvas._hoverWired = true;
                canvas.addEventListener('mousemove', (e) => {
                    const rect = canvas.getBoundingClientRect();
                    const x = e.clientX - rect.left, y = e.clientY - rect.top;
                    const tiles = this._deepDiveTreemapTiles || [];
                    const hit = tiles.find(t =>
                        x >= t.x && x <= t.x + t.w && y >= t.y && y <= t.y + t.h
                    );
                    canvas.style.cursor = hit?.item.ticker ? 'pointer' : 'default';
                    if (hit) canvas.title = `${hit.item.name} (${hit.item.ticker || '—'}) · ${formatWealth(hit.item.market_cap_usd || 0)} · ${hit.item.sector || 'Other'}`;
                });
                canvas.addEventListener('click', (e) => {
                    const rect = canvas.getBoundingClientRect();
                    const x = e.clientX - rect.left, y = e.clientY - rect.top;
                    const tiles = this._deepDiveTreemapTiles || [];
                    const hit = tiles.find(t =>
                        x >= t.x && x <= t.x + t.w && y >= t.y && y <= t.y + t.h
                    );
                    if (hit?.item.ticker) {
                        window.open(`https://finance.yahoo.com/quote/${hit.item.ticker}`,
                                    '_blank', 'noopener');
                    }
                });
                // Re-render on resize
                let resizeTimer;
                window.addEventListener('resize', () => {
                    clearTimeout(resizeTimer);
                    resizeTimer = setTimeout(() => this.renderDeepDiveTreemap(), 200);
                });
            }
        },

        // Color used by both the treemap and the donut legend so the
        // two read as the same picture (Tech teal in both views, etc).
        sectorColor(name) {
            return _industryColor(name || 'Other');
        },

        renderDeepDiveDonut() {
            // Pick the right slice set + canvas for the current tab.
            // Country deep-dive → sectors; industry deep-dive → countries.
            const data = this.deepDiveMarket;
            if (!data) return;
            let slices, canvasId;
            if (this.deepDiveKind === 'country' && data.sectors?.length) {
                slices = data.sectors;
                canvasId = 'deepDiveSectorDonut';
            } else if (this.deepDiveKind === 'industry' && data.countries?.length) {
                slices = data.countries;
                canvasId = 'deepDiveCountryDonut';
            } else {
                return;
            }
            const canvas = document.getElementById(canvasId);
            if (!canvas) return;
            // Same x-show race as the treemap: defer until paint.
            if (canvas.clientWidth === 0 || canvas.clientHeight === 0) {
                requestAnimationFrame(() => this.renderDeepDiveDonut());
                return;
            }
            // Pin the canvas to a fixed square. Chart.js + flex parents
            // produce squished donuts when `responsive: true` because
            // it samples clientWidth before the flex item's width has
            // settled. We size manually here (with DPR for retina).
            const SIZE = 200;
            const dpr = window.devicePixelRatio || 1;
            canvas.style.width = `${SIZE}px`;
            canvas.style.height = `${SIZE}px`;
            canvas.width = SIZE * dpr;
            canvas.height = SIZE * dpr;
            // Rebuild the chart on every render (the canvas may have
            // been re-attached by Alpine after a tab switch).
            const prev = _insightsCharts.get(canvasId);
            if (prev) prev.destroy();
            const chart = new Chart(canvas, {
                type: 'doughnut',
                data: {
                    labels: slices.map(s => s.name),
                    datasets: [{
                        data: slices.map(s => s.market_cap_usd || 0),
                        backgroundColor: slices.map(s => _industryColor(s.name)),
                        borderColor: '#fff',
                        borderWidth: 2,
                    }],
                },
                options: {
                    // responsive=false: Chart.js will keep our exact
                    // canvas.width / canvas.height. devicePixelRatio
                    // makes it sharp on retina without smearing.
                    responsive: false,
                    maintainAspectRatio: true,
                    devicePixelRatio: dpr,
                    cutout: '60%',
                    animation: { duration: 250 },
                    plugins: {
                        legend: { display: false },  // we render our own legend
                        tooltip: {
                            callbacks: {
                                label: (ctx) => {
                                    const total = ctx.dataset.data.reduce((a, b) => a + b, 0);
                                    const pct = total ? (ctx.parsed / total * 100).toFixed(1) : 0;
                                    return `${ctx.label}: ${formatWealth(ctx.parsed)} (${pct}%)`;
                                },
                            },
                        },
                    },
                },
            });
            _insightsCharts.set(canvasId, chart);
        },
    };
}
