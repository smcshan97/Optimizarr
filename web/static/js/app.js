// Main JavaScript for Optimizarr Dashboard

// API client
const API_BASE = '/api';

// Profiles cache — populated on first load, used by queue rows for name lookups
let _profilesMap = {};

// Get auth token
function getToken() {
    return localStorage.getItem('token');
}

// Check authentication
function checkAuth() {
    const token = getToken();
    if (!token) {
        window.location.href = '/login';
        return false;
    }
    return true;
}

// Logout
function logout() {
    localStorage.removeItem('token');
    localStorage.removeItem('user');
    window.location.href = '/login';
}

// API request helper
async function apiRequest(endpoint, options = {}) {
    const token = getToken();
    
    const defaultOptions = {
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
        }
    };
    
    const response = await fetch(`${API_BASE}${endpoint}`, {
        ...defaultOptions,
        ...options,
        headers: {
            ...defaultOptions.headers,
            ...options.headers
        }
    });
    
    if (response.status === 401) {
        logout();
        return null;
    }
    
    return response.json();
}

// Tab switching
function switchTab(tabName) {
    // Hide all tabs
    document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
    document.querySelectorAll('.tab-button').forEach(el => el.classList.remove('active'));
    
    // Show selected tab
    document.getElementById(`content-${tabName}`).classList.remove('hidden');
    document.getElementById(`tab-${tabName}`).classList.add('active');
    
    // Load tab data
    if (tabName === 'queue') loadQueue();
    if (tabName === 'profiles') loadProfiles();
    if (tabName === 'scanroots') loadScanRoots();
    if (tabName === 'settings') { loadSettings(); loadConnections(); }
    if (tabName === 'schedule') loadSchedule();
    if (tabName === 'logs') loadLogs();
    if (tabName === 'watches') loadWatches();
    if (tabName === 'statistics') loadStatistics();
}

// Load statistics
async function loadStats() {
    const stats = await apiRequest('/stats');
    if (stats) {
        document.getElementById('spaceSaved').textContent = `${stats.total_space_saved_gb} GB`;
        document.getElementById('filesProcessed').textContent = stats.total_files_processed;
        document.getElementById('queuePending').textContent = stats.queue_pending;
        document.getElementById('activeJobs').textContent = stats.queue_processing;
    }
}

// Load resource monitoring data
async function loadResources() {
    const resources = await apiRequest('/resources/current');
    if (resources) {
        // CPU
        const cpuPercent = resources.cpu.percent.toFixed(1);
        document.getElementById('cpuUsage').textContent = `${cpuPercent}%`;
        document.getElementById('cpuBar').style.width = `${cpuPercent}%`;
        
        // Color based on usage
        const cpuBar = document.getElementById('cpuBar');
        const cpuUsageEl = document.getElementById('cpuUsage');
        if (cpuPercent > 90) {
            cpuBar.className = cpuBar.className.replace(/bg-\w+-\d+/, 'bg-red-500');
            cpuUsageEl.className = cpuUsageEl.className.replace(/text-\w+-\d+/, 'text-red-400');
            document.getElementById('cpuStatus').textContent = '⚠️ High';
        } else if (cpuPercent > 75) {
            cpuBar.className = cpuBar.className.replace(/bg-\w+-\d+/, 'bg-yellow-500');
            cpuUsageEl.className = cpuUsageEl.className.replace(/text-\w+-\d+/, 'text-yellow-400');
            document.getElementById('cpuStatus').textContent = '⚠️ Elevated';
        } else {
            cpuBar.className = cpuBar.className.replace(/bg-\w+-\d+/, 'bg-cyan-400');
            cpuUsageEl.className = cpuUsageEl.className.replace(/text-\w+-\d+/, 'text-cyan-400');
            document.getElementById('cpuStatus').textContent = '✓ Normal';
        }
        
        // Memory
        const memoryPercent = resources.memory.percent.toFixed(1);
        const memoryUsedGB = (resources.memory.used_mb / 1024).toFixed(1);
        const memoryTotalGB = (resources.memory.total_mb / 1024).toFixed(1);
        document.getElementById('memoryUsage').textContent = `${memoryPercent}%`;
        document.getElementById('memoryBar').style.width = `${memoryPercent}%`;
        document.getElementById('memoryStatus').textContent = `${memoryUsedGB}/${memoryTotalGB} GB`;
        
        const memoryBar = document.getElementById('memoryBar');
        const memoryUsageEl = document.getElementById('memoryUsage');
        if (memoryPercent > 85) {
            memoryBar.className = memoryBar.className.replace(/bg-\w+-\d+/, 'bg-red-500');
            memoryUsageEl.className = memoryUsageEl.className.replace(/text-\w+-\d+/, 'text-red-400');
        } else if (memoryPercent > 70) {
            memoryBar.className = memoryBar.className.replace(/bg-\w+-\d+/, 'bg-yellow-500');
            memoryUsageEl.className = memoryUsageEl.className.replace(/text-\w+-\d+/, 'text-yellow-400');
        } else {
            memoryBar.className = memoryBar.className.replace(/bg-\w+-\d+/, 'bg-purple-400');
            memoryUsageEl.className = memoryUsageEl.className.replace(/text-\w+-\d+/, 'text-purple-400');
        }
        
        // GPU
        if (resources.gpu && resources.gpu.length > 0) {
            const gpu = resources.gpu[0]; // First GPU
            const gpuPercent = gpu.utilization_percent.toFixed(1);
            document.getElementById('gpuUsage').textContent = `${gpuPercent}%`;
            document.getElementById('gpuBar').style.width = `${gpuPercent}%`;
            document.getElementById('gpuStatus').textContent = gpu.name.substring(0, 20);
            
            const gpuBar = document.getElementById('gpuBar');
            const gpuUsageEl = document.getElementById('gpuUsage');
            if (gpuPercent > 90) {
                gpuBar.className = gpuBar.className.replace(/bg-\w+-\d+/, 'bg-red-500');
                gpuUsageEl.className = gpuUsageEl.className.replace(/text-\w+-\d+/, 'text-red-400');
            } else if (gpuPercent > 75) {
                gpuBar.className = gpuBar.className.replace(/bg-\w+-\d+/, 'bg-yellow-500');
                gpuUsageEl.className = gpuUsageEl.className.replace(/text-\w+-\d+/, 'text-yellow-400');
            } else {
                gpuBar.className = gpuBar.className.replace(/bg-\w+-\d+/, 'bg-orange-400');
                gpuUsageEl.className = gpuUsageEl.className.replace(/text-\w+-\d+/, 'text-orange-400');
            }
        } else {
            document.getElementById('gpuUsage').textContent = 'N/A';
            document.getElementById('gpuStatus').textContent = 'No GPU detected';
        }
    }
}

// ============================================================
// QUEUE SYSTEM — Select All, Sorting, Original Data, Progress
// ============================================================

// Queue state
let allQueueItems = [];
let selectedQueueIds = new Set();
let queueSortField = 'created_at';
let queueSortDir = 'desc'; // 'asc' or 'desc'

// Load queue from API → store → filter → display
async function loadQueue() {
    // Ensure profiles are cached so queue rows can render profile names
    if (Object.keys(_profilesMap).length === 0) {
        const profiles = await apiRequest('/profiles');
        if (profiles) profiles.forEach(p => { _profilesMap[p.id] = p; });
    }
    const items = await apiRequest('/queue');
    if (items) {
        allQueueItems = items;
        filterQueue();
    }
}

// Filter queue by search and status, then sort and display
function filterQueue() {
    const searchTerm = (document.getElementById('queueSearch')?.value || '').toLowerCase();
    const statusFilter = document.getElementById('queueStatusFilter')?.value || '';
    
    let filtered = allQueueItems;
    
    if (searchTerm) {
        filtered = filtered.filter(item => item.file_path.toLowerCase().includes(searchTerm));
    }
    if (statusFilter) {
        filtered = filtered.filter(item => item.status === statusFilter);
    }
    
    // Apply sort
    filtered = sortQueueItems(filtered, queueSortField, queueSortDir);
    
    displayQueueItems(filtered);
}

// Sort queue items by any field
function sortQueueItems(items, field, dir) {
    return [...items].sort((a, b) => {
        let valA, valB;
        
        // Extract sortable values based on field
        switch (field) {
            case 'file':
                valA = a.file_path.split(/[/\\]/).pop().toLowerCase();
                valB = b.file_path.split(/[/\\]/).pop().toLowerCase();
                break;
            case 'status':
                const statusOrder = { processing: 0, paused: 1, pending: 2, failed: 3, completed: 4 };
                valA = statusOrder[a.status] ?? 5;
                valB = statusOrder[b.status] ?? 5;
                break;
            case 'progress':
                valA = a.progress || 0;
                valB = b.progress || 0;
                break;
            case 'size':
                valA = a.file_size_bytes || 0;
                valB = b.file_size_bytes || 0;
                break;
            case 'savings':
                valA = a.estimated_savings_bytes || 0;
                valB = b.estimated_savings_bytes || 0;
                break;
            case 'codec':
                valA = getSpec(a, 'codec');
                valB = getSpec(b, 'codec');
                break;
            case 'resolution':
                valA = getSpec(a, 'resolution');
                valB = getSpec(b, 'resolution');
                break;
            case 'priority':
                valA = a.priority || 50;
                valB = b.priority || 50;
                break;
            default: // created_at
                valA = a.created_at || '';
                valB = b.created_at || '';
                break;
        }
        
        // Compare
        if (typeof valA === 'string') {
            const cmp = valA.localeCompare(valB);
            return dir === 'asc' ? cmp : -cmp;
        }
        return dir === 'asc' ? (valA - valB) : (valB - valA);
    });
}

// Toggle sort on column header click
function toggleSort(field) {
    if (queueSortField === field) {
        queueSortDir = queueSortDir === 'asc' ? 'desc' : 'asc';
    } else {
        queueSortField = field;
        queueSortDir = 'asc';
    }
    filterQueue();
}

