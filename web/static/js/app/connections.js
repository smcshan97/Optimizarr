// ============================================================
// EXTERNAL CONNECTIONS
// ============================================================

let _allConnections = [];   // cache
let _connectionEditId = null;

// --- Load & Render -------------------------------------------------

async function loadConnections() {
    const result = await apiRequest('/connections');
    if (!result) return;
    _allConnections = result;
    renderConnections(result);
    // Also refresh the scan-root dropdown inside the modal
    _populateConnectionScanRootDropdown();
}

function renderConnections(connections) {
    const container = document.getElementById('connectionsList');
    if (!container) return;

    if (!connections || connections.length === 0) {
        container.innerHTML = `
            <div class="text-center py-8 text-gray-500">
                <p class="text-2xl mb-2">🔌</p>
                <p>No connections yet.</p>
                <p class="text-sm mt-1">Add Sonarr or Radarr to import your library automatically.</p>
            </div>`;
        return;
    }

    container.innerHTML = connections.map(c => {
        const statusDot = c.enabled
            ? '<span class="w-2 h-2 rounded-full bg-green-500 inline-block mr-2"></span>'
            : '<span class="w-2 h-2 rounded-full bg-gray-500 inline-block mr-2"></span>';
        const typeLabel = c.app_type === 'radarr' ? '📽️ Radarr'
                        : c.app_type === 'sonarr' ? '📺 Sonarr'
                        : c.app_type === 'stash'  ? '🔒 Stash'
                        : c.app_type;
        const privacyBadge = c.app_type === 'stash'
            ? '<span class="text-xs bg-yellow-900 text-yellow-300 px-2 py-0.5 rounded" title="Privacy mode — stats hidden by default">🔒 Private</span>'
            : '';
        const testedAgo = c.last_tested
            ? _timeAgo(c.last_tested)
            : 'Never tested';
        const syncedAgo = c.last_synced
            ? `Last sync: ${_timeAgo(c.last_synced)}`
            : 'Never synced';
        const autoSync = (c.sync_interval_hours || 0) > 0
            ? `&nbsp;·&nbsp; <span style="color:var(--accent)" title="Library auto-syncs on this interval">⟳ Auto: ${c.sync_interval_hours}h</span>`
            : '';

        return `
        <div class="bg-gray-750 border border-gray-700 rounded-lg px-5 py-4 flex flex-col md:flex-row md:items-center gap-4"
             id="conn-card-${c.id}">

            <!-- Left: info -->
            <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2 mb-1">
                    ${statusDot}
                    <span class="font-semibold truncate">${_esc(c.name)}</span>
                    <span class="text-xs bg-gray-700 text-gray-300 px-2 py-0.5 rounded">${typeLabel}</span>
                    ${privacyBadge}
                </div>
                <p class="text-sm text-gray-400 truncate">${_esc(c.base_url)}</p>
                <p class="text-xs text-gray-500 mt-0.5">
                    Key: <code class="text-gray-400">${_esc(c.api_key_masked)}</code>
                    &nbsp;·&nbsp; ${testedAgo}
                    &nbsp;·&nbsp; ${syncedAgo}${autoSync}
                </p>
            </div>

            <!-- Right: actions -->
            <div class="flex items-center gap-2 shrink-0">
                <button onclick="testConnection(${c.id})"
                    class="bg-gray-600 hover:bg-gray-500 px-3 py-1.5 rounded text-sm transition-colors"
                    title="Test connection">🔌 Test</button>
                <button onclick="syncConnection(${c.id})"
                    class="bg-gray-600 hover:bg-gray-500 px-3 py-1.5 rounded text-sm transition-colors"
                    title="Import library into queue">⬇️ Sync</button>
                ${c.app_type !== 'stash' ? `<button onclick="registerConnectionWebhook(${c.id})"
                    class="bg-gray-600 hover:bg-gray-500 px-3 py-1.5 rounded text-sm transition-colors"
                    title="Add Optimizarr as a webhook in ${_esc(c.name)} so new downloads queue automatically">🔔 Webhook</button>` : ''}
                <button onclick="editConnection(${c.id})"
                    class="bg-gray-600 hover:bg-gray-500 px-3 py-1.5 rounded text-sm transition-colors"
                    title="Edit">✏️</button>
                <button onclick="deleteConnection(${c.id})"
                    class="bg-red-700 hover:bg-red-600 px-3 py-1.5 rounded text-sm transition-colors"
                    title="Delete">🗑️</button>
            </div>
        </div>`;
    }).join('');
}

