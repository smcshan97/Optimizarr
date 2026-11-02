// ============================================================
// UPSCALER DOWNLOAD + UPDATE CHECKER (replaces old loadUpscalers)
// ============================================================

// Track polling intervals per upscaler key
const _upscalerPollers = {};

async function loadUpscalers() {
    try {
        const info = await apiRequest('/upscalers');
        if (!info) return;
        renderUpscalerCards(info);
    } catch (err) {
        document.getElementById('upscalerStatus').innerHTML =
            '<p class="text-gray-500">Failed to detect upscalers</p>';
    }
}

function renderUpscalerCards(info) {
    const container = document.getElementById('upscalerStatus');
    if (!container) return;
    const engines = info.engines || {};
    const defs = { ...engines, ...(info.definitions || {}) };
    const det = { ...((info.detection && info.detection.details) || {}), ...engines };
    const health = info.health || {};

    container.innerHTML = Object.entries(defs).map(([key, up]) => {
        const d = det[key] || {};
        const installed = d.installed;
        const dl = d.download_state || {};
        const dlStatus = dl.status || 'idle';
        const dlProgress = dl.progress || 0;
        const isEngine = key in engines;

        let actionBtn = '';
        if (dlStatus === 'downloading') {
            actionBtn = `
                <div class="mt-2">
                    <div class="flex justify-between text-xs text-gray-400 mb-1">
                        <span>${_esc(dl.message || 'Downloading…')}</span>
                        <span>${dlProgress}%</span>
                    </div>
                    <div class="bg-gray-600 rounded-full h-1.5">
                        <div class="bg-blue-500 h-1.5 rounded-full transition-all" style="width:${dlProgress}%"></div>
                    </div>
                </div>`;
        } else if (dlStatus === 'installed' || installed) {
            actionBtn = `
                <div class="mt-2 flex items-center gap-2">
                    <span class="text-xs text-green-400">✓ ${_esc(d.version || 'Installed')}</span>
                    ${d.path ? `<span class="text-xs text-gray-500 truncate max-w-32" title="${_esc(d.path)}">${_esc(d.path)}</span>` : ''}
                    <button onclick="startUpscalerDownload('${key}')" 
                        class="text-xs text-blue-400 hover:text-blue-300 ml-auto">Update</button>
                </div>`;
        } else if (dlStatus === 'error') {
            actionBtn = `
                <div class="mt-2">
                    <p class="text-xs text-red-400">${_esc(dl.error || 'Download failed')}</p>
                    <button onclick="startUpscalerDownload('${key}')"
                        class="mt-1 btn btn-primary btn-xs">Retry</button>
                </div>`;
        } else {
            actionBtn = `
                <div class="mt-2">
                    <button onclick="startUpscalerDownload('${key}')"
                        class="btn btn-primary btn-sm">
                        ⬇ Download
                    </button>
                    <a href="${up.download_url}" target="_blank"
                        class="ml-2 text-xs text-gray-400 hover:text-gray-300">Manual →</a>
                </div>`;
        }

        const statusBadge = installed || dlStatus === 'installed'
            ? '<span class="px-2 py-0.5 bg-green-900 text-green-300 text-xs rounded">Installed</span>'
            : '<span class="px-2 py-0.5 bg-gray-600 text-gray-400 text-xs rounded">Not Installed</span>';
        // Daily self-test result (a real 2-second clip through the engine)
        const h = health[key];
        const ago = h ? Math.round((Date.now() / 1000 - h.at) / 3600) : 0;
        const healthLine = (installed && h)
            ? (h.ok
                ? `<p class="text-xs text-green-400 mt-1" title="A test clip was run through this engine">✓ Self-test passed ${ago < 1 ? 'within the hour' : ago + ' h ago'} (${h.seconds}s)</p>`
                : `<p class="text-xs mt-1" style="color:var(--warning)">⚠ Self-test failed ${ago < 1 ? 'within the hour' : ago + ' h ago'}: ${_esc(h.message)}</p>`)
            : '';

        return `
            <div class="p-4 bg-gray-700 rounded-lg" id="upscaler-card-${key}">
                <div class="flex justify-between items-start">
                    <div>
                        <span class="text-lg">${up.icon}</span>
                        <span class="font-bold ml-1">${up.name}</span>
                        <span class="ml-2">${statusBadge}</span>
                    </div>
                </div>
                <p class="text-xs text-gray-400 mt-1">${up.description}</p>
                <p class="text-xs text-gray-500">Best for: ${up.best_for}</p>
                ${healthLine}
                ${actionBtn}
                ${key === 'video2x' && installed ? `
                <label class="mt-2 flex items-center gap-2 text-xs text-gray-300">
                    <input type="checkbox" ${info.engine_pref !== 'frames' ? 'checked' : ''}
                        onchange="setUpscaleEngine(this.checked ? 'auto' : 'frames')">
                    Use for Real-ESRGAN / Real-CUGAN jobs
                </label>` : ''}
            </div>`;
    }).join('');
}

async function setUpscaleEngine(engine) {
    const res = await apiRequest('/upscalers/engine', {
        method: 'POST', body: JSON.stringify({ engine }),
    });
    if (res) showMessage(engine === 'auto'
        ? 'Video2X will be used when available'
        : 'Upscales will use the frame-extraction pipeline', 'success');
}

async function startUpscalerDownload(key) {
    const result = await apiRequest(`/upscalers/${key}/download`, { method: 'POST' });
    if (!result) return;
    showMessage(`⬇ Downloading ${key}…`, 'info');
    // Poll for progress
    if (_upscalerPollers[key]) clearInterval(_upscalerPollers[key]);
    _upscalerPollers[key] = setInterval(async () => {
        const state = await apiRequest(`/upscalers/${key}/download/status`);
        if (!state) return;
        // Re-render just this card by refreshing all upscalers
        const info = await apiRequest('/upscalers');
        if (info) renderUpscalerCards(info);
        if (state.status === 'installed' || state.status === 'error') {
            clearInterval(_upscalerPollers[key]);
            delete _upscalerPollers[key];
            if (state.status === 'installed') {
                showMessage(`✓ ${key} installed successfully!`, 'success');
            }
        }
    }, 1500);
}

async function checkUpscalerUpdates() {
    showMessage('Checking for upscaler updates…', 'info');
    const updates = await apiRequest('/upscalers/check-updates', { method: 'POST' });
    if (!updates) return;
    const updateable = Object.entries(updates).filter(([, v]) => v.update_available);
    if (updateable.length === 0) {
        showMessage('All upscalers are up to date ✓', 'success');
    } else {
        const names = updateable.map(([k, v]) => `${v.name} → ${v.latest_version}`).join(', ');
        showMessage(`Updates available: ${names}`, 'info');
    }
}

