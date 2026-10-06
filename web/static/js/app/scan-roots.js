// ============================================================
// SCAN ROOTS MANAGEMENT
// ============================================================

let currentScanRootId = null;

async function loadScanRoots() {
    const roots = await apiRequest('/scan-roots');
    const container = document.getElementById('scanRootsList');
    
    if (!roots || roots.length === 0) {
        container.innerHTML = '<p class="text-gray-400">No scan roots configured. Add one to start scanning!</p>';
        return;
    }
    
    container.innerHTML = roots.map(r => `
        <div class="bg-gray-700 rounded-lg p-4 mb-3 ${r.path_exists === false ? 'border border-red-600' : ''}">
            ${r.path_exists === false ? `
            <div class="mb-3 p-3 rounded" style="background:var(--danger-dim,rgba(220,50,50,0.12));border:1px solid rgba(220,50,50,0.4)">
                <div style="color:var(--danger)" class="font-semibold text-sm">⚠️ Folder not found on disk</div>
                <p class="text-xs text-gray-300 mt-1">This drive may be unplugged, or the folder was moved or deleted. Did it move (update the path) or should this library be removed?</p>
                <div class="flex gap-2 mt-2">
                    <button onclick="editScanRoot(${r.id})" class="btn btn-primary btn-xs">📁 It moved — update path</button>
                    <button onclick="deleteScanRoot(${r.id}, '${(r.path||'').replace(/\\/g,'\\\\').replace(/'/g,"\\'").replace(/"/g,'&quot;')}')" class="btn btn-danger btn-xs">🗑 Delete this library</button>
                </div>
            </div>` : ''}
            <div class="flex justify-between items-start">
                <div class="flex-1">
                    <h3 class="font-semibold text-lg">${r.path_exists === false ? '🔌 ' : ''}${_esc(r.path)}</h3>
                    <div class="flex gap-4 mt-2 text-sm text-gray-300">
                        <div><span class="text-gray-400">Type:</span> ${getLibraryTypeLabel(r.library_type)}</div>
                        <div><span class="text-gray-400">Profile:</span> ${r.profile_name || 'Unknown'}</div>
                        <div><span class="text-gray-400">Recursive:</span> ${r.recursive ? 'Yes' : 'No'}</div>
                        <div>
                            <span class="px-2 py-1 rounded text-xs ${r.enabled ? 'bg-green-900 text-green-300' : 'bg-gray-600 text-gray-300'}">
                                ${r.enabled ? 'Enabled' : 'Disabled'}
                            </span>
                        </div>
                        <div>
                            <span class="px-2 py-1 rounded text-xs ${r.show_in_stats !== false ? 'bg-blue-900 text-blue-300' : 'bg-yellow-900 text-yellow-300'}" title="${r.show_in_stats !== false ? 'Visible in Statistics' : 'Hidden from Statistics (privacy)'}">
                                ${r.show_in_stats !== false ? '📊 Stats: On' : '🔒 Stats: Hidden'}
                            </span>
                        </div>
                        ${r.watch_enabled ? `
                        <div>
                            <span class="px-2 py-1 rounded text-xs bg-green-900 text-green-300" title="Auto-queues new files added to this folder">
                                👁️ Watching
                            </span>
                        </div>` : ''}
                    </div>
                </div>
                <div class="flex gap-2">
                    <button onclick="toggleScanRootWatch(${r.id}, ${r.watch_enabled ? 'false' : 'true'})"
                        class="btn btn-sm ${r.watch_enabled ? 'btn-success' : 'btn-secondary'}"
                        title="${r.watch_enabled ? 'Folder watching ON — click to stop watching for new files' : 'Folder watching OFF — click to auto-queue new files added here'}">
                        ${r.watch_enabled ? '👁️ Watching' : '👁️‍🗨️ Watch'}
                    </button>
                    <button onclick="scanSingleRoot(${r.id})"
                        class="btn btn-success btn-sm">
                        Scan Now
                    </button>
                    <button onclick="editScanRoot(${r.id})"
                        class="btn btn-primary btn-sm">
                        Edit
                    </button>
                    <button onclick="deleteScanRoot(${r.id}, '${r.path}')"
                        class="btn btn-danger btn-sm">
                        Delete
                    </button>
                </div>
            </div>
        </div>
    `).join('');
}

