const canvas = document.getElementById('map-canvas');
const ctx = canvas.getContext('2d');
const state = { dataset: null, metadata: null, profile: null, frames: [], current: 0, playing: false, image: null, timer: null, simulation: false };
const els = {
  status: document.getElementById('dataset-status'), day: document.getElementById('dataset-day'), dataset: document.getElementById('dataset'), mode: document.getElementById('mode'), start: document.getElementById('start-time'), duration: document.getElementById('duration'), sample: document.getElementById('sample'), agents: document.getElementById('agents'), scenario: document.getElementById('scenario'), load: document.getElementById('load'), play: document.getElementById('play'), speed: document.getElementById('replay-speed'), speedOutput: document.getElementById('speed-output'), currentTime: document.getElementById('current-time'), frameStatus: document.getElementById('frame-status'), people: document.getElementById('people-count'), meanSpeed: document.getElementById('mean-speed'), peakDensity: document.getElementById('peak-density'), risk: document.getElementById('risk-level'), riskDetail: document.getElementById('risk-detail'), profile: document.getElementById('profile-summary'), zones: document.getElementById('zone-list'), routes: document.getElementById('route-list')
};

// ATC was recorded in Osaka, so controls and labels use Japan Standard Time.
const datasetTimeZone = 'Asia/Tokyo';
const fmtTime = stamp => new Intl.DateTimeFormat('en-GB', { timeZone: datasetTimeZone, hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false }).format(new Date(stamp * 1000));
const fmtStart = stamp => new Intl.DateTimeFormat('en-GB', { timeZone: datasetTimeZone, hour:'2-digit', minute:'2-digit', hour12:false }).format(new Date(stamp * 1000));

