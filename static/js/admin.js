/* =====================================================================
   admin.js — Admin panel logic
   ===================================================================== */

const API = '';
let token = localStorage.getItem('token');
let selectedCSVFile = null;

// ─── Auth guard ──────────────────────────────────────────────────────────────
if (!token) { window.location.href = '/'; }
if (localStorage.getItem('is_admin') !== 'true') {
  window.location.href = '/dashboard';
}
document.getElementById('topbar-username').textContent = localStorage.getItem('username') || 'Admin';

function handleLogout() {
  localStorage.clear();
  window.location.href = '/';
}

// ─── API helper ──────────────────────────────────────────────────────────────
async function apiFetch(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  if (!opts.body || typeof opts.body === 'string') {
    headers['Content-Type'] = 'application/json';
  }
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
  setTimeout(() => el.remove(), 4000);
}

// ─── Section nav ─────────────────────────────────────────────────────────────
function showSection(name, el) {
  document.querySelectorAll('.admin-section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.admin-nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById(`section-${name}`).classList.add('active');
  el.classList.add('active');
}

// ─── Init ─────────────────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
  loadProjects();
});

// ─── Projects ────────────────────────────────────────────────────────────────
let allProjects = [];

async function loadProjects() {
  const data = await apiFetch('/api/projects');
  if (!data) return;
  allProjects = data;
  renderProjectsList(data);
  populateProjectSelects(data);
}

