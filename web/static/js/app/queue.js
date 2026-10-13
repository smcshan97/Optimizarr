// ============================================================
// QUEUE SYSTEM — Select All, Sorting, Original Data, Progress
// ============================================================

// Queue state
let allQueueItems = [];      // current page items only
let selectedQueueIds = new Set();
let queueSortField = 'priority';
let queueSortDir = 'asc'; // priority is a rank: 1 = first to encode
let queuePage = 1;
let queuePageSize = parseInt(localStorage.getItem('queuePageSize') || '50');
let queueTotalPages = 1;
let queueTotal = 0;
let queueCounts = {};        // per-status totals across ENTIRE queue
let queueSearchTerm = '';
let queueStatusFilter = '';
let queuePendingEta = 0;        // estimated seconds to encode ALL pending items
let queuePendingEtaUnknown = 0; // pending items with unknown duration (no ETA)

// Load queue from API → display current page
// Passive "last checked" line for the background queue check (no button —
// the check runs on a timer and after every encode).
const _QUEUE_CHECK_LABELS = {
    removed_missing: 'missing removed', duplicates_removed: 'duplicates removed',
    updated: 'refreshed', removed_optimized: 'already optimized',
    plans_synced: 'upscale plans synced', targets_synced: 'targets synced',
    unquarantined: 'un-quarantined', requeued_nonconforming: 'finished files re-queued (off-profile)',
};

async function loadQueueCheckStatus() {
    const el = document.getElementById('queueCheckStatus');
    if (!el) return;
    const res = await apiRequest('/queue/check-status');
    if (!res || !res.at) { el.textContent = ''; return; }
    const mins = Math.max(0, Math.round((Date.now() / 1000 - res.at) / 60));
    const ago = mins < 1 ? 'just now' : mins < 60 ? `${mins} min ago` : `${Math.round(mins / 60)} h ago`;
    const parts = Object.entries(res.counts || {})
        .filter(([k, v]) => v && _QUEUE_CHECK_LABELS[k])
        .map(([k, v]) => `${v} ${_QUEUE_CHECK_LABELS[k]}`);
    el.textContent = `Queue checked ${ago}` + (parts.length ? ` — ${parts.join(', ')}` : ' — all good');
    el.title = res.interval_minutes
        ? `Checked automatically every ${res.interval_minutes} min and after each encode`
        : 'Checked automatically after each encode';
}

async function loadQueue() {
    loadQueueCheckStatus();
    // Ensure profiles are cached so queue rows can render profile names
    if (Object.keys(_profilesMap).length === 0) {
        const profiles = await apiRequest('/profiles');
        if (profiles) profiles.forEach(p => { _profilesMap[p.id] = p; });
    }

    const params = new URLSearchParams({
        page: queuePage,
        page_size: queuePageSize,
        sort: queueSortField,
        dir: queueSortDir,
    });
    if (queueStatusFilter) params.set('status', queueStatusFilter);
    if (queueSearchTerm) params.set('search', queueSearchTerm);

    const data = await apiRequest('/queue?' + params.toString());
    if (data && data.items) {
        allQueueItems = data.items;
        queueTotal = data.total;
        queueTotalPages = data.total_pages;
        queuePage = data.page;
        queueCounts = data.counts || {};
        queuePendingEta = data.pending_eta_seconds || 0;
        queuePendingEtaUnknown = data.pending_eta_unknown || 0;
        displayQueueItems(allQueueItems);
    }
}

// Debounce utility — delays rapid calls (e.g. keystrokes) by `delay` ms
function debounce(fn, delay) {
    let timer;
    return function(...args) {
        clearTimeout(timer);
        timer = setTimeout(() => fn.apply(this, args), delay);
    };
}

// Called when search input or status dropdown changes
function filterQueue() {
    queueSearchTerm = (document.getElementById('queueSearch')?.value || '').trim();
    queueStatusFilter = document.getElementById('queueStatusFilter')?.value || '';
    queuePage = 1; // Reset to first page on filter change
    loadQueue();
}

const debouncedFilter = debounce(filterQueue, 300);

// Toggle sort on column header click (sorting itself is server-side)
function toggleSort(field) {
    if (queueSortField === field) {
        queueSortDir = queueSortDir === 'asc' ? 'desc' : 'asc';
    } else {
        queueSortField = field;
        queueSortDir = 'asc';
    }
    queuePage = 1;
    loadQueue();
}

// Get sort arrow indicator
function sortArrow(field) {
    if (queueSortField !== field) return '<span style="color:var(--text-muted);margin-left:4px">⇅</span>';
    return queueSortDir === 'asc' 
        ? '<span style="color:var(--accent);margin-left:4px">▲</span>' 
        : '<span style="color:var(--accent);margin-left:4px">▼</span>';
}

// Pagination controls
function goToPage(page) {
    if (page < 1 || page > queueTotalPages || page === queuePage) return;
    queuePage = page;
    loadQueue();
}

function changePageSize(size) {
    queuePageSize = parseInt(size);
    localStorage.setItem('queuePageSize', queuePageSize);
    queuePage = 1;
    loadQueue();
}

// Extract spec value from current_specs JSON
function getSpec(item, key) {
    try {
        const specs = typeof item.current_specs === 'string' ? JSON.parse(item.current_specs) : (item.current_specs || {});
        return specs[key] || '-';
    } catch { return '-'; }
}

