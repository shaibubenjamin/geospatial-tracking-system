/* =====================================================================
   dashboard.js — MapLibre GL JS + drill-down navigation + charts
   ===================================================================== */

const API = '';
let token = localStorage.getItem('token');
let currentProjectId = null;
let currentLGACode = null;
let currentWardCode = null;
let currentSettlementCode = null;
let navLevel = 'lga'; // lga | ward | settlement
let navData = { lga: [], ward: [], settlement: [] };
let projectList = [];
let projectSummary = {};
let pieChart = null;
let timelineChart = null;
let layerVisibility = { lga: true, ward: false, settlement: false, grid: false, points: false };

// ─── Auth guard ──────────────────────────────────────────────────────────────
if (!token) { window.location.href = '/'; }
document.getElementById('topbar-username').textContent = localStorage.getItem('username') || 'User';
if (localStorage.getItem('is_admin') === 'true') {
  document.getElementById('admin-link').style.display = 'inline-flex';
}

function handleLogout() {
  localStorage.clear();
  window.location.href = '/';
}

// ─── API helper ──────────────────────────────────────────────────────────────
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

// ─── Toast ───────────────────────────────────────────────────────────────────
function showToast(msg, type = 'info') {
  const tc = document.getElementById('toast-container');
  const el = document.createElement('div');
  el.className = `toast ${type === 'error' ? 'error' : type === 'warn' ? 'warn' : ''}`;
  el.textContent = msg;
  tc.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

// ─── MapLibre Setup ──────────────────────────────────────────────────────────
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
      'osm-tiles': {
        type: 'raster',
        tiles: [BASEMAPS.osm],
        tileSize: 256,
        attribution: '© OpenStreetMap contributors',
      },
    },
    layers: [{ id: 'osm', type: 'raster', source: 'osm-tiles' }],
  },
  center: [5.25, 13.0], // Sokoto
  zoom: 7,
});

map.addControl(new maplibregl.NavigationControl(), 'bottom-right');
map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left');

map.on('zoom', () => {
  document.getElementById('zoom-level').textContent = map.getZoom().toFixed(1);
});

map.on('load', () => {
  document.getElementById('zoom-level').textContent = map.getZoom().toFixed(1);
  initMapSources();
  loadProjects();
});

// ─── Basemap switcher ────────────────────────────────────────────────────────
function setBasemap(key, el) {
  document.querySelectorAll('.basemap-btn').forEach(b => b.classList.remove('active'));
  el.classList.add('active');
  map.getSource('osm-tiles').setTiles([BASEMAPS[key]]);
}