// --- Modal helpers --------------------------------------------------

function showAddConnectionModal() {
    _connectionEditId = null;
    document.getElementById('connectionModalTitle').textContent = 'Add Connection';
    document.getElementById('connectionId').value = '';
    document.getElementById('connectionAppType').value = 'radarr';
    document.getElementById('connectionName').value = '';
    document.getElementById('connectionUrl').value = '';
    document.getElementById('connectionApiKey').value = '';
    document.getElementById('connectionShowInStats').checked = true;
    document.getElementById('connectionSyncInterval').value = '0';
    document.getElementById('connectionScanRoot').value = '';
    document.getElementById('connectionPathMappings').value = '';
    _hideConnectionTestResult();
    _populateConnectionScanRootDropdown();
    handleConnectionTypeChange();   // set URL placeholder / hints for default type
    document.getElementById('connectionModal').classList.remove('hidden');
    document.getElementById('connectionName').focus();
}

function editConnection(id) {
    const c = _allConnections.find(x => x.id === id);
    if (!c) return;
    _connectionEditId = id;

    document.getElementById('connectionModalTitle').textContent = 'Edit Connection';
    document.getElementById('connectionId').value = c.id;
    document.getElementById('connectionAppType').value = c.app_type;
    document.getElementById('connectionName').value = c.name;
    document.getElementById('connectionUrl').value = c.base_url;
    document.getElementById('connectionApiKey').value = '';   // never pre-fill
    document.getElementById('connectionApiKey').placeholder = `Key on file: ${c.api_key_masked} — leave blank to keep`;
    document.getElementById('connectionShowInStats').checked = !!c.show_in_stats;
    document.getElementById('connectionSyncInterval').value = String(c.sync_interval_hours || 0);
    document.getElementById('connectionPathMappings').value = _formatMappings(c.path_mappings);
    _hideConnectionTestResult();
    _populateConnectionScanRootDropdown(c.linked_scan_root_id);
    handleConnectionTypeChange();   // update URL hints / privacy note for this type
    document.getElementById('connectionModal').classList.remove('hidden');
}

function closeConnectionModal() {
    document.getElementById('connectionModal').classList.add('hidden');
    _connectionEditId = null;
}

function toggleConnectionKeyVisibility() {
    const input = document.getElementById('connectionApiKey');
    const btn   = input.nextElementSibling;
    if (input.type === 'password') {
        input.type = 'text';
        btn.textContent = 'Hide';
    } else {
        input.type = 'password';
        btn.textContent = 'Show';
    }
}

function _showConnectionTestResult(ok, message) {
    const el = document.getElementById('connectionTestResult');
    el.classList.remove('hidden', 'bg-green-900', 'text-green-300', 'bg-red-900', 'text-red-300');
    if (ok) {
        el.classList.add('bg-green-900', 'text-green-300');
        el.textContent = `✓ ${message}`;
    } else {
        el.classList.add('bg-red-900', 'text-red-300');
        el.textContent = `✗ ${message}`;
    }
}

function _hideConnectionTestResult() {
    document.getElementById('connectionTestResult').classList.add('hidden');
}

async function _populateConnectionScanRootDropdown(selectedId) {
    const sel = document.getElementById('connectionScanRoot');
    if (!sel) return;
    const roots = await apiRequest('/scan-roots');
    if (!roots) return;
    sel.innerHTML = `<option value="">— None (use default profile) —</option>` +
        roots.map(r => `<option value="${r.id}" ${r.id === selectedId ? 'selected' : ''}>${_esc(r.path)}</option>`).join('');
}