// Format file size
function formatSize(bytes) {
    if (!bytes || bytes === 0) return '-';
    const gb = bytes / (1024 * 1024 * 1024);
    if (gb >= 1) return gb.toFixed(2) + ' GB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

function formatDuration(totalSeconds) {
    if (!totalSeconds || totalSeconds <= 0) return '-';
    const h = Math.floor(totalSeconds / 3600);
    const m = Math.floor((totalSeconds % 3600) / 60);
    const s = Math.floor(totalSeconds % 60);
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
}

// Select All / Deselect All
function toggleSelectAll(checkbox) {
    const checkboxes = document.querySelectorAll('.queue-row-checkbox');
    selectedQueueIds.clear();
    
    if (checkbox.checked) {
        checkboxes.forEach(cb => {
            cb.checked = true;
            selectedQueueIds.add(parseInt(cb.dataset.id));
        });
    } else {
        checkboxes.forEach(cb => cb.checked = false);
    }
    updateBulkBar();
}

function toggleRowSelect(checkbox, id) {
    if (checkbox.checked) {
        selectedQueueIds.add(id);
    } else {
        selectedQueueIds.delete(id);
    }
    
    // Update select-all checkbox state
    const allBoxes = document.querySelectorAll('.queue-row-checkbox');
    const selectAllBox = document.getElementById('selectAllCheckbox');
    if (selectAllBox) {
        selectAllBox.checked = selectedQueueIds.size === allBoxes.length && allBoxes.length > 0;
        selectAllBox.indeterminate = selectedQueueIds.size > 0 && selectedQueueIds.size < allBoxes.length;
    }
    updateBulkBar();
}

function deselectAll() {
    selectedQueueIds.clear();
    document.querySelectorAll('.queue-row-checkbox').forEach(cb => cb.checked = false);
    const selectAllBox = document.getElementById('selectAllCheckbox');
    if (selectAllBox) { selectAllBox.checked = false; selectAllBox.indeterminate = false; }
    updateBulkBar();
}

function updateBulkBar() {
    const bar = document.getElementById('bulkActionsBar');
    const countEl = document.getElementById('bulkSelectedCount');
    
    if (selectedQueueIds.size > 0) {
        bar.classList.remove('hidden');
        countEl.textContent = `${selectedQueueIds.size} item${selectedQueueIds.size > 1 ? 's' : ''} selected`;
    } else {
        bar.classList.add('hidden');
    }
}

async function bulkDeleteSelected() {
    if (selectedQueueIds.size === 0) return;
    if (!confirm(`Delete ${selectedQueueIds.size} selected item(s) from the queue?`)) return;
    
    let deleted = 0;
    for (const id of selectedQueueIds) {
        const result = await apiRequest(`/queue/${id}`, { method: 'DELETE' });
        if (result) deleted++;
    }
    selectedQueueIds.clear();
    showMessage(`Deleted ${deleted} item(s)`, 'success');
    loadQueue();
}

async function bulkClearCompleted() {
    const result = await apiRequest('/queue/clear?status=completed', { method: 'POST' });
    if (result) {
        selectedQueueIds.clear();
        showMessage('Cleared completed items', 'success');
        loadQueue();
    }
}

// Main display function
// ── Pipeline phases ────────────────────────────────────────────────────────
// A job runs stereo → upscale → encode → finalize. Each gets its own colour
// so a multi-hour AI phase is never mistaken for a stalled HandBrake encode,
// which is exactly what one blue bar for everything used to look like.
const PHASE_CONFIG = {
    'analyze':  { label: 'Finding quality (VMAF)', emoji: '🔬', color: 'text-amber-300', barColor: 'bg-amber-500' },
    'stereo':   { label: '3D conversion', emoji: '🎥', color: 'text-purple-300', barColor: 'bg-purple-500' },
    'upscale':  { label: 'AI upscale',    emoji: '🔼', color: 'text-cyan-300',   barColor: 'bg-cyan-500'   },
    'encode':   { label: 'Encoding',      emoji: '⚙️', color: 'text-blue-400',   barColor: 'bg-blue-500'   },
    // Finalizing verifies and moves a multi-GB file: real work, no progress
    // to report, so the bar pulses rather than sitting frozen at 100%.
    'finalize': { label: 'Finalizing',    emoji: '💾', color: 'text-emerald-300', barColor: 'bg-emerald-500', indeterminate: true },
};

function phaseConfig(phase) {
    return PHASE_CONFIG[phase] || PHASE_CONFIG['encode'];
}

// Sub-label under the bars: what phase is running, how fast, how much longer.
function phaseLine(phase, d) {
    const ph = phaseConfig(phase);
    const bits = [`${ph.emoji} ${ph.label}`];
    const fps = d.fps != null ? d.fps : d.current_fps;
    if (fps > 0) bits.push(fps.toFixed(1) + ' fps');
    const phaseEta = d.phase_eta_seconds || 0;
    if (ph.indeterminate) {
        bits.push('verifying output…');
    } else if (phaseEta > 0) {
        // During an AI phase the ETA covers that phase only — the HandBrake
        // encode still follows it — so say so rather than implying the whole
        // job finishes then. In the encode phase it IS the whole remainder.
        const isPre = phase === 'upscale' || phase === 'stereo';
        bits.push('~' + formatDuration(phaseEta)
                  + (isPre ? ' left in this step' : ' left'));
    } else if ((d.eta_seconds || 0) > 0) {
        bits.push('~' + formatDuration(d.eta_seconds) + ' left');
    } else {
        bits.push('estimating…');
    }
    return bits.join(' · ');
}

function displayQueueItems(items) {
    // Don't redraw out from under an in-progress priority edit
    if (document.activeElement && document.activeElement.classList.contains('js-priority-input')) {
        return;
    }

    const container = document.getElementById('queueTable');

    if (!items || items.length === 0) {
        container.innerHTML = '<p style="color:var(--text-muted);padding:20px 0">No items in queue. Click "Scan All" to find media files.</p>';
        document.getElementById('bulkActionsBar').classList.add('hidden');
        return;
    }

    // Use server-side counts for the full queue (not just current page)
    const counts = queueCounts;
    const totalAll = Object.values(counts).reduce((s, n) => s + n, 0);

    let html = `
        <div class="flex gap-4 mb-4 flex-wrap" style="font-size:12px;color:var(--text-muted)">
            <span>Total: <strong style="color:var(--text-primary)">${totalAll}</strong></span>
            ${counts.processing ? `<span style="color:var(--info)">⚙️ Processing: ${counts.processing}</span>` : ''}
            ${counts.pending ? `<span style="color:var(--warning)">⏳ Pending: ${counts.pending}</span>` : ''}
            ${counts.completed ? `<span style="color:var(--success)">✅ Completed: ${counts.completed}</span>` : ''}
            ${counts.failed ? `<span style="color:var(--danger)">❌ Failed: ${counts.failed}</span>` : ''}
            ${counts.paused ? `<span style="color:#e67e22">⏸️ Paused: ${counts.paused}</span>` : ''}
            ${counts.cancelled ? `<span style="color:var(--text-muted)">⏹️ Cancelled: ${counts.cancelled}</span>` : ''}
            ${counts.permission_error ? `<span style="color:var(--warning)" title="Files Optimizarr can't read/write — fix access then Re-check">🔒 Permission: ${counts.permission_error}</span>` : ''}
            ${counts.quarantined ? `<span style="color:#b07cd8" title="Files with no decodable video (corrupt, incomplete, or still downloading). The files are untouched — click to re-check them.">☣️ Quarantined: ${counts.quarantined} <button onclick="recheckAllQuarantined()" class="btn btn-secondary btn-xs" style="margin-left:4px">Re-check</button></span>` : ''}
            ${queuePendingEta > 0 ? `<span style="color:var(--accent)" title="Estimated time to encode all pending items, from your encode-speed history${queuePendingEtaUnknown > 0 ? ` (+${queuePendingEtaUnknown} item(s) with unknown duration not included — re-scan to fix)` : ''}">⏱ Queue ETA: ~${formatDuration(queuePendingEta)}${queuePendingEtaUnknown > 0 ? ' ⁺' : ''}</span>` : ''}
            <span style="margin-left:auto">Showing: <strong style="color:var(--text-primary)">${items.length}</strong> of ${queueTotal}</span>
        </div>
        <table class="tbl">
            <thead>
                <tr>
                    <th class="w-8" style="cursor:default">
                        <input type="checkbox" id="selectAllCheckbox" onchange="toggleSelectAll(this)" title="Select All">
                    </th>
                    <th class="w-6" style="cursor:default" title="Drag to reorder">⠿</th>
                    <th onclick="toggleSort('file')">File ${sortArrow('file')}</th>
                    <th onclick="toggleSort('codec')">Codec ${sortArrow('codec')}</th>
                    <th onclick="toggleSort('resolution')">Resolution ${sortArrow('resolution')}</th>
                    <th onclick="toggleSort('size')">Size ${sortArrow('size')}</th>
                    <th onclick="toggleSort('savings')">Est. Change ${sortArrow('savings')}</th>
                    <th onclick="toggleSort('duration')">Duration ${sortArrow('duration')}</th>
                    <th onclick="toggleSort('status')">Status ${sortArrow('status')}</th>
                    <th onclick="toggleSort('progress')" style="min-width:180px">Progress ${sortArrow('progress')}</th>
                    <th onclick="toggleSort('priority')">Priority ${sortArrow('priority')}</th>
                    <th style="cursor:default">Profile</th>
                    <th style="cursor:default">Actions</th>
                </tr>
            </thead>
            <tbody>
    `;

    items.forEach(item => {
        // _esc: file names arrive from downloads/webhooks — a name like
        // "<img src=x onerror=...>.mkv" must render as text, not HTML.
        const fileName = _esc(item.file_path.split(/[/\\]/).pop());
        const codec = getSpec(item, 'codec');
        const resolution = getSpec(item, 'resolution');
        const fps = getSpec(item, 'framerate');
        const bitrate = getSpec(item, 'bitrate');
        const isSelected = selectedQueueIds.has(item.id);

        const statusConfig = {
            'pending':    { emoji: '⏳', color: 'text-yellow-400', barColor: 'bg-yellow-500' },
            'processing': { emoji: '⚙️', color: 'text-blue-400',   barColor: 'bg-blue-500' },
            'completed':  { emoji: '✅', color: 'text-green-400',  barColor: 'bg-green-500' },
            'failed':     { emoji: '❌', color: 'text-red-400',    barColor: 'bg-red-500' },
            'paused':     { emoji: '⏸️', color: 'text-orange-400', barColor: 'bg-orange-500' },
            'cancelled':  { emoji: '⏹️', color: 'text-gray-400',   barColor: 'bg-gray-500' },
            'permission_error': { emoji: '🔒', color: 'text-orange-300', barColor: 'bg-orange-500' },
            'quarantined': { emoji: '☣️', color: 'text-purple-300', barColor: 'bg-purple-500' },
            'kept_original': { emoji: '↩️', color: 'text-teal-300', barColor: 'bg-teal-500' }
        };
        const sc = statusConfig[item.status] || { emoji: '❓', color: 'text-gray-400', barColor: 'bg-gray-500' };
        const progress = item.progress || 0;

        // Savings / size change column (can be negative for upscaled files)
        const savingsBytes = item.estimated_savings_bytes || 0;
        const absSavings = Math.abs(savingsBytes);
        const savingsPctItem = item.file_size_bytes > 0 ? ((absSavings / item.file_size_bytes) * 100).toFixed(0) : 0;
        let savingsDisplay;
        if (savingsBytes > 0) {
            savingsDisplay = `<span style="color:var(--success)">-${formatSize(absSavings)}</span> <span style="color:var(--text-muted);font-size:10px">(${savingsPctItem}%)</span>`;
        } else if (savingsBytes < 0) {
            savingsDisplay = `<span style="color:var(--warning)">+${formatSize(absSavings)}</span> <span style="color:var(--text-muted);font-size:10px">(↑${savingsPctItem}%)</span>`;
        } else {
            savingsDisplay = '<span style="color:var(--text-muted)">—</span>';
        }

        // Duration column — with estimated encode time underneath when known
        const durSec = item.duration_seconds || 0;
        let durDisplay = durSec > 0 ? formatDuration(durSec) : '<span style="color:var(--text-muted)">—</span>';
        if (item.eta_seconds > 0) {
            const etaLabel = item.status === 'processing' ? 'remaining' : 'encode';
            durDisplay += `<div style="color:var(--text-muted);font-size:10px;white-space:nowrap" title="Estimated ${etaLabel} time, from your encode-speed history">⏱ ~${formatDuration(item.eta_seconds)}</div>`;
        }

        // Progress bar — always rendered for consistency
        let progressBar;
        if (item.status === 'completed') {
            progressBar = `
                <div class="flex items-center gap-2">
                    <div class="flex-1 bg-gray-600 rounded-full h-2.5 overflow-hidden">
                        <div class="bg-green-500 h-2.5 rounded-full" style="width: 100%"></div>
                    </div>
                    <span class="text-green-400 font-mono text-xs font-bold w-14 text-right">100%</span>
                </div>`;
        } else if (item.status === 'failed') {
            progressBar = `
                <div class="flex items-center gap-2">
                    <div class="flex-1 bg-gray-600 rounded-full h-2.5 overflow-hidden">
                        <div class="bg-red-500 h-2.5 rounded-full" style="width: ${progress}%"></div>
                    </div>
                    <span class="text-red-400 font-mono text-xs w-14 text-right" title="${_esc(item.error_message || '')}">${progress > 0 ? progress.toFixed(1) + '%' : 'ERR'}</span>
                </div>`;
        } else if (item.status === 'kept_original') {
            progressBar = `
                <div class="flex items-center gap-2">
                    <span class="text-teal-300 text-xs truncate" title="${_esc(item.error_message || '')}">${(item.error_message || '').startsWith('Skipped') ? 'Skipped — already efficient' : "Kept original — encode wasn't smaller"}</span>
                </div>`;
        } else if (item.status === 'cancelled') {
            progressBar = `
                <div class="flex items-center gap-2">
                    <div class="flex-1 bg-gray-600 rounded-full h-2.5 overflow-hidden">
                        <div class="bg-gray-500 h-2.5 rounded-full" style="width: ${progress}%"></div>
                    </div>
                    <span class="text-gray-400 font-mono text-xs w-14 text-right" title="Manually stopped">${progress > 0 ? progress.toFixed(1) + '%' : '—'}</span>
                </div>`;
        } else if (item.status === 'processing' || (item.status === 'paused' && progress > 0)) {
            // Two bars: overall job on top, current phase underneath. A job
            // can spend hours in the AI upscale before HandBrake even starts,
            // and one undifferentiated bar made that look like a hung encode.
            // The SSE stream updates these elements in place between polls.
            // Paused keeps the orange status colour so it stays obvious at a
            // glance, but still names the phase it was paused in.
            const phBase = phaseConfig(item.phase);
            const ph = item.status === 'paused'
                ? { ...phBase, color: sc.color, barColor: sc.barColor }
                : phBase;
            const phaseProgress = item.phase_progress || 0;
            progressBar = `
                <div>
                    <div class="flex items-center gap-2">
                        <div class="flex-1 bg-gray-600 rounded-full h-2.5 overflow-hidden">
                            <div class="js-prog-bar ${ph.barColor} h-2.5 rounded-full transition-all duration-700" style="width: ${progress}%"></div>
                        </div>
                        <span class="js-prog-label ${ph.color} font-mono text-xs font-bold w-14 text-right">${progress.toFixed(1)}%</span>
                    </div>
                    <div class="flex items-center gap-2 mt-1">
                        <div class="flex-1 bg-gray-700 rounded-full h-1 overflow-hidden">
                            <div class="js-phase-bar ${ph.barColor} ${ph.indeterminate ? 'animate-pulse' : ''} h-1 rounded-full transition-all duration-700" style="width: ${ph.indeterminate ? 100 : phaseProgress}%"></div>
                        </div>
                        <span class="js-phase-label ${ph.color} font-mono text-[10px] w-14 text-right">${ph.indeterminate ? '' : phaseProgress.toFixed(0) + '%'}</span>
                    </div>
                    <div class="js-prog-eta text-xs text-gray-500 mt-0.5">${phaseLine(item.phase, item)}</div>
                </div>`;
        } else {
            // Pending — empty bar
            progressBar = `
                <div class="flex items-center gap-2">
                    <div class="flex-1 bg-gray-600 rounded-full h-2.5 overflow-hidden">
                        <div class="bg-gray-500 h-2.5 rounded-full" style="width: 0%"></div>
                    </div>
                    <span class="text-gray-500 font-mono text-xs w-14 text-right">0%</span>
                </div>`;
        }

        // Codec display with color
        const codecColors = {
            'h264': 'text-yellow-300', 'h265': 'text-blue-300', 'hevc': 'text-blue-300',
            'av1': 'text-green-300', 'vp9': 'text-purple-300', 'mpeg4': 'text-orange-300',
            'mpeg2': 'text-red-300'
        };
        const codecColor = codecColors[codec.toLowerCase()] || 'text-gray-300';

        // File info tooltip
        const tooltip = _esc(`Path: ${item.file_path}\nCodec: ${codec}\nResolution: ${resolution}\nFPS: ${fps}\nBitrate: ${bitrate}`);

        html += `
            <tr class="border-b border-gray-700 hover:bg-gray-750 ${isSelected ? 'bg-gray-700' : ''}" draggable="true" data-queue-id="${item.id}">
                <td class="py-2 px-1">
                    <input type="checkbox" class="queue-row-checkbox" data-id="${item.id}" 
                        ${isSelected ? 'checked' : ''}
                        onchange="toggleRowSelect(this, ${item.id})">
                </td>
                <td class="py-2 px-1 cursor-grab text-gray-500 text-center drag-handle" title="Drag to reorder">⠿</td>
                <td class="py-2 px-2 max-w-[220px] truncate" title="${tooltip}">
                    <span class="text-gray-100">${fileName}</span>${item.upscale_plan ? ' <span class="text-blue-400 text-xs" title="AI upscaling queued">🔼</span>' : ''}${item.stereo_plan ? ' <span class="text-purple-400 text-xs" title="3D conversion queued">🎥</span>' : ''}${item.retry_count > 0 ? ` <span class="text-xs" style="color:var(--warning)" title="Retried ${item.retry_count} time(s)">↻${item.retry_count}</span>` : ''}
                </td>
                <td class="py-2 px-2"><span class="${codecColor} text-xs font-medium">${codec.toUpperCase()}</span></td>
                <td class="py-2 px-2 text-xs text-gray-300">${resolution}${fps !== '-' ? ` <span class="text-gray-500">@ ${fps}fps</span>` : ''}</td>
                <td class="py-2 px-2 text-xs">${formatSize(item.file_size_bytes)}</td>
                <td class="py-2 px-2 text-xs">${savingsDisplay}</td>
                <td class="py-2 px-2 text-xs" style="color:var(--text-secondary)">${durDisplay}</td>
                <td class="py-2 px-2"><span class="${sc.color} text-xs" title="${_esc(item.permission_message || item.error_message || '')}">${sc.emoji} ${item.status}</span></td>
                <td class="py-2 px-2">${progressBar}</td>
                <td class="py-2 px-2 text-xs text-gray-400">
                    <span class="cursor-pointer hover:text-white" style="border-bottom:1px dotted var(--text-muted)"
                        onclick="editPriority(this, ${item.id}, ${item.priority})"
                        title="Click to change rank (1 = first to encode)">${item.priority}</span>
                </td>
                <td class="py-2 px-2">
                    ${buildProfileSelect(item)}
                </td>
                <td class="py-2 px-2">
                    <button onclick="revealQueueItem(${item.id})" class="btn btn-secondary btn-xs"
                        title="Show this file in the Optimizarr host's file manager&#10;${_esc(item.file_path)}">📂</button>
                    ${(item.status === 'failed' || item.status === 'cancelled')
                        ? `<button onclick="retryQueueItem(${item.id})" class="btn btn-secondary btn-xs" title="Re-queue this item">↻ Retry</button> `
                        : ''}
                    ${item.status === 'permission_error'
                        ? `<button onclick="recheckPermissions(${item.id})" class="btn btn-secondary btn-xs" title="Re-check file access — flips to pending if readable now">🔒 Re-check</button> `
                        : ''}
                    ${item.status === 'quarantined'
                        ? `<button onclick="recheckQuarantined(${item.id})" class="btn btn-secondary btn-xs" title="Re-probe the file — goes back to pending if it's a valid video now">☣️ Re-check</button> `
                        : ''}
                    <button onclick="deleteQueueItem(${item.id})"
                        class="btn btn-danger btn-xs">Delete</button>
                </td>
            </tr>
        `;
    });

    html += '</tbody></table>';

    // ── Pagination controls ──
    if (queueTotalPages > 1 || queuePageSize < queueTotal) {
        html += `<div class="flex items-center justify-between mt-4" style="font-size:12px;color:var(--text-muted)">`;

        // Page size selector
        html += `<div class="flex items-center gap-2">
            <span>Per page:</span>
            <select onchange="changePageSize(this.value)" style="background:var(--card-bg);color:var(--text-primary);border:1px solid var(--border);border-radius:4px;padding:2px 6px;font-size:12px">
                ${[25, 50, 100, 200].map(n =>
                    `<option value="${n}" ${n === queuePageSize ? 'selected' : ''}>${n}</option>`
                ).join('')}
            </select>
        </div>`;

        // Page navigation
        html += `<div class="flex items-center gap-1">`;
        html += `<button onclick="goToPage(1)" class="btn btn-secondary btn-xs" ${queuePage <= 1 ? 'disabled style="opacity:0.3"' : ''}>«</button>`;
        html += `<button onclick="goToPage(${queuePage - 1})" class="btn btn-secondary btn-xs" ${queuePage <= 1 ? 'disabled style="opacity:0.3"' : ''}>‹</button>`;

        const startPage = Math.max(1, queuePage - 3);
        const endPage = Math.min(queueTotalPages, startPage + 6);
        for (let p = startPage; p <= endPage; p++) {
            if (p === queuePage) {
                html += `<button class="btn btn-primary btn-xs" style="min-width:28px">${p}</button>`;
            } else {
                html += `<button onclick="goToPage(${p})" class="btn btn-secondary btn-xs" style="min-width:28px">${p}</button>`;
            }
        }

        html += `<button onclick="goToPage(${queuePage + 1})" class="btn btn-secondary btn-xs" ${queuePage >= queueTotalPages ? 'disabled style="opacity:0.3"' : ''}>›</button>`;
        html += `<button onclick="goToPage(${queueTotalPages})" class="btn btn-secondary btn-xs" ${queuePage >= queueTotalPages ? 'disabled style="opacity:0.3"' : ''}>»</button>`;
        html += `</div>`;

        html += `<span>Page ${queuePage} of ${queueTotalPages}</span>`;
        html += `</div>`;
    }

    container.innerHTML = html;
    
    // Restore selection state on select-all checkbox
    const selectAllBox = document.getElementById('selectAllCheckbox');
    if (selectAllBox) {
        const allBoxes = document.querySelectorAll('.queue-row-checkbox');
        selectAllBox.checked = selectedQueueIds.size === allBoxes.length && allBoxes.length > 0;
        selectAllBox.indeterminate = selectedQueueIds.size > 0 && selectedQueueIds.size < allBoxes.length;
    }
    updateBulkBar();

    // ── Queue drag-and-drop reorder ──────────────────────────────
    initQueueDragDrop();

    // ── Live progress stream (SSE) for the active encode ─────────
    ensureProgressStream();
}

// EventSource pushing live encode progress every ~2s. The stream is
// permanent: it opens at page load, reconnects itself after any error
// (server restart, network blip), and does NOT depend on the 5s poll,
// the auto-refresh checkbox, or which tab is active — those dependencies
// were why progress only moved after a manual refresh or tab switch.
let _progressES = null;
let _esRetryTimer = null;
let _esActiveIds = null;      // ids seen in the last SSE payload
let _esLastAutoRefresh = 0;   // throttle for transition-triggered refreshes

function _refreshQueueIfVisible() {
    // Re-render the queue table when an encode starts/finishes, so status
    // flips (pending → processing → completed) appear without manual action.
    const now = Date.now();
    if (now - _esLastAutoRefresh < 4000) return;
    _esLastAutoRefresh = now;
    if (document.hidden) return;
    const queuePanel = document.getElementById('content-queue');
    if (queuePanel && queuePanel.classList.contains('active')) {
        loadQueue();
    }
}

function ensureProgressStream() {
    if (!window.EventSource) return; // ancient browser → polling only
    if (_progressES) return;         // already streaming
    if (_esRetryTimer) { clearTimeout(_esRetryTimer); _esRetryTimer = null; }

    // Placeholder so a second caller during the token fetch doesn't open a
    // duplicate stream; replaced by the real EventSource below.
    _progressES = { close() {} };
    _openProgressStream();
}

async function _openProgressStream() {
    // The stream URL carries a one-minute, stream-only token — never the
    // API token, which would end up in server/proxy logs.
    const res = await apiRequest('/auth/stream-token', { method: 'POST' });
    if (!res || !res.token) {
        _progressES = null;
        if (!_esRetryTimer) {
            _esRetryTimer = setTimeout(() => { _esRetryTimer = null; ensureProgressStream(); }, 8000);
        }
        return;
    }
    const url = `${API_BASE}/events/encode-progress?token=${encodeURIComponent(res.token)}`;
    _progressES = new EventSource(url);

    _progressES.onmessage = (e) => {
        let payload;
        try { payload = JSON.parse(e.data); } catch (_) { return; }
        const processing = payload.processing || [];

        // Detect start/finish transitions: the set of active ids changed
        const ids = processing.map(p => p.id).sort().join(',');
        if (_esActiveIds !== null && ids !== _esActiveIds) {
            _refreshQueueIfVisible();
        }
        _esActiveIds = ids;

        processing.forEach(p => {
            const row = document.querySelector(`tr[data-queue-id="${p.id}"]`);
            if (!row) return; // active item not on the current page
            const bar = row.querySelector('.js-prog-bar');
            const label = row.querySelector('.js-prog-label');
            const eta = row.querySelector('.js-prog-eta');
            const phaseBar = row.querySelector('.js-phase-bar');
            const phaseLabel = row.querySelector('.js-phase-label');
            const ph = phaseConfig(p.phase);

            // Swap the colour classes when the job moves to another phase —
            // the row was rendered with whatever phase was current then.
            const recolor = (el, extra) => {
                if (!el) return;
                Object.values(PHASE_CONFIG).forEach(c => {
                    el.classList.remove(c.barColor, c.color);
                });
                el.classList.add(extra);
            };
            recolor(bar, ph.barColor);
            recolor(label, ph.color);
            recolor(phaseBar, ph.barColor);
            recolor(phaseLabel, ph.color);

            if (bar) bar.style.width = `${p.progress}%`;
            if (label) label.textContent = `${p.progress.toFixed(1)}%`;
            if (phaseBar) {
                phaseBar.style.width = `${ph.indeterminate ? 100 : (p.phase_progress || 0)}%`;
                phaseBar.classList.toggle('animate-pulse', !!ph.indeterminate);
            }
            if (phaseLabel) {
                phaseLabel.textContent = ph.indeterminate
                    ? '' : `${(p.phase_progress || 0).toFixed(0)}%`;
            }
            if (eta) eta.textContent = phaseLine(p.phase, p);
        });
    };

    _progressES.onerror = () => {
        // Close and retry on our own timer — never wait for a poll that may
        // not be running (wrong tab, unchecked auto-refresh, hidden page).
        if (_progressES) { _progressES.close(); _progressES = null; }
        if (!_esRetryTimer) {
            _esRetryTimer = setTimeout(() => {
                _esRetryTimer = null;
                ensureProgressStream();
            }, 8000);
        }
    };
}

function initQueueDragDrop() {
    const tbody = document.querySelector('#content-queue tbody');
    if (!tbody) return;

    let dragRow = null;

    tbody.addEventListener('dragstart', (e) => {
        const tr = e.target.closest('tr[data-queue-id]');
        if (!tr) return;
        dragRow = tr;
        tr.style.opacity = '0.4';
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', tr.dataset.queueId);
    });

    tbody.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        const tr = e.target.closest('tr[data-queue-id]');
        if (tr && tr !== dragRow) {
            const rect = tr.getBoundingClientRect();
            const midY = rect.top + rect.height / 2;
            if (e.clientY < midY) {
                tbody.insertBefore(dragRow, tr);
            } else {
                tbody.insertBefore(dragRow, tr.nextSibling);
            }
        }
    });

    tbody.addEventListener('dragend', (e) => {
        if (dragRow) {
            dragRow.style.opacity = '1';
            dragRow = null;
        }
        // Collect new order and push to server.
        // Rank scheme: 1 = first to encode. Offset by page so page 2 rows
        // get ranks after page 1's rather than colliding with them.
        const rows = tbody.querySelectorAll('tr[data-queue-id]');
        const items = [];
        const rankOffset = (queuePage - 1) * queuePageSize;
        rows.forEach((row, index) => {
            items.push({
                id: parseInt(row.dataset.queueId),
                priority: rankOffset + index + 1
            });
        });
        if (items.length > 0) {
            apiRequest('/queue/reorder', {
                method: 'POST',
                body: JSON.stringify({ items })
            }).then(() => loadQueue());
        }
    });
}

