/* =====================================================================
   dashboard.js  —  Geospatial Tracking System
   Layout: full-width map on top, resizable bottom data table
   Drill-down: LGA → Ward → Settlement (GPS points at settlement)
   ===================================================================== */

// ── State ──────────────────────────────────────────────────────────────────
const API = '';
let token        = localStorage.getItem('token');
let currentPid   = null;   // project id
let navLevel     = 'lga';  // 'lga' | 'ward' | 'settlement'
let navData      = { lga: [], ward: [], settlement: [] };
let currentLGA   = null;   // { lgacode, lga_name }
let currentWard  = null;   // { wardcode, ward_name }
let currentSett  = null;   // { unique_cod, settlement_name }
let projectSummary = {};
let pieChart     = null;
let toolbarOpen  = false;
let layerPanelOpen = false;
let layerVis     = { lga: true, ward: true, settlement: true, points: true };
let sortCol      = null;
let sortDir      = 1;

// ── Auth guard ─────────────────────────────────────────────────────────────
if (!token) window.location.href = '/';
document.getElementById('topbar-username').textContent = localStorage.getItem('username') || 'User';
if (localStorage.getItem('is_admin') === 'true') {
  document.getElementById('admin-link').style.display = 'inline-flex';
}
function handleLogout() { localStorage.clear(); window.location.href = '/'; }

// ── API helper ─────────────────────────────────────────────────────────────
async function apiFetch(path, opts = {}) {
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(`${API}${path}`, { ...opts, headers });
  if (res.status === 401) { handleLogout(); return null; }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    showToast(err.detail || 'Request failed', 'error');
    return null;
  }
  return res.json();
}

