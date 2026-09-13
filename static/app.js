document.addEventListener('click', function(event) {
  const button = event.target.closest('[data-confirm]');
  if (button && !window.confirm(button.dataset.confirm)) event.preventDefault();
});
document.addEventListener('htmx:responseError', function() {
  window.alert('Could not update the results. Refresh the page and try again.');
});
document.addEventListener('DOMContentLoaded', function() {
  const data = document.getElementById('price-data');
  const canvas = document.getElementById('price-chart');
  if (!data || !canvas || !window.Chart) return;
  const series = JSON.parse(data.textContent);
  const dates = [...new Set(series.flatMap(s => s.points.map(p => p.date)))].sort();
  const colours = ['#215be5', '#159077', '#c67715', '#a94c8d', '#52657c', '#c15440'];
  new Chart(canvas, {
    type: 'line',
    data: {
      labels: dates,
      datasets: series.map((s, i) => ({
        label: s.label,
        data: dates.map(d => s.points.find(p => p.date === d)?.price ?? null),
        borderColor: colours[i % colours.length], backgroundColor: colours[i % colours.length],
        borderWidth: 2, pointRadius: dates.length < 30 ? 3 : 1,
        tension: 0, spanGaps: false
      }))
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: {mode: 'index', intersect: false},
      plugins: {legend: {position: 'bottom', labels: {boxWidth: 12}}, tooltip: {callbacks: {label: c => `${c.dataset.label}: €${Number(c.raw).toLocaleString()}`}}},
      scales: {x: {grid: {display: false}, ticks: {maxTicksLimit: 8}}, y: {ticks: {callback: v => '€' + Number(v).toLocaleString()}, grid: {color: '#edf1f6'}}}
    }
  });
});