// --- Test / Save / Delete ------------------------------------------

async function testConnectionForm() {
    const btn = document.querySelector('button[onclick="testConnectionForm()"]');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Testing…'; }

    const appType = document.getElementById('connectionAppType').value;
    const url     = document.getElementById('connectionUrl').value.trim();
    const apiKey  = document.getElementById('connectionApiKey').value.trim();

    if (!url || !apiKey) {
        _showConnectionTestResult(false, 'URL and API Key are required to test');
        if (btn) { btn.disabled = false; btn.textContent = '🔌 Test Connection'; }
        return;
    }

    // For edit mode we may be testing with existing key — send to a temporary endpoint
    // But the server test-by-id endpoint covers that case; here we only have a new-entry scenario
    const payload = { app_type: appType, base_url: url, api_key: apiKey, name: '_test_' };
    const result = await apiRequest('/connections', {
        method: 'POST',
        body: JSON.stringify({ ...payload, _test_only: true })
    }).catch(() => null);

    // Because POST saves as side effect, we use the dedicated test endpoint if editing
    if (_connectionEditId) {
        const r = await apiRequest(`/connections/${_connectionEditId}/test`, { method: 'POST' });
        if (r) {
            _showConnectionTestResult(r.ok, r.ok
                ? `Connected to ${r.app_name} ${r.version}`
                : r.error);
        }
    } else {
        // We can't test-only without saving; show a note instead
        _showConnectionTestResult(null, '');
        // Just validate fields visually — actual test happens on save
        _showConnectionTestResult(true, 'Fields look good — connection will be tested on Save');
    }

    if (btn) { btn.disabled = false; btn.textContent = '🔌 Test Connection'; }
}

async function saveConnection() {
    const btn = document.getElementById('connectionSaveBtn');
    const origText = btn ? btn.textContent : 'Save';
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Saving…'; }
    _hideConnectionTestResult();

    const id      = document.getElementById('connectionId').value;
    const appType = document.getElementById('connectionAppType').value;
    const name    = document.getElementById('connectionName').value.trim();
    const url     = document.getElementById('connectionUrl').value.trim();
    const apiKey  = document.getElementById('connectionApiKey').value.trim();
    const stats   = document.getElementById('connectionShowInStats').checked;

    if (!name || !url) {
        _showConnectionTestResult(false, 'Name and URL are required');
        if (btn) { btn.disabled = false; btn.textContent = origText; }
        return;
    }
    if (!id && !apiKey) {
        _showConnectionTestResult(false, 'API Key is required for a new connection');
        if (btn) { btn.disabled = false; btn.textContent = origText; }
        return;
    }

    const body = { app_type: appType, name, base_url: url, show_in_stats: stats };
    body.sync_interval_hours = parseInt(document.getElementById('connectionSyncInterval').value) || 0;
    const parsed = _parseMappings(document.getElementById('connectionPathMappings').value);
    if (parsed.error) {
        _showConnectionTestResult(false, parsed.error);
        if (btn) { btn.disabled = false; btn.textContent = origText; }
        return;
    }
    body.path_mappings = parsed.mappings;
    if (apiKey) body.api_key = apiKey;

    let result;
    if (id) {
        result = await apiRequest(`/connections/${id}`, {
            method: 'PUT',
            body: JSON.stringify(body)
        });
    } else {
        result = await apiRequest('/connections', {
            method: 'POST',
            body: JSON.stringify(body)
        });
    }

    if (result) {
        const verb = id ? 'updated' : 'added';
        const versionNote = result.test_result
            ? ` (${result.test_result.app_name} ${result.test_result.version})`
            : '';
        showMessage(`✓ Connection ${verb}${versionNote}`, 'success');
        closeConnectionModal();
        loadConnections();
    }

    if (btn) { btn.disabled = false; btn.textContent = origText; }
}