// ─── Map sources/layers init ─────────────────────────────────────────────────
function initMapSources() {
  const emptyGeoJSON = { type: 'FeatureCollection', features: [] };

  // LGA
  map.addSource('lga-src', { type: 'geojson', data: emptyGeoJSON });
  map.addLayer({ id: 'lga-fill', type: 'fill', source: 'lga-src',
    paint: { 'fill-color': ['interpolate', ['linear'],
      ['coalesce', ['get', 'visitation_pct'], 0], 0, '#1e40af', 50, '#3b82f6', 100, '#93c5fd'],
      'fill-opacity': 0.25 } });
  map.addLayer({ id: 'lga-line', type: 'line', source: 'lga-src',
    paint: { 'line-color': '#3b82f6', 'line-width': 2 } });
  map.addLayer({ id: 'lga-label', type: 'symbol', source: 'lga-src',
    layout: { 'text-field': ['get', 'lga_name'], 'text-size': 11,
      'text-font': ['Open Sans Regular', 'Arial Unicode MS Regular'] },
    paint: { 'text-color': '#fff', 'text-halo-color': '#0f172a', 'text-halo-width': 1.5 } });

  // Ward
  map.addSource('ward-src', { type: 'geojson', data: emptyGeoJSON });
  map.addLayer({ id: 'ward-fill', type: 'fill', source: 'ward-src',
    paint: { 'fill-color': ['interpolate', ['linear'],
      ['coalesce', ['get', 'visitation_pct'], 0], 0, '#4c1d95', 50, '#7c3aed', 100, '#a78bfa'],
      'fill-opacity': 0.3 } });
  map.addLayer({ id: 'ward-line', type: 'line', source: 'ward-src',
    paint: { 'line-color': '#8b5cf6', 'line-width': 1.5 } });
  map.addLayer({ id: 'ward-label', type: 'symbol', source: 'ward-src',
    minzoom: 9,
    layout: { 'text-field': ['get', 'ward_name'], 'text-size': 10,
      'text-font': ['Open Sans Regular', 'Arial Unicode MS Regular'] },
    paint: { 'text-color': '#e9d5ff', 'text-halo-color': '#0f172a', 'text-halo-width': 1.5 } });

  // Settlement
  map.addSource('settlement-src', { type: 'geojson', data: emptyGeoJSON });
  map.addLayer({ id: 'settlement-fill', type: 'fill', source: 'settlement-src',
    paint: { 'fill-color': ['case', ['get', 'is_visited'], '#16a34a', '#dc2626'],
      'fill-opacity': 0.5 } });
  map.addLayer({ id: 'settlement-line', type: 'line', source: 'settlement-src',
    paint: { 'line-color': ['case', ['get', 'is_visited'], '#22c55e', '#ef4444'], 'line-width': 1.2 } });

  // Grid
  map.addSource('grid-src', { type: 'geojson', data: emptyGeoJSON });
  map.addLayer({ id: 'grid-fill', type: 'fill', source: 'grid-src',
    paint: { 'fill-color': ['case', ['get', 'has_point'], '#d97706', '#374151'],
      'fill-opacity': 0.6 } });
  map.addLayer({ id: 'grid-line', type: 'line', source: 'grid-src',
    paint: { 'line-color': '#f59e0b', 'line-width': 0.8 } });

  // Points
  map.addSource('points-src', { type: 'geojson', data: emptyGeoJSON });
  map.addLayer({ id: 'points-circle', type: 'circle', source: 'points-src',
    paint: { 'circle-radius': 4, 'circle-color': '#ef4444',
      'circle-stroke-color': '#fff', 'circle-stroke-width': 1 } });

  // Apply initial visibility
  applyLayerVisibility();

  // Click handlers
  addClickHandlers();
}

// ─── Layer visibility ────────────────────────────────────────────────────────
function toggleLayer(layerName, el) {
  layerVisibility[layerName] = !layerVisibility[layerName];
  el.classList.toggle('active', layerVisibility[layerName]);
  const icon = el.querySelector('.layer-chip-eye');
  icon.className = layerVisibility[layerName] ? 'bi bi-eye layer-chip-eye' : 'bi bi-eye-slash layer-chip-eye';
  applyLayerVisibility();
}

function applyLayerVisibility() {
  const vis = layerVisibility;
  const toggle = (ids, show) => ids.forEach(id => {
    if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', show ? 'visible' : 'none');
  });
  toggle(['lga-fill', 'lga-line', 'lga-label'], vis.lga);
  toggle(['ward-fill', 'ward-line', 'ward-label'], vis.ward);
  toggle(['settlement-fill', 'settlement-line'], vis.settlement);
  toggle(['grid-fill', 'grid-line'], vis.grid);
  toggle(['points-circle'], vis.points);
}

// ─── Click handlers for map features ────────────────────────────────────────
let popup = new maplibregl.Popup({ closeButton: true, maxWidth: '280px' });