function worldToPixel(x, y) {
  const m = state.metadata.map;
  return [(x - m.origin[0]) / m.resolution, m.height - (y - m.origin[1]) / m.resolution];
}
function drawArrow(x, y, vx, vy) {
  const length = Math.hypot(vx, vy); if (length < 0.001) return;
  const scale = Math.min(20, 7 + length * 9), ux = vx / length, uy = -vy / length;
  ctx.beginPath(); ctx.moveTo(x - ux * scale * .35, y - uy * scale * .35); ctx.lineTo(x + ux * scale, y + uy * scale); ctx.lineTo(x + ux * scale - uy * 4, y + uy * scale + ux * 4); ctx.moveTo(x + ux * scale, y + uy * scale); ctx.lineTo(x + ux * scale + uy * 4, y + uy * scale - ux * 4); ctx.stroke();
}
function drawFrame() {
  if (!state.metadata || !state.image || !state.frames.length) return;
  const m = state.metadata.map, frame = state.frames[state.current];
  canvas.width = m.width; canvas.height = m.height;
  ctx.drawImage(state.image, 0, 0, m.width, m.height);
  if (state.profile) {
    ctx.font = 'bold 28px system-ui'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    for (const zone of state.profile.provisional_zones) { const [px, py] = worldToPixel(zone.x, zone.y); ctx.strokeStyle = '#cf5136'; ctx.fillStyle = 'rgba(255,255,255,.86)'; ctx.lineWidth = 5; ctx.beginPath(); ctx.arc(px, py, 34, 0, Math.PI * 2); ctx.fill(); ctx.stroke(); ctx.fillStyle = '#0d3555'; ctx.fillText(zone.id, px, py + 1); }
  }
  const cellMetres = 2, cell = cellMetres / m.resolution, cells = new Map();
  for (const person of frame.people) {
    const [, x, y, speed, angle] = person; const [px, py] = worldToPixel(x, y);
    const cx = Math.floor(px / cell), cy = Math.floor(py / cell), key = `${cx}:${cy}`;
    if (!cells.has(key)) cells.set(key, { cx, cy, n:0, speed:0, vx:0, vy:0 });
    const group = cells.get(key); group.n++; group.speed += speed; group.vx += Math.cos(angle) * speed; group.vy += Math.sin(angle) * speed;
  }
  let peak = 0, slowDense = 0, opposing = 0;
  for (const group of cells.values()) {
    const density = group.n / (cellMetres * cellMetres); peak = Math.max(peak, density);
    const meanSpeed = group.speed / group.n, resultant = Math.hypot(group.vx, group.vy) / Math.max(group.speed, .001);
    if (density >= 1 && meanSpeed < .65) slowDense++; if (group.n >= 4 && resultant < .55) opposing++;
    if (group.n >= 2) { const alpha = Math.min(.46, .07 + density * .13); ctx.fillStyle = density >= 1.5 ? `rgba(207,81,54,${alpha})` : `rgba(232,164,32,${alpha})`; ctx.fillRect(group.cx * cell, group.cy * cell, cell, cell); }
  }
  ctx.strokeStyle = 'rgba(13,53,85,.72)'; ctx.lineWidth = 3;
  for (const group of cells.values()) if (group.n >= 3) drawArrow((group.cx + .5) * cell, (group.cy + .5) * cell, group.vx / group.n, group.vy / group.n);
  ctx.fillStyle = '#1670ad';
  for (const [, x, y] of frame.people) { const [px, py] = worldToPixel(x, y); ctx.beginPath(); ctx.arc(px, py, 4, 0, Math.PI * 2); ctx.fill(); }
  const avg = frame.people.reduce((sum, item) => sum + item[3], 0) / Math.max(frame.people.length, 1);
  els.currentTime.textContent = state.simulation ? `T + ${frame.t.toFixed(0)} s` : fmtTime(frame.t); els.frameStatus.textContent = `Frame ${state.current + 1} of ${state.frames.length}`; els.people.textContent = frame.people.length.toLocaleString(); els.meanSpeed.textContent = `${avg.toFixed(2)} m/s`; els.peakDensity.textContent = `${peak.toFixed(2)} people/m²`;
  let label = 'Normal observation', detail = 'No strong local congestion proxy.', css = 'risk-clear';
  if (peak >= 2 || slowDense + opposing >= 3) { label = 'Elevated watch'; detail = `${slowDense} slow dense cell(s), ${opposing} mixed-flow cell(s).`; css = 'risk-high'; }
  else if (peak >= 1 || slowDense || opposing) { label = 'Monitor'; detail = `${slowDense} slow dense cell(s), ${opposing} mixed-flow cell(s).`; css = 'risk-watch'; }
  els.risk.textContent = label; els.risk.className = css; els.riskDetail.textContent = detail;
}
function renderProfile(profile) {
  state.profile = profile;
  els.profile.innerHTML = `<div><b>${profile.speed_m_s.mean.toFixed(2)} m/s</b><em>mean walking speed</em></div><div><b>${profile.valid_trajectories.toLocaleString()}</b><em>cleaned trajectories</em></div><div><b>${profile.provisional_zones.length}</b><em>provisional zones</em></div><div><b>${profile.top_routes[0]?.route || '—'}</b><em>strongest route</em></div>`;
  els.zones.replaceChildren(...profile.provisional_zones.map(zone => { const item = document.createElement('li'); item.innerHTML = `<span><b>${zone.id}</b> · (${zone.x.toFixed(1)}, ${zone.y.toFixed(1)}) m</span><small>${zone.support.toLocaleString()} endpoints</small>`; return item; }));
  els.routes.replaceChildren(...profile.top_routes.slice(0, 6).map(route => { const item = document.createElement('li'); item.innerHTML = `<span>${route.route}</span><small>${route.trajectories.toLocaleString()} trajectories</small>`; return item; }));
}
function stop() { state.playing = false; clearInterval(state.timer); state.timer = null; els.play.textContent = 'Play'; }
function start() { if (!state.frames.length) return; state.playing = true; els.play.textContent = 'Pause'; const tick = () => { state.current = (state.current + 1) % state.frames.length; drawFrame(); }; state.timer = setInterval(tick, Math.max(70, 800 / Number(els.speed.value))); }
async function loadWindow() {
  stop(); state.simulation = false; els.load.disabled = true; els.status.textContent = 'Reading selected interval…';
  try {
    const [hours, minutes] = els.start.value.split(':').map(Number); const base = new Date(state.metadata.first_time * 1000); const baseParts = new Intl.DateTimeFormat('en-CA', { timeZone:datasetTimeZone, year:'numeric', month:'2-digit', day:'2-digit' }).formatToParts(base); const date = Object.fromEntries(baseParts.map(p => [p.type, p.value]));
    const requested = Date.UTC(Number(date.year), Number(date.month) - 1, Number(date.day), hours - 9, minutes, 0) / 1000;
    const params = new URLSearchParams({ dataset: state.dataset, start: requested, duration: els.duration.value, sample: els.sample.value }); const response = await fetch(`/api/window?${params}`); const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || 'Could not read this interval.');
    state.frames = payload.frames; state.current = 0; els.play.disabled = !state.frames.length; els.status.textContent = `${payload.raw_points_read.toLocaleString()} source observations → ${payload.frames.length} replay frames`; drawFrame();
  } finally {
    els.load.disabled = false;
  }
}
async function loadSimulation() {
  stop(); state.simulation = true; els.load.disabled = true; els.status.textContent = 'Running Social Force simulation…';
  try {
    const params = new URLSearchParams({ dataset: state.dataset, agents: els.agents.value, duration: 120, scenario: els.scenario.value });
    const response = await fetch(`/api/simulate?${params}`); const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || 'Could not run the simulation.');
    state.frames = payload.frames; state.current = 0; els.play.disabled = !state.frames.length;
    els.status.textContent = `Social Force ABM • ${payload.simulation.agents} agents • ${payload.simulation.scenario.replace('_', ' ')}`;
    drawFrame();
  } finally { els.load.disabled = false; }
}
function resetProfile(message) {
  state.profile = null;
  els.profile.textContent = message;
  els.zones.replaceChildren();
  els.routes.replaceChildren();
}
async function activateDataset(dataset) {
  stop(); state.dataset = dataset; els.dataset.disabled = true; els.load.disabled = true; els.play.disabled = true; els.status.textContent = 'Preparing selected day…';
  try {
    const encoded = encodeURIComponent(dataset);
    const [metaResponse, profileResponse] = await Promise.all([fetch(`/api/metadata?dataset=${encoded}`), fetch(`/api/profile?dataset=${encoded}`)]);
    state.metadata = await metaResponse.json();
    if (!metaResponse.ok) throw new Error(state.metadata.error || 'Could not read this dataset.');
    if (profileResponse.ok) renderProfile(await profileResponse.json()); else resetProfile('No day-specific calibration profile yet. The multi-day build will add it when this day is processed.');
    els.start.value = fmtStart(state.metadata.first_time);
    els.day.textContent = `ATC • ${state.metadata.source.replace('atc-', '').replace('.csv', '')}`;
    els.status.textContent = `${state.metadata.row_count.toLocaleString()} observations indexed • map ready`;
    await loadWindow();
  } finally {
    els.dataset.disabled = false;
    els.load.disabled = false;
  }
}
function setMode() {
  const simulation = els.mode.value === 'simulation';
  document.body.classList.toggle('simulation-mode', simulation);
  els.start.disabled = simulation; els.duration.disabled = simulation; els.sample.disabled = simulation;
  els.load.textContent = simulation ? 'Run simulation' : 'Load interval';
}
async function init() {
  try {
    const [datasetsResponse, image] = await Promise.all([fetch('/api/datasets'), new Promise((resolve, reject) => { const i = new Image(); i.onload = () => resolve(i); i.onerror = reject; i.src = '/api/map'; })]);
    const data = await datasetsResponse.json(); if (!datasetsResponse.ok) throw new Error(data.error || 'Could not list datasets.');
    for (const dataset of data.datasets) { const option = document.createElement('option'); option.value = dataset.id; option.textContent = dataset.label; els.dataset.append(option); }
    state.image = image; els.dataset.value = data.default; await activateDataset(data.default);
  } catch (error) { els.status.textContent = `Setup error: ${error.message}`; }
}
els.load.addEventListener('click', () => (els.mode.value === 'simulation' ? loadSimulation() : loadWindow()).catch(error => { els.status.textContent = error.message; }));
els.dataset.addEventListener('change', () => activateDataset(els.dataset.value).catch(error => { els.status.textContent = error.message; els.dataset.disabled = false; els.load.disabled = false; }));
els.mode.addEventListener('change', setMode);
els.play.addEventListener('click', () => state.playing ? stop() : start());
els.speed.addEventListener('input', () => { els.speedOutput.textContent = `${els.speed.value}×`; if (state.playing) { stop(); start(); } });
window.addEventListener('beforeunload', stop); setMode(); init();