// Get sort arrow indicator
function sortArrow(field) {
    if (queueSortField !== field) return '<span class="text-gray-600 ml-1">⇅</span>';
    return queueSortDir === 'asc' 
        ? '<span class="text-blue-400 ml-1">▲</span>' 
        : '<span class="text-blue-400 ml-1">▼</span>';
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
function displayQueueItems(items) {
    const container = document.getElementById('queueTable');

    if (!items || items.length === 0) {
        container.innerHTML = '<p class="text-gray-400">No items in queue. Click "Scan All" to find media files.</p>';
        document.getElementById('bulkActionsBar').classList.add('hidden');
        return;
    }

    // Count statuses for summary
    const counts = { pending: 0, processing: 0, completed: 0, failed: 0, paused: 0 };
    let totalSize = 0, totalSavings = 0;
    items.forEach(item => { 
        counts[item.status] = (counts[item.status] || 0) + 1;
        totalSize += item.file_size_bytes || 0;
        totalSavings += item.estimated_savings_bytes || 0;
    });

    const savingsPct = totalSize > 0 ? ((totalSavings / totalSize) * 100).toFixed(1) : 0;

    let html = `
        <div class="flex gap-4 mb-4 text-xs text-gray-400 flex-wrap">
            <span>Total: <strong class="text-gray-200">${items.length}</strong></span>
            ${counts.processing ? `<span class="text-blue-400">⚙️ Processing: ${counts.processing}</span>` : ''}
            ${counts.pending ? `<span class="text-yellow-400">⏳ Pending: ${counts.pending}</span>` : ''}
            ${counts.completed ? `<span class="text-green-400">✅ Completed: ${counts.completed}</span>` : ''}
            ${counts.failed ? `<span class="text-red-400">❌ Failed: ${counts.failed}</span>` : ''}
            ${counts.paused ? `<span class="text-orange-400">⏸️ Paused: ${counts.paused}</span>` : ''}
            <span class="ml-auto">Size: <strong class="text-gray-200">${formatSize(totalSize)}</strong></span>
            <span>Projected Savings: <strong class="text-green-400">${formatSize(totalSavings)} (${savingsPct}%)</strong></span>
        </div>
        <table class="w-full text-sm">
            <thead class="border-b border-gray-600">
                <tr class="text-left text-gray-400 text-xs uppercase tracking-wider">
                    <th class="py-2 px-1 w-8">
                        <input type="checkbox" id="selectAllCheckbox" onchange="toggleSelectAll(this)" title="Select All">
                    </th>
                    <th class="py-2 px-2 cursor-pointer hover:text-white select-none" onclick="toggleSort('file')">
                        File ${sortArrow('file')}
                    </th>
                    <th class="py-2 px-2 cursor-pointer hover:text-white select-none" onclick="toggleSort('codec')">
                        Codec ${sortArrow('codec')}
                    </th>
                    <th class="py-2 px-2 cursor-pointer hover:text-white select-none" onclick="toggleSort('resolution')">
                        Resolution ${sortArrow('resolution')}
                    </th>
                    <th class="py-2 px-2 cursor-pointer hover:text-white select-none" onclick="toggleSort('size')">
                        Size ${sortArrow('size')}
                    </th>
                    <th class="py-2 px-2 cursor-pointer hover:text-white select-none" onclick="toggleSort('savings')">
                        Savings ${sortArrow('savings')}
                    </th>
                    <th class="py-2 px-2 cursor-pointer hover:text-white select-none" onclick="toggleSort('status')">
                        Status ${sortArrow('status')}
                    </th>
                    <th class="py-2 px-2 cursor-pointer hover:text-white select-none min-w-[180px]" onclick="toggleSort('progress')">
                        Progress ${sortArrow('progress')}
                    </th>
                    <th class="py-2 px-2 cursor-pointer hover:text-white select-none" onclick="toggleSort('priority')">
                        Priority ${sortArrow('priority')}
                    </th>
                    <th class="py-2 px-2">Profile</th>
                    <th class="py-2 px-2">Actions</th>
                </tr>
            </thead>
            <tbody>
    `;

    items.forEach(item => {
        const fileName = item.file_path.split(/[/\\]/).pop();
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
            'paused':     { emoji: '⏸️', color: 'text-orange-400', barColor: 'bg-orange-500' }
        };
        const sc = statusConfig[item.status] || { emoji: '❓', color: 'text-gray-400', barColor: 'bg-gray-500' };
        const progress = item.progress || 0;

        // Savings column
        const savingsBytes = item.estimated_savings_bytes || 0;
        const savingsPctItem = item.file_size_bytes > 0 ? ((savingsBytes / item.file_size_bytes) * 100).toFixed(0) : 0;
        const savingsDisplay = savingsBytes > 0 
            ? `<span class="text-green-400">-${formatSize(savingsBytes)}</span> <span class="text-gray-500 text-xs">(${savingsPctItem}%)</span>`
            : '<span class="text-gray-500">-</span>';

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
                    <span class="text-red-400 font-mono text-xs w-14 text-right" title="${item.error_message || ''}">${progress > 0 ? progress.toFixed(1) + '%' : 'ERR'}</span>
                </div>`;
        } else if (item.status === 'processing' || (item.status === 'paused' && progress > 0)) {
            // Calculate elapsed and ETA from started_at
            let etaStr = '';
            if (item.started_at && item.status === 'processing' && progress > 0.5) {
                try {
                    const startMs  = new Date(item.started_at.replace(' ', 'T') + 'Z').getTime();
                    const elapsedS = Math.max(0, (Date.now() - startMs) / 1000);
                    const totalEstS = (elapsedS / (progress / 100));
                    const remainS   = Math.max(0, totalEstS - elapsedS);

                    const fmt = (s) => {
                        s = Math.round(s);
                        if (s < 60)   return s + 's';
                        if (s < 3600) return Math.floor(s/60) + 'm ' + (s%60) + 's';
                        return Math.floor(s/3600) + 'h ' + Math.floor((s%3600)/60) + 'm';
                    };
                    etaStr = `<div class="text-xs text-gray-500 mt-0.5">${fmt(elapsedS)} elapsed · ~${fmt(remainS)} left</div>`;
                } catch (_) {}
            }
            progressBar = `
                <div>
                    <div class="flex items-center gap-2">
                        <div class="flex-1 bg-gray-600 rounded-full h-2.5 overflow-hidden">
                            <div class="${sc.barColor} h-2.5 rounded-full transition-all duration-700" style="width: ${progress}%"></div>
                        </div>
                        <span class="${sc.color} font-mono text-xs font-bold w-14 text-right">${progress.toFixed(1)}%</span>
                    </div>
                    ${etaStr}
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
        const tooltip = `Path: ${item.file_path}\nCodec: ${codec}\nResolution: ${resolution}\nFPS: ${fps}\nBitrate: ${bitrate}`;

        html += `
            <tr class="border-b border-gray-700 hover:bg-gray-750 ${isSelected ? 'bg-gray-700' : ''}">
                <td class="py-2 px-1">
                    <input type="checkbox" class="queue-row-checkbox" data-id="${item.id}" 
                        ${isSelected ? 'checked' : ''}
                        onchange="toggleRowSelect(this, ${item.id})">
                </td>
                <td class="py-2 px-2 max-w-[220px] truncate" title="${tooltip}">
                    <span class="text-gray-100">${fileName}</span>${item.upscale_plan ? ' <span class="text-blue-400 text-xs" title="AI upscaling queued">🔼</span>' : ''}
                </td>
                <td class="py-2 px-2"><span class="${codecColor} text-xs font-medium">${codec.toUpperCase()}</span></td>
                <td class="py-2 px-2 text-xs text-gray-300">${resolution}${fps !== '-' ? ` <span class="text-gray-500">@ ${fps}fps</span>` : ''}</td>
                <td class="py-2 px-2 text-xs">${formatSize(item.file_size_bytes)}</td>
                <td class="py-2 px-2 text-xs">${savingsDisplay}</td>
                <td class="py-2 px-2"><span class="${sc.color} text-xs">${sc.emoji} ${item.status}</span></td>
                <td class="py-2 px-2">${progressBar}</td>
                <td class="py-2 px-2 text-xs text-gray-400">${item.priority}</td>
                <td class="py-2 px-2">
                    ${buildProfileSelect(item)}
                </td>
                <td class="py-2 px-2">
                    <button onclick="deleteQueueItem(${item.id})"
                        class="text-red-400 hover:text-red-300 text-xs">Delete</button>
                </td>
            </tr>
        `;
    });

    html += '</tbody></table>';
    container.innerHTML = html;
    
    // Restore selection state on select-all checkbox
    const selectAllBox = document.getElementById('selectAllCheckbox');
    if (selectAllBox) {
        const allBoxes = document.querySelectorAll('.queue-row-checkbox');
        selectAllBox.checked = selectedQueueIds.size === allBoxes.length && allBoxes.length > 0;
        selectAllBox.indeterminate = selectedQueueIds.size > 0 && selectedQueueIds.size < allBoxes.length;
    }
    updateBulkBar();
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
    
    // Set username
    const user = JSON.parse(localStorage.getItem('user') || '{}');
    document.getElementById('username').textContent = user.username || '';
    
    // Load initial data
    loadStats();
    loadResources();
    loadQueue();
    
    // Auto-refresh every 5 seconds
    setInterval(() => {
        loadStats();
        loadResources();
        if (!document.getElementById('content-queue').classList.contains('hidden')) {
            loadQueue();
        }
    }, 5000);
});

// Settings functions
async function loadSettings() {
    // Load account info
    loadAccountInfo();
    
    // Load resource settings
    const settings = await apiRequest('/settings/resources');
    if (settings) {
        document.getElementById('cpuThreshold').value = parseFloat(settings.resource_cpu_threshold || '90');
        document.getElementById('memoryThreshold').value = parseFloat(settings.resource_memory_threshold || '85');
        document.getElementById('gpuThreshold').value = parseFloat(settings.resource_gpu_threshold || '90');
        document.getElementById('niceLevel').value = parseInt(settings.resource_nice_level || '10');
        document.getElementById('enableThrottling').checked = (settings.resource_enable_throttling || 'true') === 'true';
    }
}

async function saveResourceSettings() {
    const settings = {
        'resource_cpu_threshold': document.getElementById('cpuThreshold').value,
        'resource_memory_threshold': document.getElementById('memoryThreshold').value,
        'resource_gpu_threshold': document.getElementById('gpuThreshold').value,
        'resource_nice_level': document.getElementById('niceLevel').value,
        'resource_enable_throttling': document.getElementById('enableThrottling').checked ? 'true' : 'false'
    };
    
    const result = await apiRequest('/settings/resources', {
        method: 'POST',
        body: JSON.stringify(settings)
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
            cpu: 70,
            memory: 70,
            gpu: 75,
            nice: 15
        },
        'balanced': {
            cpu: 85,
            memory: 80,
            gpu: 85,
            nice: 10
        },
        'aggressive': {
            cpu: 95,
            memory: 90,
            gpu: 95,
            nice: 5
        }
    };
    
    const config = presets[preset];
    if (config) {
        document.getElementById('cpuThreshold').value = config.cpu;
        document.getElementById('memoryThreshold').value = config.memory;
        document.getElementById('gpuThreshold').value = config.gpu;
        document.getElementById('niceLevel').value = config.nice;
    }
}

// Schedule management
let selectedDays = new Set([0, 1, 2, 3, 4, 5, 6]); // All days selected by default

async function loadSchedule() {
    const schedule = await apiRequest('/schedule');
    if (schedule && schedule.config) {
        const config = schedule.config;
        
        // Set enabled state
        document.getElementById('scheduleEnabled').checked = config.enabled;
        
        // Set days
        selectedDays = new Set(config.days_of_week.split(',').map(d => parseInt(d)));
        updateDayButtons();
        
        // Set times
        document.getElementById('startTime').value = config.start_time;
        document.getElementById('endTime').value = config.end_time;
        
        // Update status
        document.getElementById('scheduleStatus').textContent = config.enabled ? '✓ Enabled' : '✗ Disabled';
        document.getElementById('scheduleStatus').className = config.enabled ? 'ml-2 font-medium text-green-400' : 'ml-2 font-medium text-gray-400';
        
        document.getElementById('withinWindow').textContent = schedule.within_schedule ? '✓ Yes' : '✗ No';
        document.getElementById('withinWindow').className = schedule.within_schedule ? 'ml-2 font-medium text-green-400' : 'ml-2 font-medium text-gray-400';
        
        document.getElementById('manualOverride').textContent = schedule.manual_override ? '✓ Active' : '✗ Inactive';
        document.getElementById('manualOverride').className = schedule.manual_override ? 'ml-2 font-medium text-yellow-400' : 'ml-2 font-medium text-gray-400';
    }
}

function toggleDay(day) {
    if (selectedDays.has(day)) {
        selectedDays.delete(day);
    } else {
        selectedDays.add(day);
    }
    updateDayButtons();
}

function updateDayButtons() {
    for (let i = 0; i < 7; i++) {
        const btn = document.getElementById(`day-${i}`);
        if (selectedDays.has(i)) {
            btn.className = 'day-button day-button-active';
        } else {
            btn.className = 'day-button';
        }
    }
}

async function saveSchedule() {
    const config = {
        enabled: document.getElementById('scheduleEnabled').checked,
        days_of_week: Array.from(selectedDays).sort().join(','),
        start_time: document.getElementById('startTime').value,
        end_time: document.getElementById('endTime').value,
        timezone: 'UTC'
    };
    
    const result = await apiRequest('/schedule', {
        method: 'POST',
        body: JSON.stringify(config)
    });
    
    if (result) {
        const msgEl = document.getElementById('scheduleMessage');
        msgEl.textContent = '✓ Schedule saved successfully';
        msgEl.className = 'mt-4 p-3 bg-green-900/50 border border-green-700 rounded text-green-300';
        msgEl.classList.remove('hidden');
        
        setTimeout(() => msgEl.classList.add('hidden'), 3000);
        
        // Reload schedule to update status
        loadSchedule();
    }
}

// ============================================================
// PROFILES MANAGEMENT
// ============================================================

let currentProfileId = null;

