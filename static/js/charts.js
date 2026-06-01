// static/js/charts.js
// Shared Chart.js wrappers used by analytics + wealth charts.

const CHART_COLORS = ['#4ecdc4', '#ff6b6b', '#6c5ce7', '#fdcb6e', '#a29bfe', '#00b894', '#e17055', '#0984e3', '#d63031', '#6ab04c'];

function renderBarChart(instances, canvasId, data, labelKey, valueKey) {
    const ctx = document.getElementById(canvasId);
    if (instances[canvasId]) instances[canvasId].destroy();
    const isWealth = valueKey === 'total_wealth' || valueKey === 'net_worth_usd';
    instances[canvasId] = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: data.map(d => d[labelKey]),
            datasets: [{ data: data.map(d => d[valueKey]), backgroundColor: CHART_COLORS }],
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: {
                legend: { display: false },
                tooltip: isWealth ? { callbacks: { label: c => formatWealth(c.parsed.x) } } : {},
            },
            scales: isWealth ? { x: { ticks: { callback: v => formatWealth(v) } } } : {},
        },
    });
}

function renderDoughnut(instances, canvasId, data) {
    const ctx = document.getElementById(canvasId);
    if (instances[canvasId]) instances[canvasId].destroy();
    instances[canvasId] = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: data.map(d => d.gender),
            datasets: [{ data: data.map(d => d.count), backgroundColor: ['#4ecdc4', '#ff6b6b', '#ccc'] }],
        },
        options: { responsive: true, maintainAspectRatio: false },
    });
}

function renderAgeChart(instances, canvasId, data) {
    const ctx = document.getElementById(canvasId);
    if (instances[canvasId]) instances[canvasId].destroy();
    instances[canvasId] = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: data.map(d => d.bracket),
            datasets: [{ data: data.map(d => d.count), backgroundColor: '#4ecdc4' }],
        },
        options: { responsive: true, maintainAspectRatio: false, animation: false, plugins: { legend: { display: false } } },
    });
}

function renderAgeWealthLine(instances, canvasId, points, smoothing) {
    const ctx = document.getElementById(canvasId);
    if (!ctx || !points || points.length === 0) return;
    if (instances[canvasId]) instances[canvasId].destroy();
    const bins = new Map();
    for (const p of points) {
        const age = p.age, w = p.net_worth_usd;
        if (age == null || w == null || w <= 0) continue;
        if (age < 25 || age > 100) continue;
        const cur = bins.get(age) || { total: 0, count: 0 };
        cur.total += w;
        cur.count += 1;
        bins.set(age, cur);
    }
    const ages = [...bins.keys()].sort((a, b) => a - b);
    if (ages.length === 0) return;
    const minAge = ages[0], maxAge = ages[ages.length - 1];
    // Densify so a missing year doesn't break the rolling window.
    const dense = [];
    for (let a = minAge; a <= maxAge; a++) {
        const b = bins.get(a);
        dense.push({ age: a, total: b ? b.total : 0, count: b ? b.count : 0 });
    }
    // Box-window rolling mean with half-width = smoothing (0 = raw).
    const w = Math.max(0, smoothing | 0);
    const data = dense.map((d, i) => {
        let total = 0, count = 0, n = 0;
        const lo = Math.max(0, i - w), hi = Math.min(dense.length - 1, i + w);
        for (let j = lo; j <= hi; j++) {
            total += dense[j].total;
            count += dense[j].count;
            n += 1;
        }
        return { x: d.age, y: total / n, count: Math.round(count / n) };
    });
    instances[canvasId] = new Chart(ctx, {
        type: 'line',
        data: {
            datasets: [{
                label: 'Aggregate net worth',
                data,
                parsing: { xAxisKey: 'x', yAxisKey: 'y' },
                borderColor: '#4ecdc4',
                backgroundColor: 'rgba(78, 205, 196, 0.15)',
                fill: true,
                tension: 0.3,
                pointRadius: 0,
                pointHoverRadius: 4,
                borderWidth: 2,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        title: items => `Age ${items[0].parsed.x}`,
                        label: c => {
                            const note = w > 0 ? ` (avg of ${2 * w + 1}y)` : '';
                            return `${formatWealth(c.parsed.y)}${note} · ~${c.raw.count} per year`;
                        },
                    },
                },
            },
            scales: {
                x: {
                    type: 'linear',
                    title: { display: true, text: 'Age' },
                    ticks: { stepSize: 5 },
                    grid: { color: '#f0f2f5' },
                },
                y: {
                    title: { display: true, text: 'Total net worth' },
                    ticks: { callback: v => formatWealth(v) },
                    grid: { color: '#f0f2f5' },
                    beginAtZero: true,
                },
            },
        },
    });
}

function renderConcentrationLine(instances, canvasId, data) {
    const ctx = document.getElementById(canvasId);
    if (!ctx || !data || data.length === 0) return;
    if (instances[canvasId]) instances[canvasId].destroy();
    const labels = data.map(d => d.date);
    const top1Pct = data.map(d => d.total ? (d.top_1 / d.total) * 100 : 0);
    const top10Pct = data.map(d => d.total ? (d.top_10 / d.total) * 100 : 0);
    const top100Pct = data.map(d => d.total ? (d.top_100 / d.total) * 100 : 0);
    instances[canvasId] = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [
                { label: 'Top 100', data: top100Pct, borderColor: '#6c5ce7', fill: false, tension: 0.1, pointRadius: 0 },
                { label: 'Top 10', data: top10Pct, borderColor: '#4ecdc4', fill: false, tension: 0.1, pointRadius: 0 },
                { label: 'Top 1', data: top1Pct, borderColor: '#ff6b6b', fill: false, tension: 0.1, pointRadius: 0 },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                tooltip: {
                    callbacks: { label: c => `${c.dataset.label}: ${c.parsed.y.toFixed(1)}%` },
                },
            },
            scales: {
                x: { type: 'category', ticks: { maxTicksLimit: 8 } },
                y: { ticks: { callback: v => v.toFixed(0) + '%' } },
            },
        },
    });
}