async function deleteConnection(id) {
    const c = _allConnections.find(x => x.id === id);
    if (!c) return;
    if (!confirm(`Delete connection "${c.name}"?\n\nThis will not remove any queued files.`)) return;

    const result = await apiRequest(`/connections/${id}`, { method: 'DELETE' });
    if (result) {
        showMessage(`✓ Connection deleted`, 'success');
        loadConnections();
    }
}

async function testConnection(id) {
    const btn = document.querySelector(`#conn-card-${id} button[title="Test connection"]`);
    if (btn) { btn.disabled = true; btn.textContent = '⏳'; }

    const result = await apiRequest(`/connections/${id}/test`, { method: 'POST' });
    if (result) {
        if (result.ok) {
            showMessage(`✓ ${result.app_name} ${result.version} — connected`, 'success');
        } else {
            showMessage(`✗ Test failed: ${result.error}`, 'error');
        }
        loadConnections();   // refresh last_tested timestamp
    }

    if (btn) { btn.disabled = false; btn.textContent = '🔌 Test'; }
}

async function syncConnection(id) {
    const c = _allConnections.find(x => x.id === id);
    if (!c) return;
    const btn = document.querySelector(`#conn-card-${id} button[title="Import library into queue"]`);
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Syncing…'; }

    const result = await apiRequest(`/connections/${id}/sync`, { method: 'POST' });
    if (result) {
        showMessage(`⬇️ ${result.message}`, 'success');
        // Refresh queue tab after short delay so new items appear
        setTimeout(() => loadQueue(), 2000);
    }

    if (btn) { btn.disabled = false; btn.textContent = '⬇️ Sync'; }
}

// --- Utility --------------------------------------------------------

function _esc(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function _timeAgo(isoString) {
    if (!isoString) return '';
    const diff = (Date.now() - new Date(isoString).getTime()) / 1000;
    if (diff < 60)  return 'just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
}




// ── Path mappings (container path => host path) ──────────────────────────
function _formatMappings(raw) {
    let list = raw;
    if (typeof raw === 'string') { try { list = JSON.parse(raw || '[]'); } catch (_) { list = []; } }
    return (list || []).map(m => `${m.remote} => ${m.local}`).join('\n');
}

function _parseMappings(text) {
    const mappings = [];
    for (const [i, line] of text.split('\n').entries()) {
        if (!line.trim()) continue;
        const parts = line.split('=>');
        if (parts.length !== 2 || !parts[0].trim() || !parts[1].trim()) {
            return { error: `Path mapping line ${i + 1} should look like: /tv => F:\\tv shows` };
        }
        mappings.push({ remote: parts[0].trim(), local: parts[1].trim() });
    }
    return { mappings };
}

async function detectConnectionMappings() {
    const url = document.getElementById('connectionUrl').value.trim();
    if (!url) { _showConnectionTestResult(false, 'Enter the URL first'); return; }
    const res = await apiRequest('/connections/detect-mappings', {
        method: 'POST', body: JSON.stringify({ base_url: url }),
    });
    if (!res) return;
    if (!res.ok) { _showConnectionTestResult(false, res.error || 'Detection failed'); return; }
    document.getElementById('connectionPathMappings').value = _formatMappings(res.mappings);
    _showConnectionTestResult(true, `Found container "${res.container}" — ${res.mappings.length} mapping(s) filled in`);
}

async function registerConnectionWebhook(id) {
    // Sonarr/Radarr in Docker reach the host via host.docker.internal; a
    // native install would use localhost or this PC's LAN address.
    const port = location.port || '5757';
    const suggested = `http://host.docker.internal:${port}`;
    const url = prompt('Address Sonarr/Radarr should use to reach Optimizarr:', suggested);
    if (!url) return;
    const res = await apiRequest(`/connections/${id}/register-webhook`, {
        method: 'POST', body: JSON.stringify({ optimizarr_url: url.trim() }),
    });
    if (res && res.ok) {
        showMessage(`✓ Webhook ${res.updated ? 'updated' : 'added'} (${res.url})`, 'success');
    } else {
        showMessage('Webhook registration failed — check the connection and try again', 'error');
    }
}