async function loadProfiles() {
    const profiles = await apiRequest('/profiles');
    const container = document.getElementById('profilesList');

    // Always keep the map fresh so queue rows can use it
    if (profiles && profiles.length > 0) {
        _profilesMap = {};
        profiles.forEach(p => { _profilesMap[p.id] = p; });
    }

    if (!profiles || profiles.length === 0) {
        container.innerHTML = '<p class="text-gray-400">No profiles yet. Create one to get started!</p>';
        return;
    }
    
    container.innerHTML = profiles.map(p => `
        <div class="bg-gray-700 rounded-lg p-4 mb-3">
            <div class="flex justify-between items-start">
                <div class="flex-1">
                    <div class="flex items-center gap-2">
                        <h3 class="font-semibold text-lg">${p.name}</h3>
                        ${p.is_default ? '<span class="px-2 py-1 bg-blue-900 text-blue-300 text-xs rounded">DEFAULT</span>' : ''}
                    </div>
                    <div class="grid grid-cols-2 gap-2 mt-2 text-sm text-gray-300">
                        <div><span class="text-gray-400">Codec:</span> ${p.codec.toUpperCase()}</div>
                        <div><span class="text-gray-400">Encoder:</span> ${p.encoder}</div>
                        <div><span class="text-gray-400">Quality:</span> CRF ${p.quality}</div>
                        <div><span class="text-gray-400">Audio:</span> ${p.audio_codec.toUpperCase()}</div>
                        <div><span class="text-gray-400">Container:</span> ${(p.container || 'mkv').toUpperCase()}</div>
                        <div><span class="text-gray-400">Audio Mode:</span> ${getAudioLabel(p.audio_handling)}</div>
                        ${p.subtitle_handling && p.subtitle_handling !== 'none' ? `<div><span class="text-gray-400">Subtitles:</span> ${getSubtitleLabel(p.subtitle_handling)}</div>` : ''}
                        ${p.resolution ? `<div><span class="text-gray-400">Resolution:</span> ${p.resolution}</div>` : ''}
                        ${p.preset ? `<div><span class="text-gray-400">Preset:</span> ${p.preset}</div>` : ''}
                    </div>
                    <div class="flex gap-2 mt-2 flex-wrap">
                        ${p.enable_filters ? '<span class="px-2 py-0.5 bg-purple-900 text-purple-300 text-xs rounded">Filters</span>' : ''}
                        ${p.chapter_markers ? '<span class="px-2 py-0.5 bg-gray-600 text-gray-300 text-xs rounded">Chapters</span>' : ''}
                        ${p.hw_accel_enabled ? '<span class="px-2 py-0.5 bg-green-900 text-green-300 text-xs rounded">GPU</span>' : ''}
                        ${p.two_pass ? '<span class="px-2 py-0.5 bg-yellow-900 text-yellow-300 text-xs rounded">2-Pass</span>' : ''}
                        ${p.upscale_enabled ? `<span class="px-2 py-0.5 bg-blue-900 text-blue-300 text-xs rounded" title="AI Upscale: ${p.upscale_key || 'realesrgan'} ×${p.upscale_factor || 2}, trigger below ${p.upscale_trigger_below || 720}px">🤖 Upscale ×${p.upscale_factor || 2}</span>` : ''}
                    </div>
                </div>
                <div class="flex gap-2">
                    <button onclick="editProfile(${p.id})" 
                        class="bg-blue-600 hover:bg-blue-700 px-3 py-1 rounded text-sm">
                        Edit
                    </button>
                    <button onclick="deleteProfile(${p.id}, '${p.name}')" 
                        class="bg-red-600 hover:bg-red-700 px-3 py-1 rounded text-sm">
                        Delete
                    </button>
                </div>
            </div>
        </div>
    `).join('');
}

function showCreateProfileForm() {
    currentProfileId = null;
    document.getElementById('profileModalTitle').textContent = 'Create Profile';
    document.getElementById('profileForm').reset();
    document.getElementById('profileId').value = '';
    document.getElementById('profileFramerateCustom').classList.add('hidden');

    // Reset Phase 2 fields to defaults
    document.getElementById('profileAudioHandling').value = 'preserve_all';
    document.getElementById('profileSubtitleHandling').value = 'none';
    document.getElementById('profileEnableFilters').checked = false;
    document.getElementById('profileChapterMarkers').checked = true;
    document.getElementById('profileHwAccel').checked = false;
    document.getElementById('profileContainer').value = 'mkv';

    // Reset AI upscale section to defaults and collapse it
    _fillProfileUpscaleFields({
        upscale_enabled: false, upscale_trigger_below: 720, upscale_target_height: 1080,
        upscale_key: 'realesrgan', upscale_model: 'realesrgan-x4plus', upscale_factor: 2,
    });
    const upSec  = document.getElementById('profileUpscaleSection');
    const upIcon = document.getElementById('profileUpscaleToggleIcon');
    if (upSec && !upSec.classList.contains('hidden')) upSec.classList.add('hidden');
    if (upIcon) upIcon.textContent = '▶';

    // Initialize preset options for default encoder (svt_av1)
    updatePresetOptions();

    document.getElementById('profileModal').classList.remove('hidden');
}

async function editProfile(id) {
    currentProfileId = id;
    const profile = await apiRequest(`/profiles/${id}`);
    
    if (!profile) return;
    
    document.getElementById('profileModalTitle').textContent = 'Edit Profile';
    document.getElementById('profileId').value = profile.id;
    document.getElementById('profileName').value = profile.name;
    document.getElementById('profileCodec').value = profile.codec;
    document.getElementById('profileEncoder').value = profile.encoder;
    document.getElementById('profileResolution').value = profile.resolution || '';
    document.getElementById('profileContainer').value = profile.container || 'mkv';
    
    // Handle framerate - check if it's a standard value
    const standardFps = ['', '24', '30', '60'];
    const fpsValue = profile.framerate ? profile.framerate.toString() : '';
    if (standardFps.includes(fpsValue)) {
        document.getElementById('profileFramerate').value = fpsValue;
        document.getElementById('profileFramerateCustom').classList.add('hidden');
    } else if (fpsValue) {
        // Custom FPS
        document.getElementById('profileFramerate').value = 'custom';
        document.getElementById('profileFramerateCustom').value = fpsValue;
        document.getElementById('profileFramerateCustom').classList.remove('hidden');
    }
    
    document.getElementById('profileQuality').value = profile.quality;
    document.getElementById('profilePreset').value = profile.preset || '';
    document.getElementById('profileAudioCodec').value = profile.audio_codec;
    document.getElementById('profileAudioHandling').value = profile.audio_handling || 'preserve_all';
    document.getElementById('profileSubtitleHandling').value = profile.subtitle_handling || 'none';
    document.getElementById('profileEnableFilters').checked = profile.enable_filters || false;
    document.getElementById('profileChapterMarkers').checked = profile.chapter_markers !== false;
    document.getElementById('profileHwAccel').checked = profile.hw_accel_enabled || false;
    document.getElementById('profileTwoPass').checked = profile.two_pass;
    document.getElementById('profileIsDefault').checked = profile.is_default;
    document.getElementById('profileCustomArgs').value = profile.custom_args || '';

    // Populate AI upscale fields
    _fillProfileUpscaleFields(profile);

    // Update preset options based on encoder
    updatePresetOptions();

    document.getElementById('profileModal').classList.remove('hidden');
}

function closeProfileModal() {
    document.getElementById('profileModal').classList.add('hidden');
    currentProfileId = null;
}

document.getElementById('profileForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    
    // Handle framerate - check for custom input
    let framerate = document.getElementById('profileFramerate').value;
    if (framerate === 'custom') {
        framerate = document.getElementById('profileFramerateCustom').value;
    }
    
    const data = {
        name: document.getElementById('profileName').value,
        codec: document.getElementById('profileCodec').value,
        encoder: document.getElementById('profileEncoder').value,
        resolution: document.getElementById('profileResolution').value || null,
        framerate: parseInt(framerate) || null,
        quality: parseInt(document.getElementById('profileQuality').value),
        preset: document.getElementById('profilePreset').value || null,
        audio_codec: document.getElementById('profileAudioCodec').value,
        container: document.getElementById('profileContainer').value,
        audio_handling: document.getElementById('profileAudioHandling').value,
        subtitle_handling: document.getElementById('profileSubtitleHandling').value,
        enable_filters: document.getElementById('profileEnableFilters').checked,
        chapter_markers: document.getElementById('profileChapterMarkers').checked,
        hw_accel_enabled: document.getElementById('profileHwAccel').checked,
        two_pass: document.getElementById('profileTwoPass').checked,
        custom_args: document.getElementById('profileCustomArgs').value || null,
        is_default: document.getElementById('profileIsDefault').checked,
        // AI upscale settings
        upscale_enabled:       document.getElementById('profileUpscaleEnabled')?.checked ?? false,
        upscale_trigger_below: parseInt(document.getElementById('profileUpscaleTrigger')?.value) || 720,
        upscale_target_height: parseInt(document.getElementById('profileUpscaleTarget')?.value)  || 1080,
        upscale_key:           document.getElementById('profileUpscaleKey')?.value   || 'realesrgan',
        upscale_model:         document.getElementById('profileUpscaleModel')?.value || 'realesrgan-x4plus',
        upscale_factor:        parseInt(document.getElementById('profileUpscaleFactor')?.value)  || 2,
    };
    
    const method = currentProfileId ? 'PUT' : 'POST';
    const url = currentProfileId ? `/profiles/${currentProfileId}` : '/profiles';
    
    const result = await apiRequest(url, {
        method: method,
        body: JSON.stringify(data)
    });
    
    if (result) {
        closeProfileModal();
        loadProfiles();
        showMessage('Profile saved successfully!', 'success');
    }
});

async function deleteProfile(id, name) {
    if (!confirm(`Delete profile "${name}"?`)) return;
    
    const result = await apiRequest(`/profiles/${id}`, { method: 'DELETE' });
    
    if (result) {
        loadProfiles();
        showMessage('Profile deleted successfully!', 'success');
    }
}

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
        <div class="bg-gray-700 rounded-lg p-4 mb-3">
            <div class="flex justify-between items-start">
                <div class="flex-1">
                    <h3 class="font-semibold text-lg">${r.path}</h3>
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
                    </div>
                </div>
                <div class="flex gap-2">
                    <button onclick="scanSingleRoot(${r.id})" 
                        class="bg-green-600 hover:bg-green-700 px-3 py-1 rounded text-sm">
                        Scan Now
                    </button>
                    <button onclick="editScanRoot(${r.id})" 
                        class="bg-blue-600 hover:bg-blue-700 px-3 py-1 rounded text-sm">
                        Edit
                    </button>
                    <button onclick="deleteScanRoot(${r.id}, '${r.path}')" 
                        class="bg-red-600 hover:bg-red-700 px-3 py-1 rounded text-sm">
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
    // Create a toast notification
    const toast = document.createElement('div');
    toast.className = `fixed top-4 right-4 px-6 py-3 rounded shadow-lg z-50 ${
        type === 'success' ? 'bg-green-900 border border-green-700 text-green-300' :
        type === 'error' ? 'bg-red-900 border border-red-700 text-red-300' :
        'bg-blue-900 border border-blue-700 text-blue-300'
    }`;
    toast.textContent = text;
    document.body.appendChild(toast);
    
    setTimeout(() => toast.remove(), 3000);
}
// ============================================================
// PROFILE UI IMPROVEMENTS
// ============================================================