function addClickHandlers() {
  map.on('click', 'lga-fill', (e) => {
    const p = e.features[0].properties;
    const html = `
      <div style="font-size:13px;color:#f1f5f9;background:#1e293b;padding:8px 0">
        <strong style="font-size:14px">${p.lga_name}</strong><br>
        <span style="color:#94a3b8">Visited: ${p.visited_settlements}/${p.total_settlements} settlements</span><br>
        <span style="color:#22c55e">Visitation: ${p.visitation_pct}%</span><br>
        <span style="color:#94a3b8">Points: ${p.point_count}</span>
      </div>`;
    popup.setLngLat(e.lngLat).setHTML(html).addTo(map);
  });

  map.on('click', 'ward-fill', (e) => {
    const p = e.features[0].properties;
    const html = `
      <div style="font-size:13px;color:#f1f5f9;background:#1e293b;padding:8px 0">
        <strong style="font-size:14px">${p.ward_name}</strong><br>
        <span style="color:#94a3b8">${p.lga_name}</span><br>
        <span style="color:#a78bfa">Visited: ${p.visited_settlements}/${p.total_settlements} settlements</span><br>
        <span style="color:#22c55e">Visitation: ${p.visitation_pct}%</span>
      </div>`;
    popup.setLngLat(e.lngLat).setHTML(html).addTo(map);
  });

  map.on('click', 'settlement-fill', (e) => {
    const p = e.features[0].properties;
    const pct = p.completeness_pct ? p.completeness_pct.toFixed(1) : '0.0';
    const html = `
      <div style="font-size:13px;color:#f1f5f9;background:#1e293b;padding:8px 0">
        <strong style="font-size:14px">${p.settlement_name || 'Settlement'}</strong><br>
        <span style="color:#94a3b8">${p.ward_name} → ${p.lga_name}</span><br>
        <span style="${p.is_visited ? 'color:#22c55e' : 'color:#ef4444'}">
          ${p.is_visited ? '✓ Visited' : '✗ Not Visited'}
        </span><br>
        <span style="color:#fbbf24">Grids: ${p.visited_grids}/${p.total_grids} (${pct}%)</span><br>
        <span style="color:#94a3b8">Points: ${p.point_count}</span><br>
        <button onclick="drillToSettlement('${p.unique_cod}')"
          style="margin-top:6px;background:#16a34a;color:#fff;border:none;padding:4px 8px;
                 border-radius:4px;cursor:pointer;font-size:12px">
          Drill Down →
        </button>
      </div>`;
    popup.setLngLat(e.lngLat).setHTML(html).addTo(map);
  });

  map.on('click', 'grid-fill', (e) => {
    const p = e.features[0].properties;
    const html = `
      <div style="font-size:13px;color:#f1f5f9;background:#1e293b;padding:8px 0">
        <strong>Grid Cell</strong><br>
        <span style="color:#94a3b8">${p.settlement_name}</span><br>
        <span style="${p.has_point ? 'color:#22c55e' : 'color:#ef4444'}">
          ${p.has_point ? '✓ Point within 20m' : '✗ No point collected'}
        </span>
      </div>`;
    popup.setLngLat(e.lngLat).setHTML(html).addTo(map);
  });

  map.on('click', 'points-circle', (e) => {
    const p = e.features[0].properties;
    const html = `
      <div style="font-size:12px;color:#f1f5f9;background:#1e293b;padding:8px 0">
        <strong>GPS Point</strong><br>
        <span style="color:#94a3b8">Lat: ${p.latitude}, Lon: ${p.longitude}</span><br>
        ${p.research_assistant ? `<span>RA: ${p.research_assistant}</span><br>` : ''}
        ${p.collection_date ? `<span>Date: ${p.collection_date}</span><br>` : ''}
        ${p.ward_name ? `<span style="color:#94a3b8">Ward: ${p.ward_name}</span>` : ''}
      </div>`;
    popup.setLngLat(e.lngLat).setHTML(html).addTo(map);
  });

  // Cursor changes
  ['lga-fill', 'ward-fill', 'settlement-fill', 'grid-fill', 'points-circle'].forEach(l => {
    map.on('mouseenter', l, () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', l, () => { map.getCanvas().style.cursor = ''; });
  });
}

// ─── Projects ────────────────────────────────────────────────────────────────
async function loadProjects() {
  const projects = await apiFetch('/api/projects');
  if (!projects) return;
  projectList = projects;

  const sel = document.getElementById('project-switcher');
  sel.innerHTML = '<option value="">Select project...</option>';
  projects.forEach(p => {
    const opt = document.createElement('option');
    opt.value = p.id;
    opt.textContent = p.name;
    if (p.is_active) opt.selected = true;
    sel.appendChild(opt);
  });

  sel.addEventListener('change', () => {
    const pid = parseInt(sel.value);
    if (pid) selectProject(pid);
  });

  const active = projects.find(p => p.is_active) || projects[0];
  if (active) {
    sel.value = active.id;
    selectProject(active.id);
  }
}

async function selectProject(pid) {
  currentProjectId = pid;
  currentLGACode = null;
  currentWardCode = null;
  currentSettlementCode = null;
  navLevel = 'lga';
  clearMapData();
  await Promise.all([
    loadLGANav(),
    loadLGABoundaries(),
    loadProjectSummary(),
    loadQCSummary(),
  ]);
}

// ─── Navigation ──────────────────────────────────────────────────────────────
async function loadLGANav() {
  const data = await apiFetch(`/api/projects/${currentProjectId}/analytics/lgas`);
  if (!data) return;
  navData.lga = data;
  navLevel = 'lga';
  renderNav(data, 'lga');
}

async function loadWardNav(lgacode) {
  currentLGACode = lgacode;
  const data = await apiFetch(`/api/projects/${currentProjectId}/analytics/wards?lgacode=${lgacode}`);
  if (!data) return;
  navData.ward = data;
  navLevel = 'ward';
  renderNav(data, 'ward');
}

async function loadSettlementNav(wardcode) {
  currentWardCode = wardcode;
  const data = await apiFetch(`/api/projects/${currentProjectId}/analytics/settlements?wardcode=${wardcode}`);
  if (!data) return;
  navData.settlement = data;
  navLevel = 'settlement';
  renderNav(data, 'settlement');
}

function renderNav(items, level) {
  const list = document.getElementById('nav-list');
  const title = document.getElementById('nav-level-title');
  const backBtn = document.getElementById('nav-back-btn');
  const search = document.getElementById('nav-search');
  search.value = '';

  const levelNames = { lga: 'LGAs', ward: 'Wards', settlement: 'Settlements' };
  title.textContent = levelNames[level] || level;
  backBtn.style.display = level === 'lga' ? 'none' : 'inline-flex';

  list.innerHTML = '';
  if (!items.length) {
    list.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted);font-size:13px">No data found</div>';
    return;
  }

  items.forEach(item => {
    const pct = level === 'settlement'
      ? (item.completeness_pct || 0)
      : (item.visitation_pct || 0);
    const pctColor = pct >= 80 ? 'visited' : pct >= 40 ? 'partial' : '';
    const label = level === 'lga' ? item.lga_name
                : level === 'ward' ? item.ward_name
                : item.settlement_name || item.unique_cod;

    const div = document.createElement('div');
    div.className = 'nav-item';
    div.dataset.name = (label || '').toLowerCase();
    div.innerHTML = `
      <div style="flex:1;min-width:0">
        <div class="nav-item-name">${label || 'Unknown'}</div>
        <div class="nav-item-bar">
          <div class="nav-item-bar-fill" style="width:${Math.min(pct,100)}%"></div>
        </div>
      </div>
      <span class="nav-item-pct ${pctColor}">${pct.toFixed(0)}%</span>
    `;

    div.addEventListener('click', () => onNavItemClick(item, level));
    list.appendChild(div);
  });
}

