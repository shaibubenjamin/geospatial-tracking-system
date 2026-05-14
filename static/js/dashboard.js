/* =====================================================================
   dashboard.js  —  Geospatial Tracking System
   Layout: left panel (280px) + full-height map
   Drill-down: LGA → Ward → Settlement (GPS points at settlement level)
   ===================================================================== */

// ── State ──────────────────────────────────────────────────────────────────
const API = '';
let token          = localStorage.getItem('token');
let currentPid     = null;   // project id
let navLevel       = 'lga';  // 'lga' | 'ward' | 'settlement'
let navData        = { lga: [], ward: [], settlement: [] };
let currentLGA     = null;   // { lgacode, lga_name }
let currentWard    = null;   // { wardcode, ward_name }
let currentSett    = null;   // { unique_cod, settlement_name }
let projectSummary = {};
let pieChart       = null;
let toolbarOpen    = false;
let layerPanelOpen = false;
let layerVis       = { lga: true, ward: true, settlement: true, points: true };
let activeNavIdx   = -1;

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

// ── Map sources + layers ───────────────────────────────────────────────────
const EMPTY_FC = { type: 'FeatureCollection', features: [] };

function initMapSources() {
  // ── LGA — lines only, no fill (transparent) ─────────────────────
  map.addSource('lga-src', { type: 'geojson', data: EMPTY_FC });
  map.addLayer({
    id: 'lga-line', type: 'line', source: 'lga-src', maxzoom: 10,
    paint: { 'line-color': '#3b82f6', 'line-width': 2.0, 'line-opacity': 0.85 },
  });
  map.addLayer({
    id: 'lga-label', type: 'symbol', source: 'lga-src', minzoom: 5, maxzoom: 9,
    layout: {
      'text-field': ['get', 'lga_name'],
      'text-size': 11, 'text-font': ['Open Sans Regular', 'Arial Unicode MS Regular'],
    },
    paint: { 'text-color': '#93c5fd', 'text-halo-color': '#0f172a', 'text-halo-width': 1.5 },
  });

  // ── Ward — lines only, no fill (transparent) ─────────────────────
  map.addSource('ward-src', { type: 'geojson', data: EMPTY_FC });
  map.addLayer({
    id: 'ward-line', type: 'line', source: 'ward-src', minzoom: 8, maxzoom: 13,
    paint: { 'line-color': '#a78bfa', 'line-width': 1.2, 'line-opacity': 0.75 },
  });
  map.addLayer({
    id: 'ward-label', type: 'symbol', source: 'ward-src', minzoom: 9, maxzoom: 12,
    layout: {
      'text-field': ['get', 'ward_name'], 'text-size': 10,
      'text-font': ['Open Sans Regular', 'Arial Unicode MS Regular'],
    },
    paint: { 'text-color': '#d8b4fe', 'text-halo-color': '#0f172a', 'text-halo-width': 1.2 },
  });

  // ── Settlement (visible at zoom 10+) ─────────────────────────────
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

  // ── GPS Points (visible at zoom 11+) ─────────────────────────────
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
  show(['lga-line', 'lga-label'], layerVis.lga);
  show(['ward-line', 'ward-label'], layerVis.ward);
  show(['settlement-fill', 'settlement-line', 'settlement-label'], layerVis.settlement);
  show(['points-circle'], layerVis.points);
}

// ── Map click handlers ─────────────────────────────────────────────────────
const popup = new maplibregl.Popup({ closeButton: true, maxWidth: '270px' });