// ── Toast ──────────────────────────────────────────────────────────────────
function showToast(msg, type = 'info') {
  const tc = document.getElementById('toast-container');
  const el = document.createElement('div');
  el.className = `toast ${type === 'error' ? 'error' : type === 'warn' ? 'warn' : ''}`;
  el.textContent = msg;
  tc.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

// ── MapLibre ───────────────────────────────────────────────────────────────
const BASEMAPS = {
  osm:       'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  satellite: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  terrain:   'https://tile.opentopomap.org/{z}/{x}/{y}.png',
};

const map = new maplibregl.Map({
  container: 'map',
  style: {
    version: 8,
    sources: {
      'osm-tiles': { type: 'raster', tiles: [BASEMAPS.osm], tileSize: 256, attribution: '© OpenStreetMap' },
    },
    layers: [{ id: 'osm', type: 'raster', source: 'osm-tiles' }],
    glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
  },
  center: [5.25, 13.05],
  zoom: 7,
});

map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left');

map.on('zoom', () => {
  document.getElementById('zoom-level').textContent = map.getZoom().toFixed(1);
});

map.on('load', () => {
  document.getElementById('zoom-level').textContent = map.getZoom().toFixed(1);
  initMapSources();
  loadProjects();
});

// ── Basemap ────────────────────────────────────────────────────────────────
function setBasemap(key, el) {
  document.querySelectorAll('.st-btn[id^="bm-"]').forEach(b => b.classList.remove('active'));
  if (el) el.classList.add('active');
  map.getSource('osm-tiles').setTiles([BASEMAPS[key]]);
}
document.getElementById('bm-street').classList.add('active');

// ── Map sources + layers ───────────────────────────────────────────────────
const EMPTY_FC = { type: 'FeatureCollection', features: [] };

function initMapSources() {
  // ── LGA (visible at low zoom 0-10) ──────────────────────────────
  map.addSource('lga-src', { type: 'geojson', data: EMPTY_FC });
  map.addLayer({
    id: 'lga-fill', type: 'fill', source: 'lga-src', maxzoom: 10,
    paint: {
      'fill-color': ['interpolate', ['linear'], ['coalesce', ['get', 'visitation_pct'], 0],
        0, '#1e3a5f', 30, '#1e40af', 60, '#2563eb', 80, '#3b82f6', 100, '#93c5fd'],
      'fill-opacity': 0.35,
    },
  });
  map.addLayer({
    id: 'lga-line', type: 'line', source: 'lga-src', maxzoom: 10,
    paint: { 'line-color': '#3b82f6', 'line-width': 1.8 },
  });
  map.addLayer({
    id: 'lga-label', type: 'symbol', source: 'lga-src', minzoom: 5, maxzoom: 9,
    layout: {
      'text-field': ['concat', ['get', 'lga_name'], '\n', ['to-string', ['coalesce', ['get', 'visitation_pct'], 0]], '%'],
      'text-size': 11, 'text-font': ['Open Sans Regular', 'Arial Unicode MS Regular'],
    },
    paint: { 'text-color': '#fff', 'text-halo-color': '#0f172a', 'text-halo-width': 1.5 },
  });

  // ── Ward (visible at mid zoom 7-13) ─────────────────────────────
  map.addSource('ward-src', { type: 'geojson', data: EMPTY_FC });
  map.addLayer({
    id: 'ward-fill', type: 'fill', source: 'ward-src', minzoom: 8, maxzoom: 13,
    paint: {
      'fill-color': ['interpolate', ['linear'], ['coalesce', ['get', 'visitation_pct'], 0],
        0, '#3b0764', 30, '#5b21b6', 60, '#7c3aed', 80, '#8b5cf6', 100, '#a78bfa'],
      'fill-opacity': 0.3,
    },
  });
  map.addLayer({
    id: 'ward-line', type: 'line', source: 'ward-src', minzoom: 8, maxzoom: 13,
    paint: { 'line-color': '#8b5cf6', 'line-width': 1.2 },
  });
  map.addLayer({
    id: 'ward-label', type: 'symbol', source: 'ward-src', minzoom: 9, maxzoom: 12,
    layout: {
      'text-field': ['get', 'ward_name'], 'text-size': 10,
      'text-font': ['Open Sans Regular', 'Arial Unicode MS Regular'],
    },
    paint: { 'text-color': '#d8b4fe', 'text-halo-color': '#0f172a', 'text-halo-width': 1.2 },
  });

  // ── Settlement (visible at high zoom 10+) ───────────────────────
  map.addSource('settlement-src', { type: 'geojson', data: EMPTY_FC });
  map.addLayer({
    id: 'settlement-fill', type: 'fill', source: 'settlement-src', minzoom: 10,
    paint: {
      'fill-color': ['case', ['get', 'is_visited'], '#16a34a', '#dc2626'],
      'fill-opacity': 0.45,
    },
  });
  map.addLayer({
    id: 'settlement-line', type: 'line', source: 'settlement-src', minzoom: 10,
    paint: {
      'line-color': ['case', ['get', 'is_visited'], '#22c55e', '#ef4444'],
      'line-width': 1.0,
    },
  });
  map.addLayer({
    id: 'settlement-label', type: 'symbol', source: 'settlement-src', minzoom: 12,
    layout: {
      'text-field': ['get', 'settlement_name'], 'text-size': 9,
      'text-font': ['Open Sans Regular', 'Arial Unicode MS Regular'],
    },
    paint: { 'text-color': '#fff', 'text-halo-color': '#0f172a', 'text-halo-width': 1 },
  });

  // ── GPS Points (visible at zoom 11+) ────────────────────────────
  map.addSource('points-src', { type: 'geojson', data: EMPTY_FC });
  map.addLayer({
    id: 'points-circle', type: 'circle', source: 'points-src', minzoom: 11,
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 11, 3, 14, 5],
      'circle-color': '#ef4444', 'circle-opacity': 0.8,
      'circle-stroke-color': '#fff', 'circle-stroke-width': 0.8,
    },
  });

  applyLayerVisibility();
  addMapClickHandlers();
}

// ── Layer visibility ───────────────────────────────────────────────────────
function toggleLayer(name) {
  layerVis[name] = !layerVis[name];
  const eye = document.getElementById(`eye-${name}`);
  if (eye) {
    eye.className = layerVis[name] ? 'bi bi-eye lp-eye' : 'bi bi-eye-slash lp-eye off';
  }
  applyLayerVisibility();
}

function applyLayerVisibility() {
  const show = (ids, on) => ids.forEach(id => {
    if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
  });
  show(['lga-fill', 'lga-line', 'lga-label'], layerVis.lga);
  show(['ward-fill', 'ward-line', 'ward-label'], layerVis.ward);
  show(['settlement-fill', 'settlement-line', 'settlement-label'], layerVis.settlement);
  show(['points-circle'], layerVis.points);
}