function filterNavItems() {
  const query = document.getElementById('nav-search').value.toLowerCase();
  document.querySelectorAll('#nav-list .nav-item').forEach(el => {
    el.style.display = el.dataset.name.includes(query) ? '' : 'none';
  });
}

async function onNavItemClick(item, level) {
  // Highlight
  document.querySelectorAll('#nav-list .nav-item').forEach(e => e.classList.remove('active'));
  event.currentTarget.classList.add('active');

  if (level === 'lga') {
    currentLGACode = item.lgacode;
    await Promise.all([
      loadWardNav(item.lgacode),
      loadWardBoundaries(item.lgacode),
      loadSettlementBoundaries(item.lgacode, null),
    ]);
    zoomToLGA(item.lgacode);
  } else if (level === 'ward') {
    currentWardCode = item.wardcode;
    await Promise.all([
      loadSettlementNav(item.wardcode),
      loadSettlementBoundaries(null, item.wardcode),
      loadGridBoundaries(null, item.wardcode),
    ]);
    zoomToWard(item.wardcode);
  } else if (level === 'settlement') {
    currentSettlementCode = item.unique_cod;
    await Promise.all([
      loadGridBoundaries(item.unique_cod, null),
      loadPoints(item.unique_cod, null, null),
    ]);
    zoomToSettlement(item.unique_cod);
  }
}

function navBack() {
  if (navLevel === 'settlement') {
    navLevel = 'ward';
    renderNav(navData.ward, 'ward');
    clearSource('grid-src');
    clearSource('points-src');
  } else if (navLevel === 'ward') {
    navLevel = 'lga';
    renderNav(navData.lga, 'lga');
    clearSource('ward-src');
    clearSource('settlement-src');
  }
}