// Preset options for different encoders
const PRESET_OPTIONS = {
    'x264': [
        { value: '', label: 'Auto (recommended)' },
        { value: 'ultrafast', label: 'ultrafast (fastest, lower quality)' },
        { value: 'superfast', label: 'superfast' },
        { value: 'veryfast', label: 'veryfast' },
        { value: 'faster', label: 'faster' },
        { value: 'fast', label: 'fast' },
        { value: 'medium', label: 'medium (balanced) ⭐' },
        { value: 'slow', label: 'slow' },
        { value: 'slower', label: 'slower' },
        { value: 'veryslow', label: 'veryslow (slowest, best quality)' }
    ],
    'x265': [
        { value: '', label: 'Auto (recommended)' },
        { value: 'ultrafast', label: 'ultrafast (fastest, lower quality)' },
        { value: 'superfast', label: 'superfast' },
        { value: 'veryfast', label: 'veryfast' },
        { value: 'faster', label: 'faster' },
        { value: 'fast', label: 'fast' },
        { value: 'medium', label: 'medium (balanced) ⭐' },
        { value: 'slow', label: 'slow' },
        { value: 'slower', label: 'slower' },
        { value: 'veryslow', label: 'veryslow (slowest, best quality)' }
    ],
    'svt_av1': [
        { value: '', label: 'Auto (recommended)' },
        { value: '0', label: '0 (slowest, best quality)' },
        { value: '1', label: '1' },
        { value: '2', label: '2' },
        { value: '3', label: '3' },
        { value: '4', label: '4' },
        { value: '5', label: '5' },
        { value: '6', label: '6 (balanced) ⭐' },
        { value: '7', label: '7' },
        { value: '8', label: '8 (faster)' },
        { value: '9', label: '9' },
        { value: '10', label: '10' },
        { value: '11', label: '11' },
        { value: '12', label: '12' },
        { value: '13', label: '13 (fastest, lower quality)' }
    ],
    'nvenc_h264': [
        { value: '', label: 'Auto (recommended)' },
        { value: 'p1', label: 'p1 (fastest)' },
        { value: 'p2', label: 'p2' },
        { value: 'p3', label: 'p3' },
        { value: 'p4', label: 'p4 (balanced) ⭐' },
        { value: 'p5', label: 'p5' },
        { value: 'p6', label: 'p6' },
        { value: 'p7', label: 'p7 (slowest, best quality)' }
    ],
    'nvenc_h265': [
        { value: '', label: 'Auto (recommended)' },
        { value: 'p1', label: 'p1 (fastest)' },
        { value: 'p2', label: 'p2' },
        { value: 'p3', label: 'p3' },
        { value: 'p4', label: 'p4 (balanced) ⭐' },
        { value: 'p5', label: 'p5' },
        { value: 'p6', label: 'p6' },
        { value: 'p7', label: 'p7 (slowest, best quality)' }
    ],
    'nvenc_av1': [
        { value: '', label: 'Auto (recommended)' },
        { value: 'p1', label: 'p1 (fastest)' },
        { value: 'p2', label: 'p2' },
        { value: 'p3', label: 'p3' },
        { value: 'p4', label: 'p4 (balanced) ⭐' },
        { value: 'p5', label: 'p5' },
        { value: 'p6', label: 'p6' },
        { value: 'p7', label: 'p7 (slowest, best quality)' }
    ],
    'qsv_h264': [
        { value: '', label: 'Auto (recommended)' },
        { value: 'veryfast', label: 'veryfast (fastest)' },
        { value: 'faster', label: 'faster' },
        { value: 'fast', label: 'fast' },
        { value: 'medium', label: 'medium (balanced) ⭐' },
        { value: 'slow', label: 'slow' },
        { value: 'slower', label: 'slower' },
        { value: 'veryslow', label: 'veryslow (slowest, best quality)' }
    ],
    'qsv_h265': [
        { value: '', label: 'Auto (recommended)' },
        { value: 'veryfast', label: 'veryfast (fastest)' },
        { value: 'faster', label: 'faster' },
        { value: 'fast', label: 'fast' },
        { value: 'medium', label: 'medium (balanced) ⭐' },
        { value: 'slow', label: 'slow' },
        { value: 'slower', label: 'slower' },
        { value: 'veryslow', label: 'veryslow (slowest, best quality)' }
    ]
};

// Update preset dropdown when encoder changes
function updatePresetOptions() {
    const encoder = document.getElementById('profileEncoder').value;
    const presetSelect = document.getElementById('profilePreset');
    const presetHelp = document.getElementById('presetHelp');
    
    const options = PRESET_OPTIONS[encoder] || [{ value: '', label: 'Auto (recommended)' }];
    
    // Save current value
    const currentValue = presetSelect.value;
    
    // Clear and repopulate
    presetSelect.innerHTML = options.map(opt => 
        `<option value="${opt.value}">${opt.label}</option>`
    ).join('');
    
    // Restore value if it exists in new options
    if (options.find(opt => opt.value === currentValue)) {
        presetSelect.value = currentValue;
    }
    
    // Update help text
    if (encoder.startsWith('nvenc')) {
        presetHelp.textContent = 'NVENC: p1=fastest, p7=slowest/best quality';
    } else if (encoder === 'svt_av1') {
        presetHelp.textContent = 'SVT-AV1: 0=slowest/best, 13=fastest';
    } else {
        presetHelp.textContent = 'Speed vs quality trade-off';
    }
}

// Handle framerate custom input
function handleFramerateChange() {
    const fps = document.getElementById('profileFramerate').value;
    const customInput = document.getElementById('profileFramerateCustom');
    
    if (fps === 'custom') {
        customInput.classList.remove('hidden');
        customInput.focus();
    } else {
        customInput.classList.add('hidden');
    }
}

// Show codec guide modal
function showCodecGuide() {
    const guide = `
╔══════════════════════════════════════════════════════════════╗
║                    VIDEO CODEC GUIDE                         ║
╚══════════════════════════════════════════════════════════════╝

🎬 H.264 (AVC)
   • Universal compatibility - plays on everything
   • Largest file sizes
   • Fast encoding
   • Use for: Maximum compatibility, older devices

📦 H.265 (HEVC)  
   • 50% smaller than H.264 at same quality
   • Wide device support (2016+)
   • Moderate encoding speed
   • Use for: Balance of size and compatibility

⭐ AV1 (RECOMMENDED)
   • 70% smaller than H.264 at same quality
   • Best compression available
   • Newer devices (2020+), all modern browsers
   • Slower encoding
   • Royalty-free and open source
   • Use for: Maximum space savings, modern libraries

🌐 VP9
   • Google's codec, similar to H.265
   • 50-60% smaller than H.264
   • Great for web streaming (YouTube)
   • Use for: YouTube uploads, web content

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 FILE SIZE COMPARISON (2-hour 1080p movie):

   H.264:  ~8 GB   ■■■■■■■■■■■■■■■■ 100%
   H.265:  ~4 GB   ■■■■■■■■         50%  
   AV1:    ~2.5 GB ■■■■■            30% ⭐
   VP9:    ~3.5 GB ■■■■■■■          44%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💡 RECOMMENDATION: Use AV1 for best compression!
`;
    
    alert(guide);
}

// Initialize encoder change listener
document.addEventListener('DOMContentLoaded', function() {
    const encoderSelect = document.getElementById('profileEncoder');
    if (encoderSelect) {
        encoderSelect.addEventListener('change', updatePresetOptions);
        // Initialize on load
        updatePresetOptions();
    }
});


// ============================================================
// FOLDER BROWSER (Server-Side Directory Browser)
// ============================================================

async function browseFolderPath() {
    // Open the folder browser modal
    document.getElementById('folderBrowserModal').classList.remove('hidden');
    await loadDirectoryListing('');
}

function closeFolderBrowser() {
    document.getElementById('folderBrowserModal').classList.add('hidden');
}

async function loadDirectoryListing(path) {
    const container = document.getElementById('folderBrowserList');
    container.innerHTML = '<p class="text-gray-400 p-4">Loading...</p>';

    // Update current path display
    document.getElementById('folderBrowserPath').textContent = path || 'Root';
    document.getElementById('folderBrowserPath').dataset.currentPath = path;

    try {
        const data = await apiRequest(`/browse?path=${encodeURIComponent(path)}`);
        if (!data) {
            container.innerHTML = '<p class="text-red-400 p-4">Failed to load directories</p>';
            return;
        }

        let html = '';

        // Parent directory link
        if (data.parent !== null && data.parent !== undefined) {
            html += `
                <div class="flex items-center gap-3 px-4 py-2 hover:bg-gray-600 cursor-pointer rounded"
                     onclick="loadDirectoryListing('${data.parent.replace(/\\/g, '\\\\')}')">
                    <span class="text-yellow-400">📁</span>
                    <span class="text-blue-400">..</span>
                </div>
            `;
        }

        if (data.dirs.length === 0 && data.parent === null) {
            html += '<p class="text-gray-400 p-4">No directories found</p>';
        }

        data.dirs.forEach(dir => {
            const escapedPath = dir.path.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
            html += `
                <div class="flex items-center gap-3 px-4 py-2 hover:bg-gray-600 cursor-pointer rounded group"
                     ondblclick="loadDirectoryListing('${escapedPath}')">
                    <span class="text-yellow-400">📁</span>
                    <span class="flex-1" onclick="loadDirectoryListing('${escapedPath}')">${dir.name}</span>
                    <button onclick="event.stopPropagation(); selectBrowsedFolder('${escapedPath}')"
                        class="hidden group-hover:block bg-blue-600 hover:bg-blue-700 px-2 py-1 rounded text-xs">
                        Select
                    </button>
                </div>
            `;
        });

        container.innerHTML = html;
    } catch (err) {
        container.innerHTML = `<p class="text-red-400 p-4">Error: ${err.message}</p>`;
    }
}

function selectCurrentFolder() {
    const currentPath = document.getElementById('folderBrowserPath').dataset.currentPath;
    if (currentPath) {
        selectBrowsedFolder(currentPath);
    }
}

function selectBrowsedFolder(path) {
    document.getElementById('scanRootPath').value = path;
    closeFolderBrowser();
    showMessage('Folder selected: ' + path, 'success');
}


// ============================================================
// ACCOUNT MANAGEMENT
// ============================================================

async function changePassword() {
    const current = document.getElementById('currentPassword').value;
    const newPass = document.getElementById('newPassword').value;
    const confirm = document.getElementById('confirmPassword').value;
    
    if (!current || !newPass || !confirm) {
        showMessage('Please fill in all password fields', 'error');
        return;
    }
    
    if (newPass !== confirm) {
        showMessage('New passwords do not match', 'error');
        return;
    }
    
    if (newPass.length < 6) {
        showMessage('Password must be at least 6 characters', 'error');
        return;
    }
    
    const result = await apiRequest('/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({
            current_password: current,
            new_password: newPass
        })
    });
    
    if (result) {
        showMessage('Password updated successfully!', 'success');
        document.getElementById('currentPassword').value = '';
        document.getElementById('newPassword').value = '';
        document.getElementById('confirmPassword').value = '';
    }
}

// Load account info when settings tab opens
function loadAccountInfo() {
    const username = localStorage.getItem('username') || 'admin';
    const isAdmin = localStorage.getItem('is_admin') === 'true';
    
    document.getElementById('accountUsername').value = username;
    document.getElementById('accountRole').value = isAdmin ? 'Administrator' : 'User';
}


// ============================================================
// QUEUE IMPROVEMENTS
// ============================================================

let queueRefreshInterval = null;

// Scan all enabled scan roots
async function scanAllRoots() {
    const btn = document.getElementById('scanAllBtn');
    const icon = document.getElementById('scanAllIcon');
    const text = document.getElementById('scanAllText');
    
    // Disable button and show loading
    btn.disabled = true;
    btn.classList.add('opacity-50', 'cursor-not-allowed');
    icon.textContent = '⏳';
    text.textContent = 'Scanning...';
    
    try {
        // Get all scan roots
        const roots = await apiRequest('/scan-roots');
        if (!roots || roots.length === 0) {
            showMessage('No scan roots configured', 'error');
            return;
        }
        
        // Scan each enabled root
        let totalFiles = 0;
        let scannedCount = 0;
        
        for (const root of roots) {
            if (root.enabled) {
                try {
                    const result = await apiRequest(`/scan-roots/${root.id}/scan`, {
                        method: 'POST'
                    });
                    // Extract number from message like "Found 5 video file(s)"
                    const match = result.message.match(/(\d+)/);
                    if (match) {
                        totalFiles += parseInt(match[1]);
                    }
                    scannedCount++;
                } catch (error) {
                    console.error(`Failed to scan ${root.path}:`, error);
                }
            }
        }
        
        showMessage(`Scanned ${scannedCount} root(s), found ${totalFiles} video file(s)!`, 'success');
        
        // Refresh queue to show new items
        loadQueue();
        
    } catch (error) {
        showMessage('Scan failed: ' + (error.message || 'Unknown error'), 'error');
    } finally {
        // Re-enable button
        btn.disabled = false;
        btn.classList.remove('opacity-50', 'cursor-not-allowed');
        icon.textContent = '🔍';
        text.textContent = 'Scan All';
    }
}