// ── Click handlers on map features ────────────────────────────────────────
const popup = new maplibregl.Popup({ closeButton: true, maxWidth: '270px' });

function addMapClickHandlers() {
  map.on('click', 'lga-fill', (e) => {
    const p = e.features[0].properties;
    popup.setLngLat(e.lngLat).setHTML(`
      <div style="font-size:13px;color:#f1f5f9;background:#1e293b;padding:4px 0">
        <strong style="font-size:14px">${p.lga_name}</strong><br>
        <span style="color:#94a3b8">Visitation: <span style="color:#22c55e">${p.visitation_pct}%</span></span><br>
        <span style="color:#94a3b8">Settlements: ${p.visited_settlements}/${p.total_settlements}</span><br>
        <button onclick="onMapLGAClick('${p.lgacode}','${p.lga_name}')"
          style="margin-top:6px;background:#1d4ed8;color:#fff;border:none;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:11px">
          Drill into wards →</button>
      </div>`).addTo(map);
  });

  map.on('click', 'ward-fill', (e) => {
    const p = e.features[0].properties;
    popup.setLngLat(e.lngLat).setHTML(`
      <div style="font-size:13px;color:#f1f5f9;background:#1e293b;padding:4px 0">
        <strong>${p.ward_name}</strong><br>
        <span style="color:#94a3b8">${p.lga_name}</span><br>
        <span style="color:#a78bfa">Visitation: ${p.visitation_pct}%</span><br>
        <button onclick="onMapWardClick('${p.wardcode}','${p.ward_name}')"
          style="margin-top:6px;background:#6d28d9;color:#fff;border:none;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:11px">
          Drill into settlements →</button>
      </div>`).addTo(map);
  });

  map.on('click', 'settlement-fill', (e) => {
    const p = e.features[0].properties;
    popup.setLngLat(e.lngLat).setHTML(`
      <div style="font-size:13px;color:#f1f5f9;background:#1e293b;padding:4px 0">
        <strong>${p.settlement_name || 'Settlement'}</strong><br>
        <span style="color:#94a3b8">${p.ward_name} › ${p.lga_name}</span><br>
        <span style="${p.is_visited ? 'color:#22c55e' : 'color:#ef4444'}">
          ${p.is_visited ? '✓ Visited' : '✗ Not Visited'}</span><br>
        <span style="color:#fbbf24">Completeness: ${Number(p.completeness_pct || 0).toFixed(1)}%</span><br>
        <span style="color:#94a3b8">Points: ${p.point_count}</span><br>
        <button onclick="onMapSettClick('${p.unique_cod}','${(p.settlement_name||'').replace(/'/g,'')}')"
          style="margin-top:6px;background:#15803d;color:#fff;border:none;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:11px">
          Show GPS points →</button>
      </div>`).addTo(map);
  });

  map.on('click', 'points-circle', (e) => {
    const p = e.features[0].properties;
    popup.setLngLat(e.lngLat).setHTML(`
      <div style="font-size:12px;color:#f1f5f9;background:#1e293b;padding:4px 0">
        <strong>GPS Point</strong><br>
        <span style="color:#94a3b8">${p.settlement_name || ''}</span><br>
        <span>Lat ${Number(p.latitude).toFixed(5)}, Lon ${Number(p.longitude).toFixed(5)}</span>
        ${p.collection_date ? `<br><span style="color:#94a3b8">${p.collection_date}</span>` : ''}
      </div>`).addTo(map);
  });

  ['lga-fill','ward-fill','settlement-fill','points-circle'].forEach(l => {
    map.on('mouseenter', l, () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', l, () => { map.getCanvas().style.cursor = ''; });
  });
}

// Map click drill-down helpers
function onMapLGAClick(lgacode, lganame) {
  popup.remove();
  drillLGA({ lgacode, lga_name: lganame });
}
function onMapWardClick(wardcode, wardname) {
  popup.remove();
  const item = navData.ward.find(w => w.wardcode === wardcode) || { wardcode, ward_name: wardname };
  drillWard(item);
}
function onMapSettClick(unique_cod, sett_name) {
  popup.remove();
  const item = navData.settlement.find(s => s.unique_cod === unique_cod) || { unique_cod, settlement_name: sett_name };
  drillSettlement(item);
}

// ── Toolbar / Layer panel ──────────────────────────────────────────────────
function toggleToolbar() {
  toolbarOpen = !toolbarOpen;
  document.getElementById('side-toolbar').classList.toggle('open', toolbarOpen);
  document.getElementById('toolbar-toggle').classList.toggle('active', toolbarOpen);
  if (!toolbarOpen && layerPanelOpen) toggleLayerPanel();
}

function toggleLayerPanel() {
  layerPanelOpen = !layerPanelOpen;
  document.getElementById('layer-panel').classList.toggle('hidden', !layerPanelOpen);
  document.getElementById('st-layers').classList.toggle('active', layerPanelOpen);
}

function toggleModal(id) {
  document.getElementById(id).classList.toggle('hidden');
}

function toggleFullscreen() {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen();
    document.getElementById('btn-fs').querySelector('i').className = 'bi bi-fullscreen-exit';
  } else {
    document.exitFullscreen();
    document.getElementById('btn-fs').querySelector('i').className = 'bi bi-fullscreen';
  }
}