// Start/Stop encoding
async function startEncoding() {
    const result = await apiRequest('/control/start', { method: 'POST' });
    if (result) {
        showMessage(result.message || 'Encoding started', 'success');
        setTimeout(loadQueue, 1000);
    }
}

async function stopEncoding() {
    const result = await apiRequest('/control/stop', { method: 'POST' });
    if (result) {
        showMessage(result.message || 'Encoding stopped', 'info');
        loadQueue();
    }
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    // Check auth
    if (!checkAuth()) return;
    
    // Set username in sidebar
    const user = JSON.parse(localStorage.getItem('user') || '{}');
    const sidebarUser = document.getElementById('sidebarUser');
    if (sidebarUser) sidebarUser.textContent = user.username || 'admin';
    
    // Force password change if flagged (default admin on first login)
    if (user.must_change_password) {
        showTab('settings');
        setTimeout(() => {
            alert('⚠️ Your account is using the default password. Please change it now in Settings → Account.');
        }, 300);
    }
    
    // Load initial data
    loadStats();
    loadResources();
    loadQueue();

    // Live progress stream — permanent, self-reconnecting
    ensureProgressStream();

    // Auto-refresh every 5 seconds (pauses when tab is hidden)
    setInterval(() => {
        if (document.hidden) return;
        loadStats();
        loadResources();
        // Only refresh queue if auto-refresh is checked and queue tab is visible
        const autoRefresh = document.getElementById('autoRefreshQueue');
        const queuePanel = document.getElementById('content-queue');
        if (autoRefresh && autoRefresh.checked &&
            queuePanel && queuePanel.classList.contains('active')) {
            loadQueue();
        }
    }, 5000);

    // Returning to the tab: refresh everything immediately instead of
    // waiting up to 5s (or requiring a manual tab switch / page reload),
    // and make sure the SSE stream is (re)connected.
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) return;
        loadStats();
        loadResources();
        ensureProgressStream();
        const queuePanel = document.getElementById('content-queue');
        if (queuePanel && queuePanel.classList.contains('active')) {
            loadQueue();
        }
    });
});

