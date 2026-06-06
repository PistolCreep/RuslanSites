(function () {
    const dataNode = document.getElementById('analytics-chart-data');

    if (!dataNode || !window.Chart) {
        return;
    }

    let chartData;
    const activeCharts = [];

    try {
        chartData = JSON.parse(dataNode.textContent || '{}');
    } catch (error) {
        return;
    }

    function chartVar(name, fallback) {
        const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
        return value || fallback;
    }

    function chartTheme() {
        return {
            text: chartVar('--chart-text', '#edf6ff'),
            muted: chartVar('--chart-muted', 'rgba(255, 255, 255, .58)'),
            grid: chartVar('--chart-grid', 'rgba(129, 181, 255, .12)'),
            border: chartVar('--chart-border', 'rgba(2, 7, 17, .90)'),
            blue: chartVar('--chart-blue', '#38d9ff'),
            violet: chartVar('--chart-violet', '#8b5cf6'),
            green: chartVar('--chart-green', '#2ee6a6'),
            gold: chartVar('--chart-gold', '#ff9f43'),
            danger: chartVar('--chart-danger', '#ff5277'),
            mutedSolid: chartVar('--chart-muted-solid', '#77808f'),
            panel: chartVar('--chart-tooltip-bg', 'rgba(5, 12, 24, .94)')
        };
    }

    function hasValues(values) {
        return Array.isArray(values) && values.some((value) => Number(value) > 0);
    }

    function shortLabel(label, maxLength) {
        const text = String(label || '');
        if (text.length <= maxLength) {
            return text;
        }
        return `${text.slice(0, maxLength - 1).trim()}…`;
    }

    function valueAt(context) {
        return Number(context.parsed.x ?? context.parsed.y ?? context.raw ?? 0);
    }

    function makeGradient(chart, colors) {
        const area = chart.chartArea;
        const fallback = colors[0];

        if (!area) {
            return fallback;
        }

        const gradient = chart.ctx.createLinearGradient(area.left, 0, area.right, 0);
        colors.forEach((color, index) => {
            gradient.addColorStop(index / Math.max(colors.length - 1, 1), color);
        });
        return gradient;
    }

    function tooltipOptions(theme, titleCallback) {
        return {
            enabled: true,
            backgroundColor: theme.panel,
            borderColor: 'rgba(125, 211, 252, .34)',
            borderWidth: 1,
            cornerRadius: 14,
            padding: 13,
            boxPadding: 7,
            caretPadding: 9,
            titleColor: '#ffffff',
            bodyColor: theme.text,
            displayColors: true,
            titleFont: { size: 13, weight: '800' },
            bodyFont: { size: 13, weight: '700' },
            callbacks: {
                title(items) {
                    if (typeof titleCallback === 'function') {
                        return titleCallback(items);
                    }
                    return items[0]?.label || '';
                },
                label(context) {
                    const label = context.dataset.label || context.label || 'Количество';
                    return ` ${label}: ${valueAt(context)}`;
                }
            }
        };
    }

    const centerPercentPlugin = {
        id: 'centerPercent',
        afterDraw(chart, args, options) {
            if (!options || !options.enabled) {
                return;
            }

            const theme = chartTheme();
            const { ctx, chartArea } = chart;
            const x = (chartArea.left + chartArea.right) / 2;
            const y = (chartArea.top + chartArea.bottom) / 2;

            ctx.save();
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.shadowColor = 'rgba(56, 217, 255, .38)';
            ctx.shadowBlur = 22;
            ctx.fillStyle = theme.text;
            ctx.font = '900 32px Inter, Segoe UI, Arial, sans-serif';
            ctx.fillText(`${options.value || 0}%`, x, y - 5);
            ctx.shadowBlur = 0;
            ctx.fillStyle = theme.muted;
            ctx.font = '800 11px Inter, Segoe UI, Arial, sans-serif';
            ctx.fillText('успешных выдач', x, y + 25);
            ctx.restore();
        }
    };

    const barValuePlugin = {
        id: 'barValue',
        afterDatasetsDraw(chart, args, options) {
            if (!options || !options.enabled) {
                return;
            }

            const theme = chartTheme();
            const { ctx, chartArea } = chart;

            ctx.save();
            ctx.fillStyle = theme.text;
            ctx.font = '800 11px Inter, Segoe UI, Arial, sans-serif';
            ctx.textBaseline = 'middle';

            chart.data.datasets.forEach((dataset, datasetIndex) => {
                const meta = chart.getDatasetMeta(datasetIndex);
                meta.data.forEach((bar, index) => {
                    const value = Number(dataset.data[index] || 0);
                    if (!value) {
                        return;
                    }

                    const props = bar.getProps(['x', 'y'], true);
                    const x = Math.min(props.x + 8, chartArea.right - 4);
                    ctx.textAlign = x > chartArea.right - 24 ? 'right' : 'left';
                    ctx.fillText(String(value), x, props.y);
                });
            });

            ctx.restore();
        }
    };

    Chart.register(centerPercentPlugin, barValuePlugin);
    Chart.defaults.font.family = 'Inter, Segoe UI, system-ui, -apple-system, BlinkMacSystemFont, Arial, sans-serif';
    Chart.defaults.color = chartTheme().text;

    function commonBarOptions(theme, labels, labelMax) {
        return {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            animation: {
                duration: 950,
                easing: 'easeOutQuart'
            },
            interaction: {
                mode: 'nearest',
                axis: 'y',
                intersect: false
            },
            layout: {
                padding: { right: 28, left: 2, top: 4, bottom: 2 }
            },
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: {
                        color: theme.text,
                        usePointStyle: true,
                        pointStyle: 'rectRounded',
                        boxWidth: 9,
                        boxHeight: 9,
                        padding: 18,
                        font: { size: 12, weight: '800' }
                    }
                },
                tooltip: tooltipOptions(theme, (items) => labels[items[0]?.dataIndex] || ''),
                barValue: { enabled: true }
            },
            scales: {
                x: {
                    beginAtZero: true,
                    border: { display: false },
                    grid: { color: theme.grid, drawTicks: false },
                    ticks: {
                        color: theme.muted,
                        precision: 0,
                        padding: 8,
                        font: { size: 11, weight: '800' }
                    }
                },
                y: {
                    border: { display: false },
                    grid: { display: false },
                    ticks: {
                        color: theme.text,
                        padding: 10,
                        font: { size: 11, weight: '800' },
                        callback(value) {
                            return shortLabel(labels[value] || this.getLabelForValue(value), labelMax);
                        }
                    }
                }
            }
        };
    }

    function statusColors(theme, keys) {
        const palette = {
            pending: theme.blue,
            confirmed: theme.violet,
            issued: theme.green,
            not_issued: theme.gold,
            cancelled: theme.mutedSolid
        };
        return keys.map((key) => palette[key] || theme.blue);
    }

    function createStatusChart(theme) {
        const canvas = document.getElementById('statusChart');
        const statusData = chartData.statuses || {};

        if (!canvas || !hasValues(statusData.values)) {
            return;
        }

        const chart = new Chart(canvas, {
            type: 'doughnut',
            data: {
                labels: statusData.labels || [],
                datasets: [{
                    data: statusData.values || [],
                    backgroundColor: statusColors(theme, statusData.keys || []),
                    borderColor: theme.border,
                    borderWidth: 3,
                    hoverBorderColor: '#ffffff',
                    hoverBorderWidth: 3,
                    hoverOffset: 12,
                    spacing: 5
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '72%',
                radius: '92%',
                animation: {
                    animateRotate: true,
                    animateScale: true,
                    duration: 1050,
                    easing: 'easeOutQuart'
                },
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: {
                            color: theme.text,
                            usePointStyle: true,
                            pointStyle: 'circle',
                            boxWidth: 8,
                            boxHeight: 8,
                            padding: 16,
                            font: { size: 12, weight: '800' }
                        }
                    },
                    tooltip: tooltipOptions(theme, null),
                    centerPercent: {
                        enabled: true,
                        value: statusData.success_percent || 0
                    }
                }
            }
        });

        activeCharts.push(chart);
    }

    function createProductChart(theme) {
        const canvas = document.getElementById('productChart');
        const products = chartData.products || {};
        const labels = products.labels || [];

        if (!canvas || (!hasValues(products.issued) && !hasValues(products.not_issued))) {
            return;
        }

        const chart = new Chart(canvas, {
            type: 'bar',
            data: {
                labels,
                datasets: [
                    {
                        label: 'Выдано',
                        data: products.issued || [],
                        backgroundColor(context) {
                            return makeGradient(context.chart, [theme.green, theme.blue]);
                        },
                        borderColor: 'rgba(255, 255, 255, .20)',
                        borderWidth: 1,
                        borderRadius: 12,
                        borderSkipped: false,
                        maxBarThickness: 18
                    },
                    {
                        label: 'Не выдано',
                        data: products.not_issued || [],
                        backgroundColor(context) {
                            return makeGradient(context.chart, [theme.gold, theme.danger]);
                        },
                        borderColor: 'rgba(255, 255, 255, .18)',
                        borderWidth: 1,
                        borderRadius: 12,
                        borderSkipped: false,
                        maxBarThickness: 18
                    }
                ]
            },
            options: commonBarOptions(theme, labels, 24)
        });

        activeCharts.push(chart);
    }

    function createReasonsChart(theme) {
        const canvas = document.getElementById('reasonsChart');
        const reasons = chartData.reasons || {};
        const labels = reasons.labels || [];

        if (!canvas || !hasValues(reasons.values)) {
            return;
        }

        const chart = new Chart(canvas, {
            type: 'bar',
            data: {
                labels,
                datasets: [{
                    label: 'Количество',
                    data: reasons.values || [],
                    backgroundColor(context) {
                        return makeGradient(context.chart, [theme.violet, theme.gold, theme.danger]);
                    },
                    borderColor: 'rgba(255, 255, 255, .20)',
                    borderWidth: 1,
                    borderRadius: 13,
                    borderSkipped: false,
                    maxBarThickness: 22
                }]
            },
            options: commonBarOptions(theme, labels, 30)
        });

        activeCharts.push(chart);
    }

    function destroyCharts() {
        while (activeCharts.length) {
            activeCharts.pop().destroy();
        }
    }

    function createCharts() {
        const theme = chartTheme();
        Chart.defaults.color = theme.text;
        destroyCharts();
        createStatusChart(theme);
        createProductChart(theme);
        createReasonsChart(theme);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', createCharts);
    } else {
        createCharts();
    }

    window.addEventListener('site-theme-change', createCharts);
})();