function refreshData() {
  if (!currentPid) return;
  selectProject(currentPid);
  showToast('Refreshing data…');
}

// ── Drag-to-resize ─────────────────────────────────────────────────────────
(function initDrag() {
  const handle = document.getElementById('drag-handle');
  const panel  = document.getElementById('bt-panel');
  let dragging = false, startY = 0, startH = 0;

  handle.addEventListener('mousedown', (e) => {
    dragging = true;
    startY   = e.clientY;
    startH   = panel.getBoundingClientRect().height;
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'ns-resize';
  });
  document.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const delta = startY - e.clientY;
    const newH  = Math.max(80, Math.min(window.innerHeight * 0.6, startH + delta));
    panel.style.height = newH + 'px';
  });
  document.addEventListener('mouseup', () => {
    dragging = false;
    document.body.style.userSelect = '';
    document.body.style.cursor = '';
  });

  // Touch support
  handle.addEventListener('touchstart', (e) => {
    dragging = true; startY = e.touches[0].clientY;
    startH = panel.getBoundingClientRect().height;
  }, { passive: true });
  document.addEventListener('touchmove', (e) => {
    if (!dragging) return;
    const delta = startY - e.touches[0].clientY;
    const newH  = Math.max(80, Math.min(window.innerHeight * 0.6, startH + delta));
    panel.style.height = newH + 'px';
  }, { passive: true });
  document.addEventListener('touchend', () => { dragging = false; });
})();

// ── Projects ───────────────────────────────────────────────────────────────
async function loadProjects() {
  const projects = await apiFetch('/api/projects');
  if (!projects) return;
  const sel = document.getElementById('project-switcher');
  sel.innerHTML = '<option value="">Select project…</option>';
  projects.forEach(p => {
    const o = document.createElement('option');
    o.value = p.id; o.textContent = p.name;
    if (p.is_active) o.selected = true;
    sel.appendChild(o);
  });
  sel.addEventListener('change', () => { if (sel.value) selectProject(parseInt(sel.value)); });
  const active = projects.find(p => p.is_active) || projects[0];
  if (active) { sel.value = active.id; selectProject(active.id); }
}

async function selectProject(pid) {
  currentPid = pid;
  resetDrillState();
  clearMapSources();
  await Promise.all([
    loadLGABoundaries(),
    loadWardBoundaries(null),
    loadLGAMetrics(),
    loadProjectSummary(),
    loadQCSummary(),
  ]);
}

function resetDrillState() {
  currentLGA = currentWard = currentSett = null;
  navLevel = 'lga';
  navData  = { lga: [], ward: [], settlement: [] };
  ['tab-ward','tab-sett'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.disabled = true;
  });
  updateBreadcrumb();
}

function resetToProject() {
  if (!currentPid) return;
  resetDrillState();
  clearSource('settlement-src');
  clearSource('points-src');
  renderTable(navData.lga, 'lga');
  map.flyTo({ center: [5.25, 13.05], zoom: 7, duration: 800 });
}

// ── Boundaries ─────────────────────────────────────────────────────────────
async function loadLGABoundaries() {
  const d = await apiFetch(`/api/projects/${currentPid}/boundaries/lga/geojson`);
  if (d) map.getSource('lga-src').setData(d);
}