// Delete queue item
async function deleteQueueItem(id) {
    if (!confirm('Delete this item from the queue?')) return;
    
    const result = await apiRequest(`/queue/${id}`, {
        method: 'DELETE'
    });
    
    if (result) {
        selectedQueueIds.delete(id);
        showMessage('Item deleted from queue', 'success');
        loadQueue();
    }
}

// Toggle auto-refresh
function toggleAutoRefresh() {
    const enabled = document.getElementById('autoRefreshQueue').checked;
    
    if (enabled) {
        queueRefreshInterval = setInterval(() => {
            loadQueue();
        }, 5000);
    } else {
        if (queueRefreshInterval) {
            clearInterval(queueRefreshInterval);
            queueRefreshInterval = null;
        }
    }
}

// Initialize auto-refresh on page load
if (document.getElementById('autoRefreshQueue').checked) {
    toggleAutoRefresh();
}


// ============================================================
// LIBRARY TYPE HELPERS
// ============================================================

const LIBRARY_TYPE_LABELS = {
    'movie': '🎬 Movies',
    'tv_show': '📺 TV Shows',
    'anime': '🎌 Anime',
    'home_video': '🎥 Home Videos',
    '4k_content': '🖥️ 4K/UHD',
    'web_content': '🌐 Web/YouTube',
    'archive': '📦 Archive',
    'music_video': '🎵 Music Videos',
    'custom': '⚙️ Custom'
};

function getLibraryTypeLabel(type) {
    return LIBRARY_TYPE_LABELS[type] || LIBRARY_TYPE_LABELS['custom'];
}

// Library type change handler — show recommended settings
async function handleLibraryTypeChange() {
    const type = document.getElementById('scanRootLibraryType').value;
    const recBox = document.getElementById('libraryTypeRecommendation');
    
    if (type === 'custom') {
        recBox.classList.add('hidden');
        return;
    }
    
    // Fetch library type definitions from API
    try {
        const types = await apiRequest('/library-types');
        if (!types || !types[type] || !types[type].recommended) {
            recBox.classList.add('hidden');
            return;
        }
        
        const rec = types[type].recommended;
        recBox.innerHTML = `
            <div class="text-blue-300 font-medium mb-1">💡 Recommended Settings for ${types[type].name}:</div>
            <div class="grid grid-cols-3 gap-2 text-xs text-gray-300">
                <span>Codec: <strong>${rec.codec.toUpperCase()}</strong></span>
                <span>Encoder: <strong>${rec.encoder}</strong></span>
                <span>Quality: <strong>CRF ${rec.quality}</strong></span>
                <span>Preset: <strong>${rec.preset}</strong></span>
                <span>Container: <strong>${rec.container.toUpperCase()}</strong></span>
                <span>Audio: <strong>${rec.audio_codec.toUpperCase()}</strong></span>
            </div>
            <p class="text-xs text-gray-400 mt-1">Tip: Create a matching profile with these settings for best results.</p>
        `;
        recBox.classList.remove('hidden');
    } catch (err) {
        recBox.classList.add('hidden');
    }
}


// ============================================================
// LOGS TAB
// ============================================================

async function loadLogs() {
    const logType = document.getElementById('logType').value;
    const logLevel = document.getElementById('logLevel').value;
    const logLines = document.getElementById('logLines').value;
    const output = document.getElementById('logOutput');
    const stats = document.getElementById('logStats');
    
    output.innerHTML = '<p class="text-gray-500">Loading...</p>';
    
    try {
        const data = await apiRequest(`/logs?log_type=${logType}&lines=${logLines}&level=${logLevel}`);
        
        if (!data || !data.logs || data.logs.length === 0) {
            output.innerHTML = '<p class="text-gray-500">No log entries found.</p>';
            stats.textContent = `${logType} log — 0 entries`;
            return;
        }
        
        stats.textContent = `Showing ${data.showing} of ${data.total} entries (${logType} log)`;
        
        // Color-code log lines
        const coloredLines = data.logs.map(line => {
            if (line.includes('] ERROR [')) {
                return `<div class="text-red-400">${escapeHtml(line)}</div>`;
            } else if (line.includes('] WARNING [')) {
                return `<div class="text-yellow-400">${escapeHtml(line)}</div>`;
            } else if (line.includes('] DEBUG [')) {
                return `<div class="text-gray-500">${escapeHtml(line)}</div>`;
            } else {
                return `<div class="text-gray-300">${escapeHtml(line)}</div>`;
            }
        });
        
        output.innerHTML = coloredLines.join('');
        
        // Auto-scroll to bottom
        output.scrollTop = output.scrollHeight;
        
    } catch (err) {
        output.innerHTML = `<p class="text-red-400">Error loading logs: ${err.message}</p>`;
    }
}