async function reinitGpu() {
    showMessage('Re-initializing GPU monitoring...', 'info');
    const result = await apiRequest('/resources/reinit-gpu', { method: 'POST' });
    if (result) {
        showMessage(result.message, result.success !== false ? 'success' : 'warning');
        loadResources();
    }
}

// Settings functions
async function loadSettings() {
    // Load account info
    loadAccountInfo();
    
    // Load notifications
    loadNotifications();
    
    // Load resource settings
    const settings = await apiRequest('/settings/resources');
    if (settings) {
        // Master toggle
        document.getElementById('enableThrottling').checked = (settings.resource_enable_throttling || 'true') === 'true';
        document.getElementById('niceLevel').value = parseInt(settings.resource_nice_level || '10');
        
        // Trigger toggles
        document.getElementById('pauseOnCpuTemp').checked = (settings.resource_pause_on_cpu_temp || 'true') === 'true';
        document.getElementById('cpuTempThreshold').value = parseFloat(settings.resource_cpu_temp_threshold || '85');
        document.getElementById('pauseOnGpuTemp').checked = (settings.resource_pause_on_gpu_temp || 'true') === 'true';
        document.getElementById('gpuTempThreshold').value = parseFloat(settings.resource_gpu_temp_threshold || '83');
        document.getElementById('pauseOnMemory').checked = (settings.resource_pause_on_memory || 'false') === 'true';
        document.getElementById('memoryThreshold').value = parseFloat(settings.resource_memory_threshold || '85');
        document.getElementById('pauseOnCpuUsage').checked = (settings.resource_pause_on_cpu_usage || 'false') === 'true';
        document.getElementById('cpuUsageThreshold').value = parseFloat(settings.resource_cpu_usage_threshold || '95');
    }

    // Load general encoding settings (retry behavior)
    const encSettings = await apiRequest('/settings/encoding');
    if (encSettings) {
        document.getElementById('maxRetries').value = parseInt(encSettings.max_retries || '3');
        const ms = document.getElementById('minSavingsPercent');
        if (ms) ms.value = parseFloat(encSettings.min_savings_percent ?? '5');
        const mg = document.getElementById('maxConversionGrowth');
        if (mg) mg.value = parseFloat(encSettings.max_conversion_growth_percent ?? '10');
        const mb = document.getElementById('minSourceBpp');
        if (mb) mb.value = parseFloat(encSettings.min_source_bpp ?? '0.03');
        const qc = document.getElementById('queueCheckInterval');
        if (qc) qc.value = parseInt(encSettings.queue_check_interval_minutes ?? '15');
    }
}