async function loadWardBoundaries(lgacode) {
  const q = lgacode ? `?lgacode=${lgacode}` : '';
  const d = await apiFetch(`/api/projects/${currentPid}/boundaries/ward/geojson${q}`);
  if (d) map.getSource('ward-src').setData(d);
}

async function loadSettlementBoundaries(lgacode, wardcode) {
  let q = '';
  if (wardcode) q = `?wardcode=${wardcode}`;
  else if (lgacode) q = `?lgacode=${lgacode}`;
  const d = await apiFetch(`/api/projects/${currentPid}/boundaries/settlement/geojson${q}`);
  if (d) map.getSource('settlement-src').setData(d);
}

async function loadPoints(unique_cod) {
  const d = await apiFetch(`/api/projects/${currentPid}/analytics/points/geojson?unique_cod=${encodeURIComponent(unique_cod)}&limit=5000`);
  if (d) map.getSource('points-src').setData(d);
}

// ── Metrics (table data) ───────────────────────────────────────────────────
async function loadLGAMetrics() {
  const d = await apiFetch(`/api/projects/${currentPid}/analytics/lgas`);
  if (!d) return;
  navData.lga = d;
  navLevel = 'lga';
  renderTable(d, 'lga');
}

async function loadWardMetrics(lgacode) {
  const d = await apiFetch(`/api/projects/${currentPid}/analytics/wards?lgacode=${lgacode}`);
  if (!d) return;
  navData.ward = d;
  navLevel = 'ward';
  renderTable(d, 'ward');
  const tab = document.getElementById('tab-ward');
  if (tab) tab.disabled = false;
}

async function loadSettlementMetrics(wardcode) {
  const d = await apiFetch(`/api/projects/${currentPid}/analytics/settlements?wardcode=${wardcode}`);
  if (!d) return;
  navData.settlement = d;
  navLevel = 'settlement';
  renderTable(d, 'settlement');
  const tab = document.getElementById('tab-sett');
  if (tab) tab.disabled = false;
}

// ── Drill-down logic ───────────────────────────────────────────────────────
async function drillLGA(item) {
  currentLGA  = item;
  currentWard = currentSett = null;
  clearSource('settlement-src');
  clearSource('points-src');
  updateBreadcrumb();
  zoomToLGA(item.lgacode);
  await Promise.all([
    loadWardMetrics(item.lgacode),
    loadSettlementBoundaries(item.lgacode, null),
  ]);
}

async function drillWard(item) {
  currentWard = item;
  currentSett = null;
  clearSource('points-src');
  updateBreadcrumb();
  zoomToWard(item.wardcode);
  await Promise.all([
    loadSettlementMetrics(item.wardcode),
    loadSettlementBoundaries(null, item.wardcode),
  ]);
}

async function drillSettlement(item) {
  currentSett = item;
  updateBreadcrumb();
  zoomToSettlement(item.unique_cod);
  await loadPoints(item.unique_cod);
  // Zoom map to show points level
  if (map.getZoom() < 12) map.flyTo({ zoom: 13, duration: 600 });
}

// ── Tab switcher ───────────────────────────────────────────────────────────
function switchTab(level) {
  const data = navData[level];
  if (!data?.length) return;
  navLevel = level;
  renderTable(data, level);
  document.querySelectorAll('.bt-tab').forEach(t =>
    t.classList.toggle('active', t.dataset.level === level));
}