function drillToSettlement(unique_cod) {
  popup.remove();
  const item = navData.settlement.find(s => s.unique_cod === unique_cod);
  if (item) {
    currentSettlementCode = unique_cod;
    loadGridBoundaries(unique_cod, null);
    loadPoints(unique_cod, null, null);
    zoomToSettlement(unique_cod);
  }
}

// ─── Boundary loading ────────────────────────────────────────────────────────
async function loadLGABoundaries() {
  const data = await apiFetch(`/api/projects/${currentProjectId}/boundaries/lga/geojson`);
  if (!data) return;
  map.getSource('lga-src').setData(data);
}

async function loadWardBoundaries(lgacode) {
  const q = lgacode ? `?lgacode=${lgacode}` : '';
  const data = await apiFetch(`/api/projects/${currentProjectId}/boundaries/ward/geojson${q}`);
  if (!data) return;
  map.getSource('ward-src').setData(data);
  if (!layerVisibility.ward) {
    layerVisibility.ward = true;
    const chip = document.querySelector('.layer-chip[data-layer="ward"]');
    if (chip) { chip.classList.add('active'); chip.querySelector('.layer-chip-eye').className = 'bi bi-eye layer-chip-eye'; }
    applyLayerVisibility();
  }
}

async function loadSettlementBoundaries(lgacode, wardcode) {
  let q = '';
  if (lgacode) q = `?lgacode=${lgacode}`;
  if (wardcode) q = `?wardcode=${wardcode}`;
  const data = await apiFetch(`/api/projects/${currentProjectId}/boundaries/settlement/geojson${q}`);
  if (!data) return;
  map.getSource('settlement-src').setData(data);
  if (!layerVisibility.settlement) {
    layerVisibility.settlement = true;
    const chip = document.querySelector('.layer-chip[data-layer="settlement"]');
    if (chip) { chip.classList.add('active'); chip.querySelector('.layer-chip-eye').className = 'bi bi-eye layer-chip-eye'; }
    applyLayerVisibility();
  }
}

async function loadGridBoundaries(unique_cod, wardcode) {
  if (!unique_cod && !wardcode) return;
  const q = unique_cod ? `?unique_cod=${unique_cod}` : `?unique_cod=${wardcode}`;
  // Grid endpoint needs unique_cod
  if (!unique_cod) return;
  const data = await apiFetch(`/api/projects/${currentProjectId}/boundaries/grid/geojson?unique_cod=${unique_cod}`);
  if (!data) return;
  map.getSource('grid-src').setData(data);
  if (!layerVisibility.grid) {
    layerVisibility.grid = true;
    const chip = document.querySelector('.layer-chip[data-layer="grid"]');
    if (chip) { chip.classList.add('active'); chip.querySelector('.layer-chip-eye').className = 'bi bi-eye layer-chip-eye'; }
    applyLayerVisibility();
  }
}

async function loadPoints(unique_cod, wardcode, lgacode) {
  let q = '';
  if (unique_cod) q = `?unique_cod=${unique_cod}`;
  else if (wardcode) q = `?wardcode=${wardcode}`;
  else if (lgacode) q = `?lgacode=${lgacode}`;
  const data = await apiFetch(`/api/projects/${currentProjectId}/analytics/points/geojson${q}`);
  if (!data) return;
  map.getSource('points-src').setData(data);
  if (!layerVisibility.points) {
    layerVisibility.points = true;
    const chip = document.querySelector('.layer-chip[data-layer="points"]');
    if (chip) { chip.classList.add('active'); chip.querySelector('.layer-chip-eye').className = 'bi bi-eye layer-chip-eye'; }
    applyLayerVisibility();
  }
}

// ─── Zoom helpers ─────────────────────────────────────────────────────────────
function zoomToFeatures(source) {
  const data = map.getSource(source)?._data;
  if (!data || !data.features?.length) return;
  const bounds = new maplibregl.LngLatBounds();
  data.features.forEach(f => {
    const geom = f.geometry;
    if (!geom) return;
    if (geom.type === 'MultiPolygon') {
      geom.coordinates.forEach(poly => poly.forEach(ring => ring.forEach(c => bounds.extend(c))));
    } else if (geom.type === 'Polygon') {
      geom.coordinates.forEach(ring => ring.forEach(c => bounds.extend(c)));
    } else if (geom.type === 'Point') {
      bounds.extend(geom.coordinates);
    }
  });
  if (!bounds.isEmpty()) {
    map.fitBounds(bounds, { padding: 40, maxZoom: 14 });
  }
}