async function showCreateScanRootForm() {
    currentScanRootId = null;
    document.getElementById('scanRootModalTitle').textContent = 'Add Scan Root';
    document.getElementById('scanRootForm').reset();
    document.getElementById('scanRootId').value = '';
    document.getElementById('scanRootRecursive').checked = true;
    document.getElementById('scanRootEnabled').checked = true;
    document.getElementById('scanRootShowInStats').checked = true;
    document.getElementById('scanRootLibraryType').value = 'custom';
    document.getElementById('libraryTypeRecommendation').classList.add('hidden');

    // Reset upscale fields to defaults
    _fillUpscaleFields({
        upscale_enabled: false, upscale_trigger_below: 720,
        upscale_target_height: 1080, upscale_key: 'realesrgan',
        upscale_model: 'realesrgan-x4plus', upscale_factor: 2,
    });
    // Collapse the upscale section on new roots
    const section = document.getElementById('upscaleSection');
    const icon    = document.getElementById('upscaleSectionToggleIcon');
    if (section && !section.classList.contains('hidden')) {
        section.classList.add('hidden');
        if (icon) icon.textContent = '▶';
    }

    // Reset stereo fields to defaults and collapse
    _fillScanRootStereoFields({
        stereo_enabled: false, stereo_mode: '2d_to_3d', stereo_format: 'half_sbs',
        stereo_divergence: 2.0, stereo_convergence: 0.5, stereo_depth_model: 'Any_V2_S',
    });
    const stSection = document.getElementById('scanRootStereoSection');
    const stIcon    = document.getElementById('scanRootStereoToggleIcon');
    if (stSection && !stSection.classList.contains('hidden')) {
        stSection.classList.add('hidden');
        if (stIcon) stIcon.textContent = '▶';
    }

    // Load profiles for dropdown
    const profiles = await apiRequest('/profiles');
    const select = document.getElementById('scanRootProfile');
    select.innerHTML = profiles.map(p =>
        `<option value="${p.id}">${p.name}</option>`
    ).join('');

    document.getElementById('scanRootModal').classList.remove('hidden');
}

async function editScanRoot(id) {
    currentScanRootId = id;
    const root = await apiRequest(`/scan-roots/${id}`);
    
    if (!root) return;
    
    // Load profiles for dropdown
    const profiles = await apiRequest('/profiles');
    const select = document.getElementById('scanRootProfile');
    select.innerHTML = profiles.map(p => 
        `<option value="${p.id}" ${p.id === root.profile_id ? 'selected' : ''}>${p.name}</option>`
    ).join('');
    
    document.getElementById('scanRootModalTitle').textContent = 'Edit Scan Root';
    document.getElementById('scanRootId').value = root.id;
    document.getElementById('scanRootPath').value = root.path;
    document.getElementById('scanRootProfile').value = root.profile_id;
    document.getElementById('scanRootLibraryType').value = root.library_type || 'custom';
    document.getElementById('scanRootRecursive').checked = root.recursive;
    document.getElementById('scanRootEnabled').checked = root.enabled;
    document.getElementById('scanRootShowInStats').checked = root.show_in_stats !== false;
    _fillUpscaleFields(root);
    _fillScanRootStereoFields(root);
    
    document.getElementById('scanRootModal').classList.remove('hidden');
}

function closeScanRootModal() {
    document.getElementById('scanRootModal').classList.add('hidden');
    currentScanRootId = null;
}