async function clearLogs() {
    const logType = document.getElementById('logType').value;
    if (!confirm(`Clear all ${logType} logs? This cannot be undone.`)) return;
    
    const result = await apiRequest(`/logs/clear?log_type=${logType}`, { method: 'POST' });
    if (result) {
        showMessage(`${logType} logs cleared`, 'success');
        loadLogs();
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}


// ============================================================
// PHASE 2: AUDIO/SUBTITLE/HW HELPERS
// ============================================================

const AUDIO_LABELS = {
    'preserve_all': '🔊 Preserve All',
    'keep_primary': '🔈 Primary Only',
    'stereo_mixdown': '🎧 Stereo',
    'hd_plus_aac': '🎭 HD+AAC',
    'high_quality': '🎵 HQ Audio'
};

const SUBTITLE_LABELS = {
    'preserve_all': '💬 All Subs',
    'keep_english': '🇺🇸 English',
    'burn_in': '🔥 Burn-in',
    'foreign_scan': '🌍 Foreign Scan',
    'none': '❌ None'
};

const AUDIO_HELP = {
    'preserve_all': 'Keeps all original audio tracks',
    'keep_primary': 'Only the first/default track — saves space',
    'stereo_mixdown': 'Downmix to stereo AAC — mobile friendly',
    'hd_plus_aac': 'Keeps HD surround + adds AAC for compatibility',
    'high_quality': 'Single 256kbps AAC — best for music'
};

const SUBTITLE_HELP = {
    'preserve_all': 'Keeps every subtitle track from source',
    'keep_english': 'Only English subtitle tracks',
    'burn_in': 'Burns first subtitle into video permanently',
    'foreign_scan': 'Auto-subtitles foreign language parts only',
    'none': 'No subtitles in output'
};

function getAudioLabel(strategy) {
    return AUDIO_LABELS[strategy] || AUDIO_LABELS['preserve_all'];
}

function getSubtitleLabel(strategy) {
    return SUBTITLE_LABELS[strategy] || SUBTITLE_LABELS['none'];
}

// Update help text when audio/subtitle strategy changes
document.addEventListener('DOMContentLoaded', function() {
    const audioSelect = document.getElementById('profileAudioHandling');
    if (audioSelect) {
        audioSelect.addEventListener('change', function() {
            const help = document.getElementById('audioHandlingHelp');
            if (help) help.textContent = AUDIO_HELP[this.value] || '';
        });
    }
    
    const subSelect = document.getElementById('profileSubtitleHandling');
    if (subSelect) {
        subSelect.addEventListener('change', function() {
            const help = document.getElementById('subtitleHandlingHelp');
            if (help) help.textContent = SUBTITLE_HELP[this.value] || '';
        });
    }
});

// Hardware acceleration check
async function checkHwAccel() {
    const checkbox = document.getElementById('profileHwAccel');
    const helpEl = document.getElementById('hwAccelHelp');
    
    if (!checkbox.checked) {
        helpEl.textContent = 'Use GPU encoding';
        return;
    }
    
    helpEl.textContent = 'Detecting...';
    
    try {
        const hw = await apiRequest('/hardware-detect');
        
        if (!hw || hw.error) {
            helpEl.textContent = '⚠️ ' + (hw?.error || 'Detection failed');
            helpEl.className = 'text-xs text-red-400 mt-1';
            checkbox.checked = false;
            return;
        }
        
        if (hw.encoders && hw.encoders.length > 0) {
            helpEl.textContent = '✓ ' + hw.encoders.join(', ');
            helpEl.className = 'text-xs text-green-400 mt-1';
        } else {
            helpEl.textContent = '⚠️ No hardware encoders found';
            helpEl.className = 'text-xs text-yellow-400 mt-1';
            checkbox.checked = false;
        }
    } catch (err) {
        helpEl.textContent = '⚠️ Detection failed';
        helpEl.className = 'text-xs text-red-400 mt-1';
        checkbox.checked = false;
    }
}


// ============================================================
// FOLDER WATCHES
// ============================================================

async function loadWatches() {
    const list = document.getElementById('watchesList');
    const statusEl = document.getElementById('watcherStatus');
    
    // Load watcher status
    try {
        const status = await apiRequest('/watches/status');
        if (status) {
            statusEl.innerHTML = `
                <span class="${status.running ? 'text-green-400' : 'text-yellow-400'}">
                    ${status.running ? '● Running' : '○ Stopped'}
                </span>
                <span class="mx-3 text-gray-500">|</span>
                Poll interval: ${status.poll_interval}s
                <span class="mx-3 text-gray-500">|</span>
                Active watches: ${status.active_watches}/${status.total_watches}
            `;
        }
    } catch (e) {
        statusEl.innerHTML = '<span class="text-gray-500">Status unavailable</span>';
    }
    
    // Load watches
    const watches = await apiRequest('/watches');
    if (!watches || watches.length === 0) {
        list.innerHTML = '<p class="text-gray-400">No folder watches configured. Click "Add Watch" to start monitoring a folder.</p>';
        return;
    }
    
    list.innerHTML = watches.map(w => `
        <div class="bg-gray-700 rounded-lg p-4 mb-3">
            <div class="flex justify-between items-start">
                <div class="flex-1">
                    <div class="flex items-center gap-2">
                        <span class="text-lg">${w.enabled ? '👁️' : '⏸️'}</span>
                        <h3 class="font-semibold">${w.path}</h3>
                        ${w.enabled ? '<span class="px-2 py-0.5 bg-green-900 text-green-300 text-xs rounded">Active</span>' : '<span class="px-2 py-0.5 bg-gray-600 text-gray-400 text-xs rounded">Disabled</span>'}
                    </div>
                    <div class="flex gap-4 mt-2 text-sm text-gray-300">
                        <div><span class="text-gray-400">Profile:</span> ${w.profile_name || 'Unknown'}</div>
                        <div><span class="text-gray-400">Recursive:</span> ${w.recursive ? 'Yes' : 'No'}</div>
                        <div><span class="text-gray-400">Auto Queue:</span> ${w.auto_queue ? 'Yes' : 'No'}</div>
                        ${w.last_check ? `<div><span class="text-gray-400">Last Check:</span> ${new Date(w.last_check).toLocaleString()}</div>` : ''}
                    </div>
                    <div class="mt-1 text-xs text-gray-500">Extensions: ${w.extensions}</div>
                </div>
                <div class="flex gap-2">
                    <button onclick='editWatch(${JSON.stringify(w)})' class="bg-gray-600 hover:bg-gray-500 px-3 py-1 rounded text-sm">Edit</button>
                    <button onclick="deleteWatch(${w.id})" class="bg-red-600 hover:bg-red-700 px-3 py-1 rounded text-sm">Delete</button>
                </div>
            </div>
        </div>
    `).join('');
}

async function showCreateWatchForm() {
    document.getElementById('watchModalTitle').textContent = 'Add Folder Watch';
    document.getElementById('watchForm').reset();
    document.getElementById('watchId').value = '';
    document.getElementById('watchEnabled').checked = true;
    document.getElementById('watchRecursive').checked = true;
    document.getElementById('watchAutoQueue').checked = true;
    document.getElementById('watchExtensions').value = '.mkv,.mp4,.avi,.mov,.wmv,.flv,.webm,.m4v,.ts,.mpg,.mpeg';
    
    // Populate profile dropdown
    const profiles = await apiRequest('/profiles');
    const select = document.getElementById('watchProfile');
    select.innerHTML = profiles.map(p => `<option value="${p.id}">${p.name}</option>`).join('');
    
    document.getElementById('watchModal').classList.remove('hidden');
}

function editWatch(watch) {
    document.getElementById('watchModalTitle').textContent = 'Edit Folder Watch';
    document.getElementById('watchId').value = watch.id;
    document.getElementById('watchPath').value = watch.path;
    document.getElementById('watchEnabled').checked = watch.enabled;
    document.getElementById('watchRecursive').checked = watch.recursive;
    document.getElementById('watchAutoQueue').checked = watch.auto_queue;
    document.getElementById('watchExtensions').value = watch.extensions || '';
    
    // Populate and select profile
    showCreateWatchForm().then(() => {
        document.getElementById('watchProfile').value = watch.profile_id;
        document.getElementById('watchModalTitle').textContent = 'Edit Folder Watch';
        document.getElementById('watchId').value = watch.id;
        document.getElementById('watchPath').value = watch.path;
        document.getElementById('watchEnabled').checked = watch.enabled;
        document.getElementById('watchRecursive').checked = watch.recursive;
        document.getElementById('watchAutoQueue').checked = watch.auto_queue;
        document.getElementById('watchExtensions').value = watch.extensions || '';
    });
}

async function saveWatch(event) {
    event.preventDefault();
    
    const data = {
        path: document.getElementById('watchPath').value,
        profile_id: parseInt(document.getElementById('watchProfile').value),
        enabled: document.getElementById('watchEnabled').checked,
        recursive: document.getElementById('watchRecursive').checked,
        auto_queue: document.getElementById('watchAutoQueue').checked,
        extensions: document.getElementById('watchExtensions').value
    };
    
    const watchId = document.getElementById('watchId').value;
    
    if (watchId) {
        await apiRequest(`/watches/${watchId}`, { method: 'PUT', body: JSON.stringify(data) });
        showMessage('Watch updated', 'success');
    } else {
        await apiRequest('/watches', { method: 'POST', body: JSON.stringify(data) });
        showMessage('Watch created', 'success');
    }
    
    closeWatchModal();
    loadWatches();
}

async function deleteWatch(id) {
    if (!confirm('Delete this folder watch?')) return;
    await apiRequest(`/watches/${id}`, { method: 'DELETE' });
    showMessage('Watch deleted', 'success');
    loadWatches();
}

function closeWatchModal() {
    document.getElementById('watchModal').classList.add('hidden');
}

async function forceWatchCheck() {
    const result = await apiRequest('/watches/check', { method: 'POST' });
    if (result) {
        showMessage(`Checked ${result.checked} watch(es), found ${result.new_files} new file(s)`, 'success');
        loadWatches();
    }
}


// ============================================================
// STATISTICS DASHBOARD
// ============================================================

async function loadStatistics() {
    const days = document.getElementById('statsDays').value;

    try {
        const data = await apiRequest(`/stats/dashboard?days=${days}`);
        if (!data) return;

        const t = data.totals;
        document.getElementById('statTotalFiles').textContent  = (t.total || 0).toLocaleString();
        document.getElementById('statOriginalSize').textContent = formatSize(t.total_original || 0);
        document.getElementById('statNewSize').textContent      = formatSize(t.total_new || 0);
        document.getElementById('statSaved').textContent        = formatSize(t.total_saved || 0);
        document.getElementById('statAvgPct').textContent       = (t.avg_savings_pct || 0).toFixed(1) + '%';

        // Avg encode time
        const avgSec = t.avg_encode_seconds || 0;
        const avgTimeEl = document.getElementById('statAvgTime');
        if (avgTimeEl) {
            if (avgSec === 0) {
                avgTimeEl.textContent = '—';
            } else if (avgSec < 60) {
                avgTimeEl.textContent = Math.round(avgSec) + 's';
            } else if (avgSec < 3600) {
                avgTimeEl.textContent = Math.round(avgSec / 60) + 'm';
            } else {
                avgTimeEl.textContent = (avgSec / 3600).toFixed(1) + 'h';
            }
        }

        renderDailyChart(data.daily);
        renderCodecBreakdown(data.codecs, t.total);
        renderRecentHistory(data.recent);

    } catch (err) {
        console.error('Failed to load statistics:', err);
    }

    loadHealth();
    loadUpscalers();
}

function renderDailyChart(daily) {
    const container = document.getElementById('dailyChart');
    
    if (!daily || daily.length === 0) {
        container.innerHTML = '<p class="text-gray-500 text-sm">No encoding history yet. Process some files to see activity.</p>';
        return;
    }
    
    const maxFiles = Math.max(...daily.map(d => d.files), 1);
    
    container.innerHTML = daily.map(d => {
        const height = Math.max((d.files / maxFiles) * 100, 4);
        const date = new Date(d.date);
        const label = `${date.getMonth()+1}/${date.getDate()}`;
        const savedMB = (d.saved / (1024*1024)).toFixed(0);
        
        return `
            <div class="flex flex-col items-center flex-shrink-0" style="min-width: 30px" title="${d.date}: ${d.files} files, ${savedMB}MB saved">
                <div class="text-xs text-gray-400 mb-1">${d.files}</div>
                <div class="w-6 bg-blue-500 rounded-t transition-all" style="height: ${height}%"></div>
                <div class="text-xs text-gray-500 mt-1 whitespace-nowrap">${label}</div>
            </div>
        `;
    }).join('');
}

function renderCodecBreakdown(codecs, total) {
    const container = document.getElementById('codecBreakdown');
    
    if (!codecs || codecs.length === 0) {
        container.innerHTML = '<p class="text-gray-500 text-sm">No data yet</p>';
        return;
    }
    
    const colors = ['bg-blue-500', 'bg-green-500', 'bg-purple-500', 'bg-yellow-500', 'bg-red-500', 'bg-cyan-500'];
    
    container.innerHTML = codecs.map((c, i) => {
        const pct = total > 0 ? ((c.count / total) * 100).toFixed(1) : 0;
        const savedMB = (c.saved / (1024*1024)).toFixed(0);
        return `
            <div>
                <div class="flex justify-between text-sm mb-1">
                    <span>${(c.codec || 'unknown').toUpperCase()}</span>
                    <span class="text-gray-400">${c.count} files (${pct}%) — ${savedMB}MB saved</span>
                </div>
                <div class="w-full bg-gray-600 rounded h-2">
                    <div class="${colors[i % colors.length]} rounded h-2" style="width: ${pct}%"></div>
                </div>
            </div>
        `;
    }).join('');
}

function renderRecentHistory(recent) {
    const container = document.getElementById('recentHistory');

    if (!recent || recent.length === 0) {
        container.innerHTML = '<p class="text-gray-500 text-sm">No encoding history yet. Process some files to see activity.</p>';
        return;
    }

    container.innerHTML = recent.map(h => {
        const filename = h.file_path.split(/[/\\]/).pop();
        const savedPct = h.original_size_bytes > 0
            ? ((h.savings_bytes / h.original_size_bytes) * 100).toFixed(1)
            : '0.0';
        const savings_positive = parseFloat(savedPct) > 0;

        const secs = Math.round(h.encoding_time_seconds || 0);
        let timeStr;
        if (secs <= 0)       timeStr = '—';
        else if (secs < 60)  timeStr = `${secs}s`;
        else if (secs < 3600) timeStr = `${Math.floor(secs/60)}m ${secs%60}s`;
        else                  timeStr = `${Math.floor(secs/3600)}h ${Math.floor((secs%3600)/60)}m`;

        const codec     = (h.codec     || '—').toUpperCase();
        const container_fmt = (h.container || '—').toUpperCase();
        const dateStr   = h.completed_at ? h.completed_at.split('T')[0].replace(/-/g, '/').slice(2) : '—';

        return `
            <div class="p-3 bg-gray-700 rounded-lg hover:bg-gray-650 transition-colors">
                <div class="flex justify-between items-start gap-2">
                    <span class="text-sm text-gray-100 truncate flex-1" title="${_esc(h.file_path)}">${_esc(filename)}</span>
                    <span class="text-xs ${savings_positive ? 'text-green-400' : 'text-gray-400'} flex-shrink-0 font-medium">
                        ${savings_positive ? '↓' : ''}${savedPct}%
                    </span>
                </div>
                <div class="flex gap-3 mt-1 text-xs text-gray-500">
                    <span>${formatSize(h.original_size_bytes || 0)} → ${formatSize(h.new_size_bytes || 0)}</span>
                    <span class="text-gray-600">•</span>
                    <span>${codec}${container_fmt !== '—' ? ' / ' + container_fmt : ''}</span>
                    <span class="text-gray-600">•</span>
                    <span>⏱ ${timeStr}</span>
                    <span class="ml-auto">${dateStr}</span>
                </div>
            </div>
        `;
    }).join('');
}

async function loadHealth() {
    const container = document.getElementById('healthStatus');
    try {
        const h = await apiRequest('/health');
        if (!h) return;

        const ok  = (v) => `<span class="font-bold text-green-400">● ${v}</span>`;
        const err = (v) => `<span class="font-bold text-red-400">✗ ${v}</span>`;
        const warn= (v) => `<span class="font-bold text-yellow-400">⚠ ${v}</span>`;
        const sub = (v) => `<div class="text-xs text-gray-500 mt-0.5 truncate">${v}</div>`;

        const card = (title, body) => `
            <div class="p-4 bg-gray-700 rounded-lg">
                <div class="text-xs text-gray-400 font-medium uppercase tracking-wider mb-2">${title}</div>
                ${body}
            </div>`;

        // Service card
        const overall = h.status === 'ok' ? ok('Healthy') : h.status === 'warning' ? warn('Warning') : err('Degraded');
        let cards = card('Service', `${overall}${sub('v' + (h.version || '?'))}`);

        // HandBrakeCLI
        cards += card('HandBrakeCLI',
            (h.handbrake?.installed ? ok('Installed') : err('Not Found')) +
            sub(h.handbrake?.path || 'not in PATH'));

        // ffprobe
        cards += card('FFprobe',
            (h.ffprobe?.installed ? ok('Installed') : err('Not Found')) +
            sub(h.ffprobe?.path || 'not in PATH'));

        // ffmpeg
        cards += card('FFmpeg',
            (h.ffmpeg?.installed ? ok('Installed') : err('Not Found')) +
            sub(h.ffmpeg?.path || 'not in PATH'));

        // Database
        cards += card('Database',
            (h.database?.status === 'ok' ? ok('OK') : err('Error')) +
            sub(`${h.database?.profiles||0} profiles · ${h.database?.queue_items||0} queued · ${h.database?.history_records||0} history`));

        // Scheduler
        if (h.scheduler) {
            cards += card('Scheduler',
                (h.scheduler.enabled ? (h.scheduler.running ? ok('Active') : warn('Enabled, not running')) : '<span class="text-gray-400">Disabled</span>') +
                sub(h.scheduler.next_window || ''));
        }

        // Upscalers
        if (h.upscalers && Object.keys(h.upscalers).length > 0) {
            const upLines = Object.entries(h.upscalers).map(([k, v]) =>
                `<div class="flex justify-between text-xs mt-1"><span class="text-gray-300">${k}</span>${v.installed ? ok('✓') : '<span class="text-gray-500">—</span>'}</div>`
            ).join('');
            cards += card('AI Upscalers', upLines);
        }

        // Queue summary
        if (h.queue) {
            const q = h.queue;
            cards += card('Queue',
                `<div class="text-xs space-y-0.5">
                    ${q.processing ? `<div class="text-blue-400">⚙ Processing: ${q.processing}</div>` : ''}
                    <div class="text-gray-300">Pending: ${q.pending}</div>
                    <div class="text-green-400">Completed: ${q.completed}</div>
                    ${q.failed ? `<div class="text-red-400">Failed: ${q.failed}</div>` : ''}
                </div>`);
        }

        // Disk — global
        if (h.disk) {
            const pct = h.disk.percent_used;
            cards += card('App Disk',
                (pct > 90 ? err(`${h.disk.free_gb} GB free`) : ok(`${h.disk.free_gb} GB free`)) +
                sub(`${pct}% used of ${h.disk.total_gb} GB`));
        }

        // Disk per scan root
        if (h.scan_root_disk && h.scan_root_disk.length > 0) {
            const rootLines = h.scan_root_disk.map(d => {
                const name = d.path.split(/[/\\]/).pop() || d.path;
                if (d.status === 'missing') return `<div class="flex justify-between text-xs mt-1"><span class="text-gray-400 truncate">${_esc(name)}</span>${err('Missing')}</div>`;
                if (d.status === 'error')   return `<div class="flex justify-between text-xs mt-1"><span class="text-gray-400 truncate">${_esc(name)}</span>${err('Error')}</div>`;
                const free = d.free_gb;
                const pct  = d.percent_used;
                return `<div class="flex justify-between text-xs mt-1" title="${_esc(d.path)}">
                    <span class="text-gray-300 truncate max-w-[120px]">${_esc(name)}</span>
                    <span class="${pct > 90 ? 'text-red-400' : 'text-green-400'}">${free} GB free</span>
                </div>`;
            }).join('');
            cards += card('Library Disk', rootLines);
        }

        container.innerHTML = `<div class="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-4">${cards}</div>`;

    } catch (err) {
        container.innerHTML = '<p class="text-red-400 text-sm">Failed to load health status</p>';
    }
}

// ============================================================
// PRESET IMPORT / EXPORT
// ============================================================

async function exportProfiles() {
    try {
        const data = await apiRequest('/profiles/export');
        if (!data) return;
        
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `optimizarr-profiles-${new Date().toISOString().split('T')[0]}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        
        showMessage(`Exported ${data.count} profile(s)`, 'success');
    } catch (err) {
        showMessage('Export failed: ' + err.message, 'error');
    }
}

async function importProfiles(event) {
    const file = event.target.files[0];
    if (!file) return;
    
    try {
        const text = await file.text();
        const data = JSON.parse(text);
        
        if (!data.profiles || !Array.isArray(data.profiles)) {
            showMessage('Invalid file format — expected Optimizarr profile export', 'error');
            return;
        }
        
        const result = await apiRequest('/profiles/import', {
            method: 'POST',
            body: JSON.stringify(data)
        });
        
        if (result) {
            showMessage(result.message, 'success');
            loadProfiles();
        }
    } catch (err) {
        showMessage('Import failed: ' + err.message, 'error');
    }
    
    // Reset file input
    event.target.value = '';
}

async function downloadBackup() {
    try {
        // Use a direct link so the browser triggers a file download
        const token = localStorage.getItem('auth_token');
        const resp  = await fetch('/api/backup', {
            headers: token ? { 'Authorization': 'Bearer ' + token } : {}
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            showMessage('Backup failed: ' + (err.detail || resp.statusText), 'error');
            return;
        }
        const blob     = await resp.blob();
        const cd       = resp.headers.get('Content-Disposition') || '';
        const match    = cd.match(/filename="?([^"]+)"?/);
        const filename = match ? match[1] : `optimizarr_backup_${new Date().toISOString().split('T')[0]}.db`;
        const url = URL.createObjectURL(blob);
        const a   = document.createElement('a');
        a.href = url; a.download = filename;
        document.body.appendChild(a); a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        showMessage('Backup downloaded: ' + filename, 'success');
    } catch (err) {
        showMessage('Backup failed: ' + err.message, 'error');
    }
}

async function restoreBackup(event) {
    const file = event.target.files[0];
    if (!file) return;

    const fileNameEl  = document.getElementById('restoreFileName');
    const statusEl    = document.getElementById('restoreStatus');
    if (fileNameEl) fileNameEl.textContent = file.name;

    if (!confirm(`⚠️ This will OVERWRITE your current database with "${file.name}".\n\nYour current profiles, scan roots, history, and settings will be replaced.\n\nContinue?`)) {
        event.target.value = '';
        return;
    }

    if (statusEl) { statusEl.className = 'mt-3 text-xs text-yellow-400'; statusEl.textContent = '⏳ Uploading…'; }

    try {
        const token = localStorage.getItem('auth_token');
        const form  = new FormData();
        form.append('file', file);
        const resp = await fetch('/api/restore-upload', {
            method: 'POST',
            headers: token ? { 'Authorization': 'Bearer ' + token } : {},
            body: form,
        });
        const data = await resp.json().catch(() => ({}));
        if (resp.ok) {
            if (statusEl) { statusEl.className = 'mt-3 text-xs text-green-400'; statusEl.textContent = '✓ ' + (data.message || 'Restored successfully'); }
            showMessage('Database restored. Please restart Optimizarr.', 'success');
        } else {
            if (statusEl) { statusEl.className = 'mt-3 text-xs text-red-400'; statusEl.textContent = '✗ ' + (data.detail || 'Restore failed'); }
            showMessage('Restore failed: ' + (data.detail || 'Unknown error'), 'error');
        }
    } catch (err) {
        if (statusEl) { statusEl.className = 'mt-3 text-xs text-red-400'; statusEl.textContent = '✗ ' + err.message; }
        showMessage('Restore failed: ' + err.message, 'error');
    }

    event.target.value = '';
}

async function applyQueuePriority() {
    const sortBy = document.getElementById('queueSortBy').value;
    const sortMap = {
        'default':            { sort_by: 'default',            order: 'desc' },
        'file_size':          { sort_by: 'file_size',          order: 'desc' },
        'estimated_savings':  { sort_by: 'estimated_savings',  order: 'desc' },
        'filename':           { sort_by: 'filename',           order: 'asc'  },
    };
    const params = sortMap[sortBy] || sortMap['default'];

    // Show spinner on the button
    const btn = document.querySelector('button[onclick="applyQueuePriority()"]');
    const origText = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Applying…'; }

    try {
        const result = await apiRequest('/queue/prioritize', {
            method: 'POST',
            body: JSON.stringify(params)
        });
        if (result) {
            showMessage(`✓ ${result.message}`, 'success');
            loadQueue();
        }
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = origText; }
    }
}

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
    const defs = info.definitions || {};
    const det = (info.detection && info.detection.details) ? info.detection.details : {};

    container.innerHTML = Object.entries(defs).map(([key, up]) => {
        const d = det[key] || {};
        const installed = d.installed;
        const dl = d.download_state || {};
        const dlStatus = dl.status || 'idle';
        const dlProgress = dl.progress || 0;

        let actionBtn = '';
        if (dlStatus === 'downloading') {
            actionBtn = `
                <div class="mt-2">
                    <div class="flex justify-between text-xs text-gray-400 mb-1">
                        <span>${dl.message || 'Downloading…'}</span>
                        <span>${dlProgress}%</span>
                    </div>
                    <div class="bg-gray-600 rounded-full h-1.5">
                        <div class="bg-blue-500 h-1.5 rounded-full transition-all" style="width:${dlProgress}%"></div>
                    </div>
                </div>`;
        } else if (dlStatus === 'installed' || installed) {
            actionBtn = `
                <div class="mt-2 flex items-center gap-2">
                    <span class="text-xs text-green-400">✓ ${d.version || 'Installed'}</span>
                    ${d.path ? `<span class="text-xs text-gray-500 truncate max-w-32" title="${d.path}">${d.path}</span>` : ''}
                    <button onclick="startUpscalerDownload('${key}')" 
                        class="text-xs text-blue-400 hover:text-blue-300 ml-auto">Update</button>
                </div>`;
        } else if (dlStatus === 'error') {
            actionBtn = `
                <div class="mt-2">
                    <p class="text-xs text-red-400">${dl.error || 'Download failed'}</p>
                    <button onclick="startUpscalerDownload('${key}')"
                        class="mt-1 bg-blue-600 hover:bg-blue-700 px-3 py-1 rounded text-xs">Retry</button>
                </div>`;
        } else {
            actionBtn = `
                <div class="mt-2">
                    <button onclick="startUpscalerDownload('${key}')"
                        class="bg-blue-600 hover:bg-blue-700 px-3 py-1.5 rounded text-sm font-medium">
                        ⬇ Download
                    </button>
                    <a href="${up.download_url}" target="_blank"
                        class="ml-2 text-xs text-gray-400 hover:text-gray-300">Manual →</a>
                </div>`;
        }

        const statusBadge = installed || dlStatus === 'installed'
            ? '<span class="px-2 py-0.5 bg-green-900 text-green-300 text-xs rounded">Installed</span>'
            : '<span class="px-2 py-0.5 bg-gray-600 text-gray-400 text-xs rounded">Not Installed</span>';

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
                ${actionBtn}
            </div>`;
    }).join('');
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

// ============================================================
// WINDOWS REST HOURS (Schedule tab)
// ============================================================

async function loadWindowsActiveHours() {
    const avail = document.getElementById('windowsHoursAvailable');
    const info = document.getElementById('windowsHoursInfo');
    const toggle = document.getElementById('useWindowsRestHours');
    if (!avail) return;

    try {
        const data = await apiRequest('/schedule/windows-active-hours');
        if (!data || !data.available) {
            avail.textContent = '(not available on this OS)';
            if (toggle) toggle.disabled = true;
            return;
        }
        avail.textContent = `(detected: active ${data.active_start}:00–${data.active_end}:00)`;
        if (info) {
            info.textContent = `Encoding will run from ${data.rest_start_str} to ${data.rest_end_str} (outside active hours)`;
            info.classList.remove('hidden');
        }
    } catch (e) {
        avail.textContent = '(not available)';
        if (toggle) toggle.disabled = true;
    }
}

function toggleWindowsHours() {
    const useWin = document.getElementById('useWindowsRestHours');
    const manualDiv = document.getElementById('manualTimeWindow');
    if (!useWin || !manualDiv) return;
    if (useWin.checked) {
        manualDiv.style.opacity = '0.4';
        manualDiv.style.pointerEvents = 'none';
    } else {
        manualDiv.style.opacity = '1';
        manualDiv.style.pointerEvents = '';
    }
}

// Override saveSchedule to include new fields
const _originalSaveSchedule = typeof saveSchedule === 'function' ? saveSchedule : null;
async function saveSchedule() {
    const useWinEl = document.getElementById('useWindowsRestHours');
    const config = {
        enabled: document.getElementById('scheduleEnabled').checked,
        days_of_week: Array.from(selectedDays).sort().join(','),
        start_time: document.getElementById('startTime').value,
        end_time: document.getElementById('endTime').value,
        timezone: 'local',
        use_windows_rest_hours: useWinEl ? useWinEl.checked : false,
        max_concurrent_jobs: 1,
    };
    const result = await apiRequest('/schedule', {
        method: 'POST',
        body: JSON.stringify(config)
    });
    if (result) {
        const msgEl = document.getElementById('scheduleMessage');
        if (msgEl) {
            msgEl.textContent = '✓ Schedule saved successfully';
            msgEl.className = 'mt-4 p-3 bg-green-900/50 border border-green-700 rounded text-green-300';
            msgEl.classList.remove('hidden');
            setTimeout(() => msgEl.classList.add('hidden'), 3000);
        }
        loadSchedule();
    }
}

// Override loadSchedule to include Windows hours fields
const _origLoadSchedule = window._loadScheduleOriginal || null;
async function loadSchedule() {
    const schedule = await apiRequest('/schedule');
    if (!schedule) return;
    const config = schedule.config || {};

    const enabledEl = document.getElementById('scheduleEnabled');
    if (enabledEl) enabledEl.checked = config.enabled;

    if (typeof selectedDays !== 'undefined') {
        selectedDays = new Set((config.days_of_week || '0,1,2,3,4,5,6').split(',').map(Number));
        if (typeof updateDayButtons === 'function') updateDayButtons();
    }

    const startEl = document.getElementById('startTime');
    const endEl = document.getElementById('endTime');
    if (startEl) startEl.value = config.start_time || '22:00';
    if (endEl) endEl.value = config.end_time || '06:00';

    const useWinEl = document.getElementById('useWindowsRestHours');
    if (useWinEl) {
        useWinEl.checked = config.use_windows_rest_hours || false;
        toggleWindowsHours();
    }

    // Status indicators
    const statusEl = document.getElementById('scheduleStatus');
    if (statusEl) {
        statusEl.textContent = config.enabled ? '✓ Enabled' : '✗ Disabled';
        statusEl.className = config.enabled ? 'ml-2 font-medium text-green-400' : 'ml-2 font-medium text-gray-400';
    }
    const withinEl = document.getElementById('withinWindow');
    if (withinEl) {
        withinEl.textContent = schedule.within_schedule ? '✓ Yes' : '✗ No';
        withinEl.className = schedule.within_schedule ? 'ml-2 font-medium text-green-400' : 'ml-2 font-medium text-gray-400';
    }
    const overrideEl = document.getElementById('manualOverride');
    if (overrideEl) {
        overrideEl.textContent = schedule.manual_override ? '✓ Active' : '✗ Inactive';
        overrideEl.className = schedule.manual_override ? 'ml-2 font-medium text-yellow-400' : 'ml-2 font-medium text-gray-400';
    }

    // Load Windows hours info if on this tab
    loadWindowsActiveHours();
}

// ============================================================
// SHOW MESSAGE HELPER (if not already defined)
// ============================================================
if (typeof showMessage !== 'function') {
    function showMessage(msg, type = 'info') {
        console.log(`[${type.toUpperCase()}] ${msg}`);
    }
}


// ============================================================
// QUEUE RE-PROBE
// ============================================================

async function reprobeQueue() {
    const btn = document.getElementById('reprobeBtn');
    const icon = document.getElementById('reproBtnIcon');
    const text = document.getElementById('reproBtnText');
    if (btn) { btn.disabled = true; }
    if (icon) icon.textContent = '⏳';
    if (text) text.textContent = 'Probing…';

    try {
        const result = await apiRequest('/queue/reprobe', { method: 'POST' });
        if (result) {
            showMessage(`✓ ${result.message}`, 'success');
            loadQueue();
        }
    } finally {
        if (btn) { btn.disabled = false; }
        if (icon) icon.textContent = '🔬';
        if (text) text.textContent = 'Re-probe';
    }
}


// ============================================================
// CLEAR COMPLETED
// ============================================================

async function clearCompleted() {
    const result = await apiRequest('/queue/clear-completed', { method: 'POST' });
    if (result) {
        showMessage(`✓ ${result.message}`, 'success');
        loadQueue();
    }
}


// ============================================================
// SEED DEFAULT PROFILES
// ============================================================

async function seedDefaultProfiles() {
    const btn = document.querySelector('button[onclick="seedDefaultProfiles()"]');
    const origText = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Adding…'; }

    try {
        const result = await apiRequest('/profiles/seed-defaults', { method: 'POST' });
        if (result) {
            if (result.created && result.created.length > 0) {
                showMessage(`✓ ${result.message}`, 'success');
            } else {
                showMessage('All default profiles already exist', 'info');
            }
            loadProfiles();
        }
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = origText; }
    }
}