async function saveResourceSettings() {
    const settings = {
        'resource_enable_throttling': document.getElementById('enableThrottling').checked ? 'true' : 'false',
        'resource_nice_level': document.getElementById('niceLevel').value,
        'resource_pause_on_cpu_temp': document.getElementById('pauseOnCpuTemp').checked ? 'true' : 'false',
        'resource_cpu_temp_threshold': document.getElementById('cpuTempThreshold').value,
        'resource_pause_on_gpu_temp': document.getElementById('pauseOnGpuTemp').checked ? 'true' : 'false',
        'resource_gpu_temp_threshold': document.getElementById('gpuTempThreshold').value,
        'resource_pause_on_memory': document.getElementById('pauseOnMemory').checked ? 'true' : 'false',
        'resource_memory_threshold': document.getElementById('memoryThreshold').value,
        'resource_pause_on_cpu_usage': document.getElementById('pauseOnCpuUsage').checked ? 'true' : 'false',
        'resource_cpu_usage_threshold': document.getElementById('cpuUsageThreshold').value,
    };
    
    const result = await apiRequest('/settings/resources', {
        method: 'POST',
        body: JSON.stringify(settings)
    });

    // Save general encoding settings (retry behavior)
    await apiRequest('/settings/encoding', {
        method: 'POST',
        body: JSON.stringify({
            max_retries: document.getElementById('maxRetries').value,
            min_savings_percent: document.getElementById('minSavingsPercent')?.value ?? '5',
            max_conversion_growth_percent: document.getElementById('maxConversionGrowth')?.value ?? '10',
            min_source_bpp: document.getElementById('minSourceBpp')?.value ?? '0.03',
            queue_check_interval_minutes: document.getElementById('queueCheckInterval')?.value ?? '15',
        })
    });

    if (result) {
        const msgEl = document.getElementById('settingsMessage');
        msgEl.textContent = '✓ Settings saved successfully';
        msgEl.className = 'mt-4 p-3 bg-green-900/50 border border-green-700 rounded text-green-300';
        msgEl.classList.remove('hidden');
        
        setTimeout(() => msgEl.classList.add('hidden'), 3000);
    }
}