// ── Bottom table rendering ─────────────────────────────────────────────────
const COLS = {
  lga: [
    { key: 'lga_name',        label: 'LGA',           w: '140px' },
    { key: 'total_settlements',label: 'Settlements',   w: '90px', align: 'right' },
    { key: 'visited_settlements',label: 'Visited',     w: '70px', align: 'right' },
    { key: 'visitation_pct',  label: 'Visitation %',  w: '140px', bar: true },
    { key: 'total_grids',     label: 'Total Grids',   w: '90px', align: 'right' },
    { key: 'visited_grids',   label: 'Visited Grids', w: '90px', align: 'right' },
    { key: 'completeness_pct',label: 'Completeness %',w: '140px', bar: true },
    { key: 'point_count',     label: 'GPS Points',    w: '90px', align: 'right' },
  ],
  ward: [
    { key: 'ward_name',       label: 'Ward',          w: '150px' },
    { key: 'lga_name',        label: 'LGA',           w: '120px' },
    { key: 'total_settlements',label: 'Settlements',  w: '90px', align: 'right' },
    { key: 'visited_settlements',label: 'Visited',    w: '70px', align: 'right' },
    { key: 'visitation_pct',  label: 'Visitation %',  w: '140px', bar: true },
    { key: 'completeness_pct',label: 'Completeness %',w: '140px', bar: true },
    { key: 'point_count',     label: 'GPS Points',    w: '90px', align: 'right' },
  ],
  settlement: [
    { key: 'settlement_name', label: 'Settlement',    w: '180px' },
    { key: 'ward_name',       label: 'Ward',          w: '130px' },
    { key: 'lga_name',        label: 'LGA',           w: '110px' },
    { key: 'is_visited',      label: 'Status',        w: '80px' },
    { key: 'total_grids',     label: 'Grids',         w: '70px', align: 'right' },
    { key: 'completeness_pct',label: 'Completeness %',w: '140px', bar: true },
    { key: 'point_count',     label: 'GPS Points',    w: '90px', align: 'right' },
  ],
};

function renderTable(items, level) {
  const cols  = COLS[level];
  const thead = document.getElementById('bt-thead');

  // Update active tab
  document.querySelectorAll('.bt-tab').forEach(t =>
    t.classList.toggle('active', t.dataset.level === level));

  // Headers
  thead.innerHTML = '<tr>' + cols.map(c => `
    <th style="min-width:${c.w}" onclick="sortTable('${c.key}','${level}')">
      ${c.label}<span class="sort-icon">⇅</span>
    </th>`).join('') + '</tr>';

  renderRows(items, level);
  document.getElementById('bt-search').value = '';
  document.getElementById('bt-count').textContent = `${items.length} rows`;
  updateFooterSummary(items, level);
}

function renderRows(items, level) {
  const cols  = COLS[level];
  const tbody = document.getElementById('bt-tbody');

  if (!items.length) {
    tbody.innerHTML = `<tr><td colspan="${cols.length}" class="bt-empty">No data found</td></tr>`;
    return;
  }

  tbody.innerHTML = items.map((row, i) => {
    const cells = cols.map(c => {
      const val = row[c.key];
      if (c.bar) {
        const pct = Math.min(100, Math.max(0, Number(val) || 0));
        const color = pct >= 80 ? '#22c55e' : pct >= 50 ? '#f59e0b' : '#ef4444';
        return `<td>
          <div class="tbl-bar-wrap">
            <div class="tbl-bar"><div class="tbl-bar-fill" style="width:${pct}%;background:${color}"></div></div>
            <span style="font-size:11px;color:${color};font-weight:600;min-width:34px">${pct.toFixed(1)}%</span>
          </div>
        </td>`;
      }
      if (c.key === 'is_visited') {
        return `<td><span class="vis-badge ${val ? 'yes' : 'no'}">${val ? '✓ Visited' : '✗ Not visited'}</span></td>`;
      }
      const display = val != null ? (typeof val === 'number' ? val.toLocaleString() : val) : '—';
      const align   = c.align === 'right' ? 'text-align:right' : '';
      return `<td style="${align}">${display}</td>`;
    }).join('');

    return `<tr data-idx="${i}" onclick="onRowClick(${i},'${level}')" >${cells}</tr>`;
  }).join('');
}

function onRowClick(idx, level) {
  document.querySelectorAll('#bt-tbody tr').forEach(r => r.classList.remove('selected'));
  document.querySelector(`#bt-tbody tr[data-idx="${idx}"]`)?.classList.add('selected');
  const item = navData[level][idx];
  if (!item) return;
  if (level === 'lga')        drillLGA(item);
  else if (level === 'ward')  drillWard(item);
  else if (level === 'settlement') drillSettlement(item);
}

function filterTable() {
  const q = document.getElementById('bt-search').value.toLowerCase();
  document.querySelectorAll('#bt-tbody tr').forEach(tr => {
    tr.style.display = tr.textContent.toLowerCase().includes(q) ? '' : 'none';
  });
}