document.getElementById('scanRootForm').addEventListener('submit', async (e) => {
    e.preventDefault();

    const data = {
        path:          document.getElementById('scanRootPath').value,
        profile_id:    parseInt(document.getElementById('scanRootProfile').value),
        library_type:  document.getElementById('scanRootLibraryType').value,
        recursive:     document.getElementById('scanRootRecursive').checked,
        enabled:       document.getElementById('scanRootEnabled').checked,
        show_in_stats: document.getElementById('scanRootShowInStats').checked,
        // AI upscale settings
        upscale_enabled:        document.getElementById('scanRootUpscaleEnabled')?.checked ?? false,
        upscale_trigger_below:  parseInt(document.getElementById('scanRootUpscaleTrigger')?.value || '720'),
        upscale_target_height:  parseInt(document.getElementById('scanRootUpscaleTarget')?.value  || '1080'),
        upscale_key:            document.getElementById('scanRootUpscaleKey')?.value    || 'realesrgan',
        upscale_model:          document.getElementById('scanRootUpscaleModel')?.value  || 'realesrgan-x4plus',
        upscale_factor:         parseInt(document.getElementById('scanRootUpscaleFactor')?.value || '2'),
        // 3D / stereo settings
        stereo_enabled:        document.getElementById('scanRootStereoEnabled')?.checked ?? false,
        stereo_mode:           document.getElementById('scanRootStereoMode')?.value || '2d_to_3d',
        stereo_format:         document.getElementById('scanRootStereoFormat')?.value || 'half_sbs',
        stereo_divergence:     parseFloat(document.getElementById('scanRootStereoDivergence')?.value || '2.0'),
        stereo_convergence:    parseFloat(document.getElementById('scanRootStereoConvergence')?.value || '0.5'),
        stereo_depth_model:    document.getElementById('scanRootStereoDepthModel')?.value || 'Any_V2_S',
    };

    const method = currentScanRootId ? 'PUT' : 'POST';
    const url    = currentScanRootId ? `/scan-roots/${currentScanRootId}` : '/scan-roots';

    const result = await apiRequest(url, {
        method: method,
        body: JSON.stringify(data)
    });

    if (result) {
        closeScanRootModal();
        loadScanRoots();
        showMessage('Scan root saved successfully!', 'success');
    }
});

async function deleteScanRoot(id, path) {
    if (!confirm(`Delete scan root "${path}"?`)) return;

    const result = await apiRequest(`/scan-roots/${id}`, { method: 'DELETE' });

    if (result) {
        loadScanRoots();
        showMessage('Scan root deleted successfully!', 'success');
    }
}

async function toggleScanRootWatch(id, enabled) {
    const result = await apiRequest(`/scan-roots/${id}/watch`, {
        method: 'POST',
        body: JSON.stringify({ enabled })
    });
    if (result) {
        showMessage(result.message || (enabled ? 'Folder watching enabled' : 'Folder watching disabled'),
                    result.success === false ? 'error' : 'success');
        loadScanRoots();
    }
}

async function scanSingleRoot(id) {
    // Find the scan button and disable it
    const scanBtn = event.target;
    const originalText = scanBtn.textContent;
    scanBtn.disabled = true;
    scanBtn.classList.add('opacity-50', 'cursor-not-allowed');
    scanBtn.textContent = '⏳ Scanning...';
    
    try {
        const result = await apiRequest(`/scan-roots/${id}/scan`, { method: 'POST' });
        if (result) {
            showMessage(result.message, 'success');
            loadQueue(); // Refresh queue
        }
    } catch (error) {
        showMessage('Scan failed: ' + (error.message || 'Unknown error'), 'error');
    } finally {
        // Re-enable button
        scanBtn.disabled = false;
        scanBtn.classList.remove('opacity-50', 'cursor-not-allowed');
        scanBtn.textContent = originalText;
    }
}

// ============================================================
// HELPER FUNCTIONS
// ============================================================

function showMessage(text, type = 'info') {
    const container = document.getElementById('toastContainer');
    if (!container) {
        // Fallback if container missing
        console.log(`[${type}] ${text}`);
        return;
    }
    const typeMap = { success: 'toast-success', error: 'toast-error', info: 'toast-info', warning: 'toast-warning' };
    const toast = document.createElement('div');
    toast.className = `toast ${typeMap[type] || 'toast-info'}`;
    toast.textContent = text;
    container.appendChild(toast);
    // Auto-remove after animation completes
    setTimeout(() => toast.remove(), 4000);
}