function addMapClickHandlers() {
  map.on('click', 'lga-line', (e) => {
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

  map.on('click', 'ward-line', (e) => {
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

  ['lga-line','ward-line','settlement-fill','points-circle'].forEach(l => {
    map.on('mouseenter', l, () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', l, () => { map.getCanvas().style.cursor = ''; });
  });
}

// Map click drill-down helpers (called from popup HTML)
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

// ── Projects ───────────────────────────────────────────────────────────────
async function loadProjects() {
  const projects = await apiFetch('/api/projects');
  if (!projects) return;
  const sel = document.getElementById('project-switcher');
  sel.innerHTML = '<option value="">Select project…</option>';
  projects.forEach(p => {
    const o = document.createElement('option');
    o.value = p.id; o.textContent = p.name;
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
  navLevel   = 'lga';
  navData    = { lga: [], ward: [], settlement: [] };
  activeNavIdx = -1;
  updateNavHeader();
}

function resetToProject() {
  if (!currentPid) return;
  resetDrillState();
  clearSource('settlement-src');
  clearSource('points-src');
  // Remove all layer filters — show all LGAs and wards
  ['lga-line', 'lga-label', 'ward-line', 'ward-label'].forEach(id => {
    if (map.getLayer(id)) map.setFilter(id, null);
  });
  loadWardBoundaries(null); // reload all wards
  renderNav(navData.lga, 'lga');
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

// ── Metrics ────────────────────────────────────────────────────────────────
async function loadLGAMetrics() {
  const d = await apiFetch(`/api/projects/${currentPid}/analytics/lgas`);
  if (!d) return;
  navData.lga = d;
  navLevel = 'lga';
  renderNav(d, 'lga');
}

async function loadWardMetrics(lgacode) {
  const d = await apiFetch(`/api/projects/${currentPid}/analytics/wards?lgacode=${lgacode}`);
  if (!d) return;
  navData.ward = d;
  navLevel = 'ward';
  renderNav(d, 'ward');
}

async function loadSettlementMetrics(wardcode) {
  const d = await apiFetch(`/api/projects/${currentPid}/analytics/settlements?wardcode=${wardcode}`);
  if (!d) return;
  navData.settlement = d;
  navLevel = 'settlement';
  renderNav(d, 'settlement');
}

// ── Drill-down logic ───────────────────────────────────────────────────────
async function drillLGA(item) {
  currentLGA  = item;
  currentWard = currentSett = null;
  clearSource('settlement-src');
  clearSource('points-src');

  // Hide all other LGA boundaries — show only the selected one
  const lgaFilter = ['==', ['get', 'lgacode'], item.lgacode];
  ['lga-line', 'lga-label'].forEach(id => { if (map.getLayer(id)) map.setFilter(id, lgaFilter); });

  updateNavHeader();
  zoomToLGA(item.lgacode);
  await Promise.all([
    loadWardMetrics(item.lgacode),
    loadWardBoundaries(item.lgacode),      // wards for this LGA only
    loadSettlementBoundaries(item.lgacode, null),
  ]);
}

async function drillWard(item) {
  currentWard = item;
  currentSett = null;
  clearSource('points-src');

  // Hide all other ward boundaries — show only the selected one
  const wardFilter = ['==', ['get', 'wardcode'], item.wardcode];
  ['ward-line', 'ward-label'].forEach(id => { if (map.getLayer(id)) map.setFilter(id, wardFilter); });

  updateNavHeader();
  zoomToWard(item.wardcode);
  await Promise.all([
    loadSettlementMetrics(item.wardcode),
    loadSettlementBoundaries(null, item.wardcode),
  ]);
}

async function drillSettlement(item) {
  currentSett = item;
  updateNavHeader();
  zoomToSettlement(item.unique_cod);
  await loadPoints(item.unique_cod);
  if (map.getZoom() < 12) map.flyTo({ zoom: 13, duration: 600 });
}

// ── Nav back ───────────────────────────────────────────────────────────────
function navGoBack() {
  if (navLevel === 'ward') {
    // Back to LGA list: restore all LGA + ward boundaries, clear settlement
    currentWard = currentSett = null;
    clearSource('settlement-src');
    clearSource('points-src');
    // Remove all filters — show all LGAs and wards again
    ['lga-line', 'lga-label', 'ward-line', 'ward-label'].forEach(id => {
      if (map.getLayer(id)) map.setFilter(id, null);
    });
    // Reload all ward boundaries (selectProject loaded them all initially)
    loadWardBoundaries(null);
    navLevel = 'lga';
    renderNav(navData.lga, 'lga');
    currentLGA = null;
    map.flyTo({ center: [5.25, 13.05], zoom: 7, duration: 700 });
    updateNavHeader();
  } else if (navLevel === 'settlement') {
    // Back to ward list: restore all wards for the LGA, clear settlement
    currentSett = null;
    clearSource('points-src');
    // Remove ward filter — show all wards for this LGA again
    ['ward-line', 'ward-label'].forEach(id => {
      if (map.getLayer(id)) map.setFilter(id, null);
    });
    navLevel = 'ward';
    renderNav(navData.ward, 'ward');
    if (currentWard) zoomToWard(currentWard.wardcode);
    currentWard = null;
    updateNavHeader();
  }
}

// ── Nav header ─────────────────────────────────────────────────────────────
function updateNavHeader() {
  const btn   = document.getElementById('nav-back-btn');
  const title = document.getElementById('nav-hdr-title');

  if (navLevel === 'lga') {
    btn.classList.add('hidden');
    const count = navData.lga.length;
    title.textContent = `LGAs (${count})`;
  } else if (navLevel === 'ward') {
    btn.classList.remove('hidden');
    const count = navData.ward.length;
    const lname = currentLGA ? ` — ${currentLGA.lga_name}` : '';
    title.textContent = `Wards (${count})${lname}`;
  } else if (navLevel === 'settlement') {
    btn.classList.remove('hidden');
    const count = navData.settlement.length;
    const wname = currentWard ? ` — ${currentWard.ward_name}` : '';
    title.textContent = `Settlements (${count})${wname}`;
  }
}

// ── Color helper ───────────────────────────────────────────────────────────
function pctColor(pct) {
  if (pct >= 70) return '#22c55e';
  if (pct >= 40) return '#f59e0b';
  return '#ef4444';
}

// ── renderNav ──────────────────────────────────────────────────────────────
function renderNav(items, level) {
  const list = document.getElementById('nav-list');
  activeNavIdx = -1;

  if (!items || !items.length) {
    list.innerHTML = `<div style="padding:24px 16px;text-align:center;color:#475569;font-size:13px">No data</div>`;
    return;
  }

  list.innerHTML = items.map((item, idx) => {
    let name, pct, isVisited;

    if (level === 'lga') {
      name = item.lga_name || '—';
      pct  = Math.min(100, Math.max(0, Number(item.visitation_pct) || 0));
    } else if (level === 'ward') {
      name = item.ward_name || '—';
      pct  = Math.min(100, Math.max(0, Number(item.visitation_pct) || 0));
    } else {
      name      = item.settlement_name || item.unique_cod || '—';
      isVisited = item.is_visited;
      pct       = isVisited ? 100 : 0;
    }

    const color = pctColor(pct);

    let rightContent;
    if (level === 'settlement') {
      rightContent = isVisited
        ? `<span class="vis-badge yes" style="font-size:10px">✓</span>`
        : `<span class="vis-badge no"  style="font-size:10px">✗</span>`;
    } else {
      rightContent = `
        <div class="nav-item-bar"><div class="nav-item-bar-fill" style="width:${pct}%;background:${color}"></div></div>
        <span class="nav-item-pct" style="color:${color}">${pct.toFixed(0)}%</span>`;
    }

    return `<div class="nav-item" data-idx="${idx}" onclick="onNavClick(${idx},'${level}')">
      <span class="nav-item-name" title="${name}">${name}</span>
      ${rightContent}
    </div>`;
  }).join('');

  // Search filter reset
  const searchInput = document.getElementById('nav-search-input');
  if (searchInput) searchInput.value = '';

  updateNavHeader();
}

// ── Nav click ──────────────────────────────────────────────────────────────
function onNavClick(idx, level) {
  // Update active style
  document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
  const el = document.querySelector(`.nav-item[data-idx="${idx}"]`);
  if (el) el.classList.add('active');
  activeNavIdx = idx;

  const item = navData[level][idx];
  if (!item) return;

  if (level === 'lga')             drillLGA(item);
  else if (level === 'ward')       drillWard(item);
  else if (level === 'settlement') drillSettlement(item);
}

// ── Search filter ──────────────────────────────────────────────────────────
function filterNavList() {
  const q = document.getElementById('nav-search-input').value.toLowerCase();
  document.querySelectorAll('#nav-list .nav-item').forEach(el => {
    const name = el.querySelector('.nav-item-name')?.textContent.toLowerCase() || '';
    el.style.display = name.includes(q) ? '' : 'none';
  });
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
  if (bounds) {
    map.fitBounds(bounds, { padding: 60, maxZoom: 12 });
  } else {
    const all = map.getSource('ward-src')?._data;
    if (all?.features?.length) {
      const b = new maplibregl.LngLatBounds();
      all.features.forEach(f => { const b2 = boundsFromFeature(f); if (b2) b.extend(b2); });
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

  const total   = d.total_settlements   || 0;
  const visited = d.visited_settlements || 0;
  const notVis  = Math.max(0, total - visited);
  const pct     = total > 0 ? ((visited / total) * 100).toFixed(0) : 0;

  document.getElementById('vis-pct-big').textContent    = `${pct}%`;
  document.getElementById('kpi-total').textContent      = total.toLocaleString();
  document.getElementById('kpi-visited').textContent    = visited.toLocaleString();
  document.getElementById('kpi-not-visited').textContent = notVis.toLocaleString();
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

// ── Pie chart ──────────────────────────────────────────────────────────────
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

  const visited  = projectSummary.visited_settlements || 0;
  const total    = projectSummary.total_settlements   || 1;
  const title    = 'Settlement Visitation';
  const subtitle = `${visited} of ${total} settlements visited`;

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
function exportNavCSV() {
  const items = navData[navLevel];
  if (!items?.length) { showToast('No data to export', 'warn'); return; }

  let headers, rows;
  if (navLevel === 'lga') {
    headers = ['LGA', 'Total Settlements', 'Visited Settlements', 'Visitation %', 'GPS Points'];
    rows = items.map(r => [r.lga_name, r.total_settlements, r.visited_settlements, r.visitation_pct, r.point_count]);
  } else if (navLevel === 'ward') {
    headers = ['Ward', 'LGA', 'Total Settlements', 'Visited Settlements', 'Visitation %', 'GPS Points'];
    rows = items.map(r => [r.ward_name, r.lga_name, r.total_settlements, r.visited_settlements, r.visitation_pct, r.point_count]);
  } else {
    headers = ['Settlement', 'Ward', 'LGA', 'Visited', 'GPS Points'];
    rows = items.map(r => [r.settlement_name, r.ward_name, r.lga_name, r.is_visited ? 'Yes' : 'No', r.point_count]);
  }

  const header = headers.map(h => `"${h}"`).join(',');
  const body   = rows.map(row => row.map(v => {
    if (v == null) return '';
    if (typeof v === 'string' && v.includes(',')) return `"${v}"`;
    return v;
  }).join(','));

  const csv  = [header, ...body].join('\n');
  const blob = new Blob([csv], { type: 'text/csv' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href = url; a.download = `geotracker_${navLevel}_${Date.now()}.csv`;
  a.click(); URL.revokeObjectURL(url);
}