function sortTable(key, level) {
  const data = navData[level];
  if (sortCol === key) sortDir = -sortDir;
  else { sortCol = key; sortDir = 1; }
  data.sort((a, b) => {
    const av = a[key], bv = b[key];
    if (av == null) return 1; if (bv == null) return -1;
    return typeof av === 'string'
      ? av.localeCompare(bv) * sortDir
      : (av - bv) * sortDir;
  });
  renderRows(data, level);
}

function updateFooterSummary(items, level) {
  const el = document.getElementById('bt-footer');
  if (!items.length) { el.textContent = 'No records'; return; }
  if (level === 'lga') {
    const visited = items.filter(r => r.visitation_pct >= 50).length;
    const avgPct  = (items.reduce((s, r) => s + (r.visitation_pct || 0), 0) / items.length).toFixed(1);
    el.textContent = `${items.length} LGAs  •  ${visited} ≥50% visited  •  Avg visitation ${avgPct}%`;
  } else if (level === 'ward') {
    const avgPct = (items.reduce((s, r) => s + (r.visitation_pct || 0), 0) / items.length).toFixed(1);
    el.textContent = `${items.length} wards in ${currentLGA?.lga_name || ''}  •  Avg visitation ${avgPct}%`;
  } else {
    const visited = items.filter(r => r.is_visited).length;
    const pct     = ((visited / items.length) * 100).toFixed(1);
    el.textContent = `${items.length} settlements  •  ${visited} visited (${pct}%)  •  ${currentWard?.ward_name || ''}`;
  }
}

// ── Breadcrumb ─────────────────────────────────────────────────────────────
function updateBreadcrumb() {
  const el = document.getElementById('tb-breadcrumb');
  let html = `<span class="bc-seg" onclick="resetToProject()"><i class="bi bi-house-fill"></i> Sokoto</span>`;
  if (currentLGA) {
    html += `<span class="bc-arrow">›</span>
             <span class="bc-seg" onclick="switchTab('lga')">${currentLGA.lga_name}</span>`;
  }
  if (currentWard) {
    html += `<span class="bc-arrow">›</span>
             <span class="bc-seg" onclick="switchTab('ward')">${currentWard.ward_name}</span>`;
  }
  if (currentSett) {
    html += `<span class="bc-arrow">›</span>
             <span class="bc-seg current">${currentSett.settlement_name || currentSett.unique_cod}</span>`;
  }
  el.innerHTML = html;
}

// ── Zoom helpers ───────────────────────────────────────────────────────────
function boundsFromFeature(feat) {
  const bounds = new maplibregl.LngLatBounds();
  const geom   = feat?.geometry;
  if (!geom) return null;
  const push = coords => {
    if (Array.isArray(coords[0])) coords.forEach(push);
    else bounds.extend(coords);
  };
  if (geom.type === 'MultiPolygon') geom.coordinates.forEach(p => p.forEach(push));
  else if (geom.type === 'Polygon')  geom.coordinates.forEach(push);
  return bounds.isEmpty() ? null : bounds;
}

function zoomToLGA(lgacode) {
  const src  = map.getSource('lga-src')?._data;
  const feat = src?.features?.find(f => f.properties.lgacode === lgacode);
  const bounds = boundsFromFeature(feat);
  if (bounds) map.fitBounds(bounds, { padding: 60, maxZoom: 10 });
}

function zoomToWard(wardcode) {
  const src  = map.getSource('ward-src')?._data;
  const feat = src?.features?.find(f => f.properties.wardcode === wardcode);
  const bounds = boundsFromFeature(feat);
  if (bounds) map.fitBounds(bounds, { padding: 60, maxZoom: 12 });
  else {
    const all = map.getSource('ward-src')?._data;
    if (all?.features?.length) {
      const b = new maplibregl.LngLatBounds();
      all.features.forEach(f => boundsFromFeature(f) && b.extend(boundsFromFeature(f)));
      if (!b.isEmpty()) map.fitBounds(b, { padding: 60 });
    }
  }
}

function zoomToSettlement(unique_cod) {
  const src  = map.getSource('settlement-src')?._data;
  const feat = src?.features?.find(f => f.properties.unique_cod === unique_cod);
  const bounds = boundsFromFeature(feat);
  if (bounds) map.fitBounds(bounds, { padding: 80, maxZoom: 15 });
}