function zoomToLGA(lgacode) {
  const data = map.getSource('lga-src')?._data;
  if (!data?.features?.length) return;
  const feature = data.features.find(f => f.properties.lgacode === lgacode);
  if (!feature) return;
  const bounds = new maplibregl.LngLatBounds();
  const geom = feature.geometry;
  if (geom.type === 'MultiPolygon') {
    geom.coordinates.forEach(poly => poly.forEach(ring => ring.forEach(c => bounds.extend(c))));
  } else if (geom.type === 'Polygon') {
    geom.coordinates.forEach(ring => ring.forEach(c => bounds.extend(c)));
  }
  if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 60 });
}

function zoomToWard(wardcode) {
  const data = map.getSource('ward-src')?._data;
  if (!data?.features?.length) return;
  const feature = data.features.find(f => f.properties.wardcode === wardcode);
  if (!feature) { zoomToFeatures('ward-src'); return; }
  const bounds = new maplibregl.LngLatBounds();
  const geom = feature.geometry;
  if (geom.type === 'MultiPolygon') {
    geom.coordinates.forEach(poly => poly.forEach(ring => ring.forEach(c => bounds.extend(c))));
  } else if (geom.type === 'Polygon') {
    geom.coordinates.forEach(ring => ring.forEach(c => bounds.extend(c)));
  }
  if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 60 });
}

function zoomToSettlement(unique_cod) {
  const data = map.getSource('settlement-src')?._data;
  if (!data?.features?.length) return;
  const feature = data.features.find(f => f.properties.unique_cod === unique_cod);
  if (!feature) { zoomToFeatures('settlement-src'); return; }
  const bounds = new maplibregl.LngLatBounds();
  const geom = feature.geometry;
  if (geom.type === 'MultiPolygon') {
    geom.coordinates.forEach(poly => poly.forEach(ring => ring.forEach(c => bounds.extend(c))));
  }
  if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 80, maxZoom: 16 });
}

// ─── Clear helpers ────────────────────────────────────────────────────────────
function clearSource(sourceId) {
  const src = map.getSource(sourceId);
  if (src) src.setData({ type: 'FeatureCollection', features: [] });
}

function clearMapData() {
  ['lga-src', 'ward-src', 'settlement-src', 'grid-src', 'points-src'].forEach(clearSource);
}

// ─── Summary panel ────────────────────────────────────────────────────────────
async function loadProjectSummary() {
  const data = await apiFetch(`/api/projects/${currentProjectId}/analytics/summary`);
  if (!data) return;
  projectSummary = data;
  document.getElementById('summary-visited').textContent =
    `${data.visited_settlements}/${data.total_settlements} (${data.visitation_pct}%)`;
  document.getElementById('summary-completeness').textContent =
    `${data.completeness_pct}%`;
  document.getElementById('summary-points').textContent =
    data.total_points.toLocaleString();
}

// ─── QC ──────────────────────────────────────────────────────────────────────
async function loadQCSummary() {
  const data = await apiFetch(`/api/projects/${currentProjectId}/qc/summary`);
  if (!data) return;
  document.getElementById('qc-total').textContent = data.total_flags;
  document.getElementById('qc-out-of-bound').textContent = data.out_of_bound;
  document.getElementById('qc-time-violations').textContent = data.time_violations;
  document.getElementById('qc-stacked').textContent = data.stacked_points;
  document.getElementById('qc-total-modal').textContent = data.total_flags;
}

function toggleQCModal() {
  document.getElementById('qc-modal').classList.toggle('hidden');
}

// ─── Pie charts ──────────────────────────────────────────────────────────────
let currentPieType = null;

function togglePieModal(type) {
  const modal = document.getElementById('pie-modal');
  if (currentPieType === type && !modal.classList.contains('hidden')) {
    closePieModal();
    return;
  }
  currentPieType = type;
  modal.classList.remove('hidden');
  renderPieChart(type);
}

function closePieModal() {
  document.getElementById('pie-modal').classList.add('hidden');
  currentPieType = null;
}