function applyPreset(preset) {
    const presets = {
        'conservative': {
            cpuTemp: 75, gpuTemp: 72, memory: 80, cpuUsage: 90, nice: 15,
            pauseCpuTemp: true, pauseGpuTemp: true, pauseMemory: true, pauseCpuUsage: false
        },
        'balanced': {
            cpuTemp: 85, gpuTemp: 80, memory: 85, cpuUsage: 95, nice: 10,
            pauseCpuTemp: true, pauseGpuTemp: true, pauseMemory: false, pauseCpuUsage: false
        },
        'aggressive': {
            cpuTemp: 95, gpuTemp: 88, memory: 90, cpuUsage: 98, nice: 5,
            pauseCpuTemp: true, pauseGpuTemp: true, pauseMemory: false, pauseCpuUsage: false
        }
    };
    
    const config = presets[preset];
    if (config) {
        document.getElementById('cpuTempThreshold').value = config.cpuTemp;
        document.getElementById('gpuTempThreshold').value = config.gpuTemp;
        document.getElementById('memoryThreshold').value = config.memory;
        document.getElementById('cpuUsageThreshold').value = config.cpuUsage;
        document.getElementById('niceLevel').value = config.nice;
        document.getElementById('pauseOnCpuTemp').checked = config.pauseCpuTemp;
        document.getElementById('pauseOnGpuTemp').checked = config.pauseGpuTemp;
        document.getElementById('pauseOnMemory').checked = config.pauseMemory;
        document.getElementById('pauseOnCpuUsage').checked = config.pauseCpuUsage;
    }
}

