"""Dashboard HTML template — read-only polling dashboard."""

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Cognitive OS — Runtime Dashboard</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: system-ui, sans-serif; background: #0d1117; color: #c9d1d9; padding: 24px; }
  h1 { font-size: 20px; margin-bottom: 4px; }
  .subtitle { color: #8b949e; font-size: 12px; margin-bottom: 24px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
  .card h2 { font-size: 14px; color: #58a6ff; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
  .stat { display: flex; justify-content: space-between; padding: 4px 0; font-size: 13px; }
  .stat .label { color: #8b949e; }
  .stat .value { font-family: monospace; }
  .score { font-size: 28px; font-weight: bold; font-family: monospace; }
  .score-bar { height: 6px; background: #21262d; border-radius: 3px; margin-top: 6px; overflow: hidden; }
  .score-fill { height: 100%; border-radius: 3px; transition: width 0.5s; }
  .score-fill.low { background: #3fb950; }
  .score-fill.mid { background: #d29922; }
  .score-fill.high { background: #f85149; }
  .event-row { font-size: 12px; font-family: monospace; padding: 3px 0; border-bottom: 1px solid #21262d; }
  .event-row .ts { color: #8b949e; }
  .event-row .type { color: #58a6ff; }
  .event-row .agg { color: #7ee787; }
  .trace-row { font-family: monospace; font-size: 12px; padding: 2px 0; }
  .trace-row .arrow { color: #8b949e; }
  .section { margin-top: 24px; }
  .error { color: #f85149; }
  button { background: #21262d; color: #c9d1d9; border: 1px solid #30363d; border-radius: 4px; padding: 4px 12px; cursor: pointer; font-size: 12px; }
  button:hover { background: #30363d; }
</style>
</head>
<body>
<h1>Cognitive OS — Runtime Dashboard</h1>
<div class="subtitle">read-only | polling every 3s | UTC</div>

<div class="grid">
  <div class="card">
    <h2>Stats</h2>
    <div class="stat"><span class="label">Total Events</span><span class="value" id="total-events">-</span></div>
    <div class="stat"><span class="label">Last Sequence</span><span class="value" id="last-seq">-</span></div>
    <div class="stat"><span class="label">Applied Events</span><span class="value" id="applied-events">-</span></div>
    <div class="stat"><span class="label">State Hash</span><span class="value" id="state-hash" style="font-size:10px">-</span></div>
    <div class="stat"><span class="label">Server Time</span><span class="value" id="server-time">-</span></div>
  </div>

  <div class="card">
    <h2>Derived State</h2>
    <div class="stat"><span class="label">Workload</span></div>
    <div class="score" id="workload-score">-</div>
    <div class="score-bar"><div class="score-fill" id="workload-bar" style="width:0%"></div></div>
    <div class="stat" style="margin-top:8px"><span class="label">Deadline Pressure</span></div>
    <div class="score" id="deadline-score">-</div>
    <div class="score-bar"><div class="score-fill" id="deadline-bar" style="width:0%"></div></div>
    <div class="stat" style="margin-top:8px"><span class="label">Activity Density</span></div>
    <div class="score" id="activity-score">-</div>
    <div class="score-bar"><div class="score-fill" id="activity-bar" style="width:0%"></div></div>
  </div>

  <div class="card">
    <h2>Snapshot</h2>
    <div id="snapshot-info">loading...</div>
  </div>
</div>

<div class="section">
  <div class="card">
    <h2>Event Timeline (recent 30)</h2>
    <div id="event-timeline">loading...</div>
  </div>
</div>

<script>
function scoreClass(v) { return v > 0.7 ? 'high' : v > 0.3 ? 'mid' : 'low'; }

async function poll() {
  try {
    let [stats, state, snaps, events] = await Promise.all([
      fetch('/stats').then(r => r.json()),
      fetch('/state').then(r => r.json()),
      fetch('/snapshots').then(r => r.json()),
      fetch('/events/recent?n=30').then(r => r.json()),
    ]);

    document.getElementById('total-events').textContent = stats.total_events;
    document.getElementById('last-seq').textContent = stats.last_sequence;
    document.getElementById('applied-events').textContent = stats.applied_events;
    document.getElementById('state-hash').textContent = state.state_hash ? state.state_hash.substring(0,16)+'...' : '-';
    document.getElementById('server-time').textContent = stats.server_time_utc;

    let d = state.derived || {};
    updateScore('workload', d.workload);
    updateScore('deadline', d.deadline_pressure);
    updateScore('activity', d.activity_density);

    document.getElementById('snapshot-info').innerHTML =
      '<div class="stat"><span class="label">Snapshots</span><span class="value">'+snaps.snapshots.length+'</span></div>' +
      '<div class="stat"><span class="label">Latest Seq</span><span class="value">'+(snaps.latest_sequence || 'none')+'</span></div>';

    let html = '';
    for (let e of events.events) {
      html += '<div class="event-row"><span class="ts">'+e.timestamp.substring(11,19)+'</span> ' +
        '<span class="type">'+e.event_type+'</span> ' +
        '<span class="agg">('+e.aggregate_id+')</span></div>';
    }
    document.getElementById('event-timeline').innerHTML = html || '<div class="event-row">no events</div>';
  } catch(e) {
    console.error(e);
  }
}

function updateScore(name, data) {
  if (!data) return;
  let s = data.score || 0;
  document.getElementById(name+'-score').textContent = s.toFixed(2);
  let bar = document.getElementById(name+'-bar');
  bar.style.width = (s*100)+'%';
  bar.className = 'score-fill ' + scoreClass(s);
}

poll();
setInterval(poll, 3000);
</script>
</body>
</html>"""