function renderPieChart(type) {
  const canvas = document.getElementById('pie-chart');
  const ctx = canvas.getContext('2d');
  if (pieChart) { pieChart.destroy(); pieChart = null; }

  let visited, total, title, subtitle;
  if (type === 'coverage') {
    visited = projectSummary.visited_settlements || 0;
    total = projectSummary.total_settlements || 0;
    title = 'Settlement Visitation';
    subtitle = `${visited} of ${total} settlements visited`;
  } else {
    visited = projectSummary.visited_grids || 0;
    total = projectSummary.total_grids || 0;
    title = 'Grid Completeness';
    subtitle = `${visited} of ${total} grids covered`;
  }

  document.getElementById('pie-modal-title').textContent = title;
  document.getElementById('pie-subtitle').textContent = subtitle;

  const unvisited = Math.max(0, total - visited);
  pieChart = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: ['Visited', 'Not Visited'],
      datasets: [{
        data: [visited, unvisited],
        backgroundColor: ['#16a34a', '#dc2626'],
        borderColor: ['#15803d', '#b91c1c'],
        borderWidth: 2,
      }],
    },
    options: {
      responsive: false,
      plugins: {
        legend: { position: 'bottom', labels: { color: '#f1f5f9', font: { size: 11 } } },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const val = ctx.raw;
              const pct = total > 0 ? ((val / total) * 100).toFixed(1) : 0;
              return ` ${ctx.label}: ${val} (${pct}%)`;
            },
          },
        },
      },
    },
  });
}

// ─── Timeline chart ──────────────────────────────────────────────────────────
async function toggleTimeline() {
  const panel = document.getElementById('bottom-panel');
  if (panel.classList.contains('open')) {
    panel.classList.remove('open');
    return;
  }
  panel.classList.add('open');
  await renderTimeline();
}

async function renderTimeline() {
  if (!currentProjectId) return;
  const data = await apiFetch(`/api/projects/${currentProjectId}/analytics/timeline`);
  if (!data?.length) return;

  const canvas = document.getElementById('timeline-chart');
  const ctx = canvas.getContext('2d');
  if (timelineChart) { timelineChart.destroy(); timelineChart = null; }

  const labels = data.map(d => d.date);
  const points = data.map(d => d.cumulative_points);

  timelineChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Cumulative Points',
        data: points,
        borderColor: '#16a34a',
        backgroundColor: 'rgba(22,163,74,0.15)',
        fill: true,
        tension: 0.3,
        pointRadius: 2,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: {
          ticks: { color: '#94a3b8', maxTicksLimit: 10 },
          grid: { color: 'rgba(51,65,85,0.5)' },
        },
        y: {
          ticks: { color: '#94a3b8' },
          grid: { color: 'rgba(51,65,85,0.5)' },
        },
      },
      plugins: {
        legend: { labels: { color: '#f1f5f9' } },
      },
    },
  });
}

function toggleForecast() {
  if (!timelineChart) return;
  const checked = document.getElementById('forecast-toggle').checked;
  if (checked) addForecastLine();
  else removeForecastLine();
}

function addForecastLine() {
  if (!timelineChart?.data?.datasets) return;
  const existing = timelineChart.data.datasets[0].data;
  if (!existing.length) return;
  const n = existing.length;
  const lastVal = existing[n - 1];
  const avg = n > 1 ? (lastVal - existing[0]) / n : lastVal;
  const forecastData = [null, ...existing.map(() => null)];
  const futurePts = 14;
  const futureVals = Array.from({ length: futurePts }, (_, i) =>
    Math.round(lastVal + avg * (i + 1))
  );
  const labels = timelineChart.data.labels;
  const lastDate = new Date(labels[labels.length - 1]);
  const futureLabels = Array.from({ length: futurePts }, (_, i) => {
    const d = new Date(lastDate);
    d.setDate(d.getDate() + i + 1);
    return d.toISOString().slice(0, 10);
  });

  timelineChart.data.labels = [...labels, ...futureLabels];
  timelineChart.data.datasets[0].data = [...existing, ...Array(futurePts).fill(null)];
  timelineChart.data.datasets.push({
    label: 'Forecast',
    data: [...Array(n).fill(null), ...futureVals],
    borderColor: '#f59e0b',
    borderDash: [5, 5],
    backgroundColor: 'transparent',
    pointRadius: 2,
  });
  timelineChart.update();
}

function removeForecastLine() {
  if (!timelineChart?.data?.datasets) return;
  if (timelineChart.data.datasets.length > 1) {
    timelineChart.data.datasets.pop();
    timelineChart.update();
  }
}