function renderProjectsList(projects) {
  const el = document.getElementById('projects-list');
  if (!projects.length) {
    el.innerHTML = '<p class="text-muted text-sm">No projects yet.</p>';
    return;
  }
  el.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Name</th><th>Slug</th><th>Active</th><th>Created</th><th>Actions</th>
        </tr>
      </thead>
      <tbody>
        ${projects.map(p => `
          <tr>
            <td><strong>${p.name}</strong></td>
            <td><code style="color:var(--text-muted)">${p.slug}</code></td>
            <td>
              ${p.is_active
                ? '<span class="badge badge-green">Active</span>'
                : '<span class="badge badge-gray">Inactive</span>'}
            </td>
            <td class="text-muted text-sm">${new Date(p.created_at).toLocaleDateString()}</td>
            <td>
              ${!p.is_active ? `<button class="btn btn-secondary btn-sm" onclick="setActive(${p.id})">Set Active</button>` : ''}
              <button class="btn btn-danger btn-sm" onclick="deleteProject(${p.id},'${p.name}')">
                <i class="bi bi-trash"></i>
              </button>
            </td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
}

function populateProjectSelects(projects) {
  const ids = ['boundary-project', 'upload-project', 'compute-project', 'status-project'];
  ids.forEach(id => {
    const sel = document.getElementById(id);
    if (!sel) return;
    sel.innerHTML = '<option value="">Select project...</option>';
    projects.forEach(p => {
      const opt = document.createElement('option');
      opt.value = p.id;
      opt.textContent = p.name;
      if (p.is_active) opt.selected = true;
      sel.appendChild(opt);
    });
  });
}

async function createProject() {
  const name = document.getElementById('proj-name').value.trim();
  const slug = document.getElementById('proj-slug').value.trim();
  const description = document.getElementById('proj-desc').value.trim();
  if (!name || !slug) { showToast('Name and slug are required', 'error'); return; }

  const data = await apiFetch('/api/projects', {
    method: 'POST',
    body: JSON.stringify({ name, slug, description }),
  });
  if (!data) return;
  showToast(`Project "${name}" created`, 'info');
  document.getElementById('proj-name').value = '';
  document.getElementById('proj-slug').value = '';
  document.getElementById('proj-desc').value = '';
  loadProjects();
}

async function setActive(projectId) {
  await apiFetch(`/api/projects/${projectId}`, {
    method: 'PATCH',
    body: JSON.stringify({ is_active: true }),
  });
  showToast('Project set as active', 'info');
  loadProjects();
}

async function deleteProject(projectId, name) {
  if (!confirm(`Delete project "${name}"? This cannot be undone.`)) return;
  await apiFetch(`/api/projects/${projectId}`, { method: 'DELETE' });
  showToast(`Project "${name}" deleted`);
  loadProjects();
}

// ─── Boundary upload ─────────────────────────────────────────────────────────
function handleDragOver(e) {
  e.preventDefault();
  e.currentTarget.classList.add('dragover');
}

function handleDrop(e, type) {
  e.preventDefault();
  e.currentTarget.classList.remove('dragover');
  const file = e.dataTransfer.files[0];
  if (!file) return;
  if (type === 'csv') {
    selectedCSVFile = file;
    document.getElementById('csv-filename').textContent = file.name;
    document.getElementById('validate-btn').disabled = false;
    document.getElementById('upload-btn').disabled = false;
    return;
  }
  uploadBoundaryFile(type, file);
}

async function uploadBoundary(type, input) {
  const file = input.files[0];
  if (!file) return;
  uploadBoundaryFile(type, file);
}

async function uploadBoundaryFile(type, file) {
  const projectId = document.getElementById('boundary-project').value;
  if (!projectId) { showToast('Select a project first', 'error'); return; }

  const resultEl = document.getElementById(`${type}-result`);
  resultEl.innerHTML = '<div class="text-muted text-sm">Uploading...</div>';

  const formData = new FormData();
  formData.append('file', file);

  const headers = {};
  if (token) headers['Authorization'] = `Bearer ${token}`;

  try {
    const res = await fetch(`${API}/api/projects/${projectId}/boundaries/${type}`, {
      method: 'POST',
      headers,
      body: formData,
    });
    const data = await res.json();
    if (!res.ok) {
      resultEl.innerHTML = `<div class="badge badge-red">${data.detail || 'Upload failed'}</div>`;
      return;
    }
    resultEl.innerHTML = `
      <div class="validation-preview">
        <div class="validation-row">
          <span>Inserted</span>
          <span class="text-success fw-600">${data.inserted}</span>
        </div>
        <div class="validation-row">
          <span>Skipped</span>
          <span class="text-muted">${data.skipped}</span>
        </div>
        ${data.errors?.length ? `
          <details style="margin-top:8px">
            <summary class="text-sm text-muted" style="cursor:pointer">
              ${data.errors.length} warnings
            </summary>
            <div style="font-size:11px;color:var(--text-muted);margin-top:4px">
              ${data.errors.slice(0,5).join('<br>')}
            </div>
          </details>
        ` : ''}
      </div>
    `;
    showToast(`${type} boundaries uploaded: ${data.inserted} records`);
  } catch (err) {
    resultEl.innerHTML = `<div class="badge badge-red">Network error</div>`;
  }
}

// ─── CSV Upload ──────────────────────────────────────────────────────────────
function onCSVSelected(input) {
  selectedCSVFile = input.files[0];
  if (!selectedCSVFile) return;
  document.getElementById('csv-filename').textContent = selectedCSVFile.name;
  document.getElementById('validate-btn').disabled = false;
  document.getElementById('upload-btn').disabled = false;
}

async function validateCSV() {
  const projectId = document.getElementById('upload-project').value;
  if (!projectId) { showToast('Select a project first', 'error'); return; }
  if (!selectedCSVFile) { showToast('Select a CSV file first', 'error'); return; }

  const formData = new FormData();
  formData.append('file', selectedCSVFile);
  const headers = {};
  if (token) headers['Authorization'] = `Bearer ${token}`;

  document.getElementById('validation-card').style.display = 'block';
  document.getElementById('validation-result').innerHTML = '<div class="text-muted text-sm">Validating...</div>';

  try {
    const res = await fetch(`${API}/api/projects/${projectId}/ingest/validate`, {
      method: 'POST', headers, body: formData,
    });
    const data = await res.json();
    if (!res.ok) {
      document.getElementById('validation-result').innerHTML =
        `<div class="badge badge-red">${data.detail || 'Validation failed'}</div>`;
      return;
    }

    const errHtml = data.errors.length
      ? `<div class="validation-row"><span class="text-danger">Errors</span><span>${data.errors.join('; ')}</span></div>`
      : '';

    document.getElementById('validation-result').innerHTML = `
      <div class="validation-preview">
        <div class="validation-row"><span>Total Rows</span><span class="fw-600">${data.total_rows}</span></div>
        <div class="validation-row"><span>Valid</span><span class="text-success fw-600">${data.valid_rows}</span></div>
        <div class="validation-row"><span>Duplicates</span><span class="text-muted">${data.duplicate_rows}</span></div>
        <div class="validation-row"><span>Invalid</span><span class="text-danger">${data.invalid_rows}</span></div>
        ${errHtml}
      </div>
      ${data.sample_valid.length ? `
        <div class="mt-8 text-sm text-muted">Sample valid rows:</div>
        <div class="table-wrap mt-8" style="font-size:11px">
          <table>
            <thead><tr>${Object.keys(data.sample_valid[0]).map(k => `<th>${k}</th>`).join('')}</tr></thead>
            <tbody>
              ${data.sample_valid.map(row =>
                `<tr>${Object.values(row).map(v => `<td>${v ?? ''}</td>`).join('')}</tr>`
              ).join('')}
            </tbody>
          </table>
        </div>
      ` : ''}
    `;
    showToast(`Validation complete: ${data.valid_rows} valid rows`);
  } catch (err) {
    document.getElementById('validation-result').innerHTML =
      `<div class="badge badge-red">Network error</div>`;
  }
}

async function uploadCSV() {
  const projectId = document.getElementById('upload-project').value;
  if (!projectId) { showToast('Select a project first', 'error'); return; }
  if (!selectedCSVFile) { showToast('Select a CSV file first', 'error'); return; }

  const formData = new FormData();
  formData.append('file', selectedCSVFile);
  const headers = {};
  if (token) headers['Authorization'] = `Bearer ${token}`;

  document.getElementById('upload-progress-card').style.display = 'block';
  document.getElementById('upload-progress-content').innerHTML = `
    <div class="text-sm text-muted">Uploading <strong>${selectedCSVFile.name}</strong>...</div>
    <div class="upload-progress-bar mt-8"><div class="upload-progress-fill" style="width:40%"></div></div>
  `;
  document.getElementById('upload-btn').disabled = true;

  try {
    const res = await fetch(`${API}/api/projects/${projectId}/ingest/upload`, {
      method: 'POST', headers, body: formData,
    });
    const data = await res.json();
    if (!res.ok) {
      document.getElementById('upload-progress-content').innerHTML =
        `<div class="badge badge-red">${data.detail || 'Upload failed'}</div>`;
      return;
    }

    document.getElementById('upload-progress-content').innerHTML = `
      <div class="upload-progress-bar"><div class="upload-progress-fill" style="width:100%;background:var(--primary)"></div></div>
      <div class="validation-preview mt-8">
        <div class="validation-row"><span>Batch ID</span><span class="text-sm text-muted">${data.id}</span></div>
        <div class="validation-row"><span>Total Rows</span><span class="fw-600">${data.row_count}</span></div>
        <div class="validation-row"><span>Valid Inserted</span><span class="text-success fw-600">${data.valid_count}</span></div>
        <div class="validation-row"><span>Duplicates</span><span class="text-muted">${data.duplicate_count}</span></div>
        <div class="validation-row"><span>Status</span>
          <span class="badge ${data.status === 'processed' ? 'badge-green' : 'badge-yellow'}">${data.status}</span>
        </div>
      </div>
      <p class="text-sm text-muted mt-8">
        Background processing started — analytics will update shortly.
      </p>
    `;
    showToast('Upload complete. Analytics computing in background...');
    pollBatchStatus(projectId, data.id);
  } catch (err) {
    document.getElementById('upload-progress-content').innerHTML =
      `<div class="badge badge-red">Network error</div>`;
  } finally {
    document.getElementById('upload-btn').disabled = false;
  }
}

async function pollBatchStatus(projectId, batchId) {
  let attempts = 0;
  const interval = setInterval(async () => {
    attempts++;
    const data = await apiFetch(`/api/projects/${projectId}/ingest/batches/${batchId}`);
    if (!data) { clearInterval(interval); return; }
    if (data.status === 'processed' || data.status === 'error' || attempts > 20) {
      clearInterval(interval);
      if (data.status === 'processed') {
        showToast('Analytics computation complete!');
      } else if (data.status === 'error') {
        showToast('Processing error — check server logs', 'error');
      }
    }
  }, 3000);
}

// ─── Compute ─────────────────────────────────────────────────────────────────
async function triggerCompute(fullRecompute) {
  const projectId = document.getElementById('compute-project').value;
  if (!projectId) { showToast('Select a project first', 'error'); return; }

  document.getElementById('compute-result').innerHTML =
    '<div class="text-muted text-sm">Triggering computation...</div>';

  const data = await apiFetch(
    `/api/projects/${projectId}/analytics/compute?full_recompute=${fullRecompute}`,
    { method: 'POST' }
  );
  if (!data) return;
  document.getElementById('compute-result').innerHTML = `
    <div class="badge badge-green"><i class="bi bi-check-circle"></i> ${data.message}</div>
    <p class="text-sm text-muted mt-8">Analytics will complete in the background.</p>
  `;
  showToast('Computation triggered');
}

// ─── Upload History ───────────────────────────────────────────────────────────
async function loadBatchHistory() {
  const projectId = document.getElementById('status-project').value;
  if (!projectId) return;
  const data = await apiFetch(`/api/projects/${projectId}/ingest/batches`);
  const tbody = document.getElementById('batch-table-body');
  if (!data?.length) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-muted)">No uploads yet</td></tr>';
    return;
  }
  tbody.innerHTML = data.map(b => `
    <tr>
      <td>${b.filename || '—'}</td>
      <td>${b.row_count ?? '—'}</td>
      <td class="text-success">${b.valid_count ?? '—'}</td>
      <td class="text-muted">${b.duplicate_count ?? '—'}</td>
      <td>
        <span class="status-dot ${
          b.status === 'processed' ? 'green' : b.status === 'error' ? 'red' : 'yellow'
        }"></span>${b.status}
      </td>
      <td class="text-muted text-sm">${new Date(b.created_at).toLocaleString()}</td>
    </tr>
  `).join('');
}

// ─── Users ───────────────────────────────────────────────────────────────────
async function createUser() {
  const username = document.getElementById('new-username').value.trim();
  const password = document.getElementById('new-password').value;
  const email = document.getElementById('new-email').value.trim() || null;
  const is_admin = document.getElementById('new-is-admin').checked;

  if (!username || !password) { showToast('Username and password required', 'error'); return; }

  const data = await apiFetch('/api/auth/users', {
    method: 'POST',
    body: JSON.stringify({ username, password, email, is_admin }),
  });
  if (!data) return;

  document.getElementById('user-result').innerHTML = `
    <div class="badge badge-green"><i class="bi bi-check-circle"></i> User "${data.username}" created</div>
  `;
  document.getElementById('new-username').value = '';
  document.getElementById('new-password').value = '';
  document.getElementById('new-email').value = '';
  showToast(`User "${data.username}" created`);
}

// Auto-generate slug from name
document.getElementById('proj-name')?.addEventListener('input', (e) => {
  const slug = e.target.value.toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9-]/g, '');
  document.getElementById('proj-slug').value = slug;
});