// ── Clear helpers ──────────────────────────────────────────────────────────
function clearSource(id) {
  const src = map.getSource(id);
  if (src) src.setData(EMPTY_FC);
}
function clearMapSources() {
  ['lga-src','ward-src','settlement-src','points-src'].forEach(clearSource);
}

// ── Project summary / KPIs ─────────────────────────────────────────────────
async function loadProjectSummary() {
  const d = await apiFetch(`/api/projects/${currentPid}/analytics/summary`);
  if (!d) return;
  projectSummary = d;
  document.getElementById('kpi-visited').textContent =
    `${d.visited_settlements}/${d.total_settlements}`;
  document.getElementById('kpi-comp').textContent =
    (d.completeness_pct || 0).toFixed(1);
  document.getElementById('kpi-points').textContent =
    (d.total_points || 0).toLocaleString();
}

// ── QC ─────────────────────────────────────────────────────────────────────
async function loadQCSummary() {
  const d = await apiFetch(`/api/projects/${currentPid}/qc/summary`);
  if (!d) return;
  document.getElementById('qc-total').textContent = d.total_flags || 0;
  document.getElementById('qc-oob').textContent   = d.out_of_bound || 0;
  document.getElementById('qc-tv').textContent    = d.time_violations || 0;
  document.getElementById('qc-sp').textContent    = d.stacked_points || 0;
  document.getElementById('qc-ttl').textContent   = d.total_flags || 0;
}

// ── Pie charts ─────────────────────────────────────────────────────────────
let currentPieType = null;

function togglePieModal(type) {
  const modal = document.getElementById('pie-modal');
  if (currentPieType === type && !modal.classList.contains('hidden')) {
    modal.classList.add('hidden');
    currentPieType = null;
    return;
  }
  currentPieType = type;
  modal.classList.remove('hidden');
  renderPieChart(type);
}

function renderPieChart(type) {
  const ctx = document.getElementById('pie-chart').getContext('2d');
  if (pieChart) { pieChart.destroy(); pieChart = null; }

  let visited, total, title, subtitle;
  if (type === 'coverage') {
    visited  = projectSummary.visited_settlements || 0;
    total    = projectSummary.total_settlements || 1;
    title    = 'Settlement Visitation';
    subtitle = `${visited} of ${total} settlements visited`;
  } else {
    visited  = projectSummary.visited_grids || 0;
    total    = projectSummary.total_grids || 1;
    title    = 'Grid Completeness';
    subtitle = `${visited} of ${total} grids covered`;
  }
  document.getElementById('pie-title').textContent    = title;
  document.getElementById('pie-subtitle').textContent = subtitle;

  pieChart = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: ['Visited', 'Not Visited'],
      datasets: [{ data: [visited, Math.max(0, total - visited)],
        backgroundColor: ['#16a34a','#dc2626'],
        borderColor: ['#14532d','#991b1b'], borderWidth: 2 }],
    },
    options: {
      responsive: false, cutout: '65%',
      plugins: {
        legend: { position: 'bottom', labels: { color: '#f1f5f9', font: { size: 11 }, padding: 12 } },
        tooltip: { callbacks: {
          label: ctx => {
            const pct = total > 0 ? ((ctx.raw / total) * 100).toFixed(1) : 0;
            return ` ${ctx.label}: ${ctx.raw.toLocaleString()} (${pct}%)`;
          }
        }},
      },
    },
  });
}

// ── CSV Export ─────────────────────────────────────────────────────────────
function exportTableCSV() {
  const items = navData[navLevel];
  const cols  = COLS[navLevel];
  if (!items?.length) { showToast('No data to export', 'warn'); return; }

  const header = cols.map(c => `"${c.label}"`).join(',');
  const rows   = items.map(row =>
    cols.map(c => {
      const v = row[c.key];
      if (v == null) return '';
      if (typeof v === 'string' && v.includes(',')) return `"${v}"`;
      return v;
    }).join(',')
  );
  const csv  = [header, ...rows].join('\n');
  const blob = new Blob([csv], { type: 'text/csv' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href = url; a.download = `geotracker_${navLevel}_${Date.now()}.csv`;
  a.click(); URL.revokeObjectURL(url);
}