// ============================================================
// QUEUE — INLINE PROFILE CHANGE
// ============================================================

/**
 * Build a compact profile <select> for a queue row.
 * Shows the current profile name; changing it fires an immediate PATCH.
 * Disabled for processing/completed items.
 */
function buildProfileSelect(item) {
    const profiles = Object.values(_profilesMap);

    if (profiles.length === 0) {
        // Map not yet populated — show raw ID as fallback
        return `<span class="text-gray-500 text-xs">Profile #${item.profile_id || '?'}</span>`;
    }

    const isLocked = item.status === 'processing';
    const currentId = item.profile_id || '';

    // Find current profile name for the title tooltip
    const currentProfile = _profilesMap[currentId];
    const currentName = currentProfile
        ? currentProfile.name.replace(/^[^\w]*\s*/, '')   // strip leading emoji for title
        : `Profile #${currentId}`;

    const options = profiles
        .map(p => `<option value="${p.id}" ${p.id === currentId ? 'selected' : ''}>${p.name}</option>`)
        .join('');

    return `
        <select
            title="Profile: ${currentName}"
            onchange="changeQueueProfile(${item.id}, parseInt(this.value))"
            ${isLocked ? 'disabled' : ''}
            class="bg-gray-700 border border-gray-600 rounded px-1 py-0.5 text-xs
                   text-gray-200 max-w-[160px] truncate
                   ${isLocked ? 'opacity-50 cursor-not-allowed' : 'hover:border-blue-500 cursor-pointer'}">
            ${options}
        </select>
    `;
}

