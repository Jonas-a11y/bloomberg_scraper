// static/js/charts.js
// Shared Chart.js wrappers used by analytics + wealth charts.

const CHART_COLORS = ['#4ecdc4', '#ff6b6b', '#6c5ce7', '#fdcb6e', '#a29bfe', '#00b894', '#e17055', '#0984e3', '#d63031', '#6ab04c'];

function renderBarChart(instances, canvasId, data, labelKey, valueKey) {
    const ctx = document.getElementById(canvasId);
    if (instances[canvasId]) instances[canvasId].destroy();
    instances[canvasId] = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: data.map(d => d[labelKey]),
            datasets: [{ data: data.map(d => d[valueKey]), backgroundColor: CHART_COLORS }],
        },
        options: { indexAxis: 'y', responsive: true, plugins: { legend: { display: false } } },
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
        options: { responsive: true },
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
        options: { responsive: true, plugins: { legend: { display: false } } },
    });
}