/**
 * PATCH the queue item with a new profile_id.
 * Updates _profilesMap lookup inline without a full queue reload.
 */
async function changeQueueProfile(itemId, profileId) {
    const result = await apiRequest(`/queue/${itemId}`, {
        method: 'PATCH',
        body: JSON.stringify({ profile_id: profileId })
    });

    if (result) {
        // Update local cache so re-render shows new value immediately
        const item = allQueueItems.find(i => i.id === itemId);
        if (item) item.profile_id = profileId;
        const name = _profilesMap[profileId]?.name || `Profile #${profileId}`;
        showMessage(`✓ Profile changed to "${name}"`, 'success');
    } else {
        // Revert visual state by re-rendering
        filterQueue();
    }
}


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
        const typeLabel = c.app_type === 'radarr' ? '📽️ Radarr' : '📺 Sonarr';
        const testedAgo = c.last_tested
            ? _timeAgo(c.last_tested)
            : 'Never tested';
        const syncedAgo = c.last_synced
            ? `Last sync: ${_timeAgo(c.last_synced)}`
            : 'Never synced';

        return `
        <div class="bg-gray-750 border border-gray-700 rounded-lg px-5 py-4 flex flex-col md:flex-row md:items-center gap-4"
             id="conn-card-${c.id}">

            <!-- Left: info -->
            <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2 mb-1">
                    ${statusDot}
                    <span class="font-semibold truncate">${_esc(c.name)}</span>
                    <span class="text-xs bg-gray-700 text-gray-300 px-2 py-0.5 rounded">${typeLabel}</span>
                </div>
                <p class="text-sm text-gray-400 truncate">${_esc(c.base_url)}</p>
                <p class="text-xs text-gray-500 mt-0.5">
                    Key: <code class="text-gray-400">${_esc(c.api_key_masked)}</code>
                    &nbsp;·&nbsp; ${testedAgo}
                    &nbsp;·&nbsp; ${syncedAgo}
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
    document.getElementById('connectionScanRoot').value = '';
    _hideConnectionTestResult();
    _populateConnectionScanRootDropdown();
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
    _hideConnectionTestResult();
    _populateConnectionScanRootDropdown(c.linked_scan_root_id);
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


// ============================================================
// AI UPSCALE SETTINGS — Scan Root Modal
// ============================================================

const _UPSCALER_MODELS = {
    realesrgan: ['realesrgan-x4plus', 'realesrgan-x4plus-anime', 'realesr-animevideov3'],
    realcugan:  ['models-se', 'models-pro', 'models-nose'],
    waifu2x:    ['models-cunet', 'models-upconv_7_anime_style_art_rgb'],
};

function toggleUpscaleSection() {
    const section = document.getElementById('upscaleSection');
    const icon    = document.getElementById('upscaleSectionToggleIcon');
    const hidden  = section.classList.toggle('hidden');
    icon.textContent = hidden ? '▶' : '▼';
}

function handleUpscaleToggle() {
    const enabled = document.getElementById('scanRootUpscaleEnabled').checked;
    const fields  = document.getElementById('upscaleSettingsFields');
    const badge   = document.getElementById('upscaleEnabledBadge');
    fields.classList.toggle('opacity-50',       !enabled);
    fields.classList.toggle('pointer-events-none', !enabled);
    badge.classList.toggle('hidden', !enabled);
}

function handleUpscalerKeyChange() {
    const key     = document.getElementById('scanRootUpscaleKey').value;
    const models  = _UPSCALER_MODELS[key] || [];
    const sel     = document.getElementById('scanRootUpscaleModel');
    sel.innerHTML = models.map((m, i) => `<option value="${m}" ${i === 0 ? 'selected' : ''}>${m}</option>`).join('');
}

// Call once on page load to set initial model list
handleUpscalerKeyChange();

// ── Patch openEditScanRootModal to populate upscale fields ─────────────────
const _origLoadScanRootForEdit = window.openEditScanRootModal;
function _fillUpscaleFields(root) {
    const ue = document.getElementById('scanRootUpscaleEnabled');
    if (!ue) return;
    ue.checked = !!root.upscale_enabled;

    const triggerEl = document.getElementById('scanRootUpscaleTrigger');
    const targetEl  = document.getElementById('scanRootUpscaleTarget');
    const keyEl     = document.getElementById('scanRootUpscaleKey');
    const factorEl  = document.getElementById('scanRootUpscaleFactor');

    if (triggerEl) triggerEl.value = root.upscale_trigger_below ?? 720;
    if (targetEl)  targetEl.value  = root.upscale_target_height  ?? 1080;
    if (keyEl)     keyEl.value     = root.upscale_key            ?? 'realesrgan';
    if (factorEl)  factorEl.value  = root.upscale_factor         ?? 2;

    // Populate model dropdown for the selected key then set saved model
    handleUpscalerKeyChange();
    const modelEl = document.getElementById('scanRootUpscaleModel');
    if (modelEl && root.upscale_model) modelEl.value = root.upscale_model;

    // Keep fields enabled/disabled in sync
    handleUpscaleToggle();
}

// ============================================================
// PROFILE UPSCALE FUNCTIONS
// ============================================================

function toggleProfileUpscaleSection() {
    const section = document.getElementById('profileUpscaleSection');
    const icon    = document.getElementById('profileUpscaleToggleIcon');
    if (!section) return;
    const hidden = section.classList.toggle('hidden');
    if (icon) icon.textContent = hidden ? '▶' : '▼';
}

function handleProfileUpscaleToggle() {
    const enabled = document.getElementById('profileUpscaleEnabled')?.checked;
    const fields  = document.getElementById('profileUpscaleFields');
    const badge   = document.getElementById('profileUpscaleEnabledBadge');
    if (!fields) return;
    fields.classList.toggle('opacity-50',          !enabled);
    fields.classList.toggle('pointer-events-none', !enabled);
    if (badge) badge.classList.toggle('hidden', !enabled);
}

function handleProfileUpscalerKeyChange() {
    const key    = document.getElementById('profileUpscaleKey')?.value;
    const models = _UPSCALER_MODELS[key] || [];
    const sel    = document.getElementById('profileUpscaleModel');
    if (!sel) return;
    sel.innerHTML = models.map((m, i) =>
        `<option value="${_esc(m)}" ${i === 0 ? 'selected' : ''}>${_esc(m)}</option>`
    ).join('');
}

function _fillProfileUpscaleFields(profile) {
    const ue = document.getElementById('profileUpscaleEnabled');
    if (!ue) return;

    ue.checked = !!profile.upscale_enabled;

    const triggerEl = document.getElementById('profileUpscaleTrigger');
    const targetEl  = document.getElementById('profileUpscaleTarget');
    const keyEl     = document.getElementById('profileUpscaleKey');
    const factorEl  = document.getElementById('profileUpscaleFactor');

    if (triggerEl) triggerEl.value = profile.upscale_trigger_below ?? 720;
    if (targetEl)  targetEl.value  = profile.upscale_target_height  ?? 1080;
    if (keyEl)     keyEl.value     = profile.upscale_key            ?? 'realesrgan';
    if (factorEl)  factorEl.value  = profile.upscale_factor         ?? 2;

    handleProfileUpscalerKeyChange();
    const modelEl = document.getElementById('profileUpscaleModel');
    if (modelEl && profile.upscale_model) modelEl.value = profile.upscale_model;

    handleProfileUpscaleToggle();
}
