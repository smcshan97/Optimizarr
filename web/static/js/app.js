// Main JavaScript for Optimizarr Dashboard

// API client
const API_BASE = '/api';

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
    if (tabName === 'settings') loadSettings();
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

// Load queue
async function loadQueue() {
    const items = await apiRequest('/queue');
    
    if (!items || items.length === 0) {
        document.getElementById('queueTable').innerHTML = '<p class="text-gray-400">No items in queue</p>';
        return;
    }
    
    let html = `
        <table class="w-full">
            <thead>
                <tr class="text-left border-b border-gray-700">
                    <th class="pb-3">File</th>
                    <th class="pb-3">Status</th>
                    <th class="pb-3">Progress</th>
                    <th class="pb-3">Size</th>
                    <th class="pb-3">Savings</th>
                    <th class="pb-3">Actions</th>
                </tr>
            </thead>
            <tbody>
    `;
    
    items.forEach(item => {
        const fileName = item.file_path.split('/').pop();
        const statusColor = {
            'pending': 'text-yellow-400',
            'processing': 'text-blue-400',
            'completed': 'text-green-400',
            'failed': 'text-red-400',
            'paused': 'text-orange-400'
        }[item.status] || 'text-gray-400';
        
        const sizeMB = (item.file_size_bytes / (1024 * 1024)).toFixed(1);
        const savingsMB = (item.estimated_savings_bytes / (1024 * 1024)).toFixed(1);
        
        html += `
            <tr class="border-b border-gray-700">
                <td class="py-3 text-sm">${fileName}</td>
                <td class="py-3"><span class="${statusColor}">${item.status}</span></td>
                <td class="py-3">
                    <div class="w-full bg-gray-700 rounded-full h-2">
                        <div class="bg-blue-600 h-2 rounded-full" style="width: ${item.progress}%"></div>
                    </div>
                    <span class="text-xs text-gray-400">${item.progress.toFixed(1)}%</span>
                </td>
                <td class="py-3 text-sm">${sizeMB} MB</td>
                <td class="py-3 text-sm text-green-400">-${savingsMB} MB</td>
                <td class="py-3">
                    <button onclick="deleteQueueItem(${item.id})" class="text-red-400 hover:text-red-300 text-sm">
                        Delete
                    </button>
                </td>
            </tr>
        `;
    });
    
    html += '</tbody></table>';
    document.getElementById('queueTable').innerHTML = html;
}

// Load profiles
async function loadProfiles() {
    const profiles = await apiRequest('/profiles');
    
    if (!profiles || profiles.length === 0) {
        document.getElementById('profilesList').innerHTML = '<p class="text-gray-400">No profiles found</p>';
        return;
    }
    
    let html = '<div class="grid gap-4">';
    
    profiles.forEach(profile => {
        html += `
            <div class="bg-gray-700 p-4 rounded">
                <div class="flex justify-between items-start">
                    <div>
                        <h3 class="font-bold text-lg">${profile.name}</h3>
                        <div class="mt-2 space-y-1 text-sm text-gray-300">
                            <p><span class="text-gray-400">Codec:</span> ${profile.codec} (${profile.encoder})</p>
                            <p><span class="text-gray-400">Quality:</span> CRF ${profile.quality}</p>
                            <p><span class="text-gray-400">Resolution:</span> ${profile.resolution || 'Source'}</p>
                            <p><span class="text-gray-400">Audio:</span> ${profile.audio_codec}</p>
                        </div>
                    </div>
                    <button onclick="deleteProfile(${profile.id})" class="text-red-400 hover:text-red-300">
                        Delete
                    </button>
                </div>
            </div>
        `;
    });
    
    html += '</div>';
    document.getElementById('profilesList').innerHTML = html;
}

// Load scan roots
async function loadScanRoots() {
    const roots = await apiRequest('/scan-roots');
    
    if (!roots || roots.length === 0) {
        document.getElementById('scanRootsList').innerHTML = '<p class="text-gray-400">No scan roots configured</p>';
        return;
    }
    
    let html = '<div class="grid gap-4">';
    
    roots.forEach(root => {
        const statusColor = root.enabled ? 'text-green-400' : 'text-gray-500';
        
        html += `
            <div class="bg-gray-700 p-4 rounded">
                <div class="flex justify-between items-start">
                    <div>
                        <h3 class="font-bold">${root.path}</h3>
                        <p class="text-sm text-gray-400 mt-1">
                            Profile ID: ${root.profile_id} | 
                            <span class="${statusColor}">${root.enabled ? 'Enabled' : 'Disabled'}</span> |
                            ${root.recursive ? 'Recursive' : 'Non-recursive'}
                        </p>
                    </div>
                    <button onclick="deleteScanRoot(${root.id})" class="text-red-400 hover:text-red-300">
                        Delete
                    </button>
                </div>
            </div>
        `;
    });
    
    html += '</div>';
    document.getElementById('scanRootsList').innerHTML = html;
}

// Actions
async function scanRoots() {
    const result = await apiRequest('/queue/scan', { method: 'POST' });
    if (result) {
        alert(result.message);
        loadQueue();
        loadStats();
    }
}

async function startEncoding() {
    const result = await apiRequest('/control/start', { method: 'POST' });
    if (result) {
        alert(result.message);
        setTimeout(() => {
            loadQueue();
            loadStats();
        }, 1000);
    }
}

async function stopEncoding() {
    const result = await apiRequest('/control/stop', { method: 'POST' });
    if (result) {
        alert(result.message);
        loadQueue();
    }
}

async function deleteQueueItem(id) {
    if (!confirm('Remove this item from the queue?')) return;
    
    const result = await apiRequest(`/queue/${id}`, { method: 'DELETE' });
    if (result) {
        loadQueue();
        loadStats();
    }
}

async function deleteProfile(id) {
    if (!confirm('Delete this profile?')) return;
    
    const result = await apiRequest(`/profiles/${id}`, { method: 'DELETE' });
    if (result) {
        loadProfiles();
    }
}

async function deleteScanRoot(id) {
    if (!confirm('Delete this scan root?')) return;
    
    const result = await apiRequest(`/scan-roots/${id}`, { method: 'DELETE' });
    if (result) {
        loadScanRoots();
    }
}

function showCreateProfileForm() {
    // TODO: Implement modal form for creating profiles
    alert('Profile creation form coming soon! Use the API at /api/profiles for now.');
}

function showCreateScanRootForm() {
    // TODO: Implement modal form for creating scan roots
    alert('Scan root creation form coming soon! Use the API at /api/scan-roots for now.');
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
                    <div class="flex gap-2 mt-2">
                        ${p.enable_filters ? '<span class="px-2 py-0.5 bg-purple-900 text-purple-300 text-xs rounded">Filters</span>' : ''}
                        ${p.chapter_markers ? '<span class="px-2 py-0.5 bg-gray-600 text-gray-300 text-xs rounded">Chapters</span>' : ''}
                        ${p.hw_accel_enabled ? '<span class="px-2 py-0.5 bg-green-900 text-green-300 text-xs rounded">GPU</span>' : ''}
                        ${p.two_pass ? '<span class="px-2 py-0.5 bg-yellow-900 text-yellow-300 text-xs rounded">2-Pass</span>' : ''}
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
        is_default: document.getElementById('profileIsDefault').checked
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
    document.getElementById('scanRootLibraryType').value = 'custom';
    document.getElementById('libraryTypeRecommendation').classList.add('hidden');
    
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
    
    document.getElementById('scanRootModal').classList.remove('hidden');
}

function closeScanRootModal() {
    document.getElementById('scanRootModal').classList.add('hidden');
    currentScanRootId = null;
}

document.getElementById('scanRootForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const data = {
        path: document.getElementById('scanRootPath').value,
        profile_id: parseInt(document.getElementById('scanRootProfile').value),
        library_type: document.getElementById('scanRootLibraryType').value,
        recursive: document.getElementById('scanRootRecursive').checked,
        enabled: document.getElementById('scanRootEnabled').checked
    };
    
    const method = currentScanRootId ? 'PUT' : 'POST';
    const url = currentScanRootId ? `/scan-roots/${currentScanRootId}` : '/scan-roots';
    
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
let allQueueItems = []; // Store all items for filtering

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

// Filter queue by search and status
function filterQueue() {
    const searchTerm = document.getElementById('queueSearch').value.toLowerCase();
    const statusFilter = document.getElementById('queueStatusFilter').value;
    
    let filtered = allQueueItems;
    
    // Apply search filter
    if (searchTerm) {
        filtered = filtered.filter(item => 
            item.file_path.toLowerCase().includes(searchTerm)
        );
    }
    
    // Apply status filter
    if (statusFilter) {
        filtered = filtered.filter(item => 
            item.status === statusFilter
        );
    }
    
    // Display filtered results
    displayQueueItems(filtered);
}

// Display queue items (separated from loading)
function displayQueueItems(items) {
    const container = document.getElementById('queueTable');

    if (items.length === 0) {
        container.innerHTML = '<p class="text-gray-400">No items in queue</p>';
        return;
    }

    // Count statuses for summary
    const counts = { pending: 0, processing: 0, completed: 0, failed: 0, paused: 0 };
    items.forEach(item => { counts[item.status] = (counts[item.status] || 0) + 1; });

    let html = `
        <div class="flex gap-4 mb-4 text-xs text-gray-400">
            <span>Total: ${items.length}</span>
            ${counts.processing ? `<span class="text-blue-400">Processing: ${counts.processing}</span>` : ''}
            ${counts.pending ? `<span class="text-yellow-400">Pending: ${counts.pending}</span>` : ''}
            ${counts.completed ? `<span class="text-green-400">Completed: ${counts.completed}</span>` : ''}
            ${counts.failed ? `<span class="text-red-400">Failed: ${counts.failed}</span>` : ''}
            ${counts.paused ? `<span class="text-orange-400">Paused: ${counts.paused}</span>` : ''}
        </div>
        <table class="w-full text-sm">
            <thead class="border-b border-gray-700">
                <tr class="text-left">
                    <th class="py-2">File</th>
                    <th class="py-2">Status</th>
                    <th class="py-2">Progress</th>
                    <th class="py-2">Priority</th>
                    <th class="py-2">Actions</th>
                </tr>
            </thead>
            <tbody>
    `;

    items.forEach(item => {
        const fileName = item.file_path.split(/[/\\]/).pop();
        const statusConfig = {
            'pending':    { emoji: '⏳', color: 'text-yellow-400', barColor: 'bg-yellow-500' },
            'processing': { emoji: '⚙️', color: 'text-blue-400',   barColor: 'bg-blue-500' },
            'completed':  { emoji: '✅', color: 'text-green-400',  barColor: 'bg-green-500' },
            'failed':     { emoji: '❌', color: 'text-red-400',    barColor: 'bg-red-500' },
            'paused':     { emoji: '⏸️', color: 'text-orange-400', barColor: 'bg-orange-500' }
        };
        const sc = statusConfig[item.status] || { emoji: '❓', color: 'text-gray-400', barColor: 'bg-gray-500' };
        const progress = item.progress || 0;

        // Build progress cell - prominent bar for processing, simple text for others
        let progressCell;
        if (item.status === 'processing' || (item.status === 'paused' && progress > 0)) {
            progressCell = `
                <td class="py-2 min-w-[180px]">
                    <div class="flex items-center gap-2">
                        <div class="flex-1 bg-gray-600 rounded-full h-3 overflow-hidden">
                            <div class="${sc.barColor} h-3 rounded-full transition-all duration-700"
                                 style="width: ${progress}%"></div>
                        </div>
                        <span class="${sc.color} font-mono text-xs font-bold w-12 text-right">${progress.toFixed(1)}%</span>
                    </div>
                </td>
            `;
        } else if (item.status === 'completed') {
            progressCell = `<td class="py-2"><span class="text-green-400 text-xs font-bold">100%</span></td>`;
        } else if (item.status === 'failed') {
            progressCell = `<td class="py-2"><span class="text-red-400 text-xs" title="${item.error_message || ''}">${item.error_message ? 'Error' : '-'}</span></td>`;
        } else {
            progressCell = `<td class="py-2"><span class="text-gray-500 text-xs">-</span></td>`;
        }

        html += `
            <tr class="border-b border-gray-700 hover:bg-gray-700">
                <td class="py-2 max-w-xs truncate" title="${item.file_path}">${fileName}</td>
                <td class="py-2"><span class="${sc.color}">${sc.emoji} ${item.status}</span></td>
                ${progressCell}
                <td class="py-2">${item.priority}</td>
                <td class="py-2">
                    <button onclick="deleteQueueItem(${item.id})"
                        class="text-red-400 hover:text-red-300 text-xs">
                        Delete
                    </button>
                </td>
            </tr>
        `;
    });

    html += '</tbody></table>';
    container.innerHTML = html;
}

// Toggle auto-refresh
function toggleAutoRefresh() {
    const enabled = document.getElementById('autoRefreshQueue').checked;
    
    if (enabled) {
        // Start auto-refresh every 5 seconds
        queueRefreshInterval = setInterval(() => {
            loadQueue();
        }, 5000);
        showMessage('Auto-refresh enabled (5s)', 'info');
    } else {
        // Stop auto-refresh
        if (queueRefreshInterval) {
            clearInterval(queueRefreshInterval);
            queueRefreshInterval = null;
        }
        showMessage('Auto-refresh disabled', 'info');
    }
}

// Update loadQueue to store items for filtering
const originalLoadQueue = loadQueue;
async function loadQueue() {
    const items = await apiRequest('/queue');
    if (items) {
        allQueueItems = items; // Store for filtering
        filterQueue(); // Apply current filters
    }
}

// Delete queue item
async function deleteQueueItem(id) {
    if (!confirm('Delete this item from the queue?')) return;
    
    const result = await apiRequest(`/queue/${id}`, {
        method: 'DELETE'
    });
    
    if (result) {
        showMessage('Item deleted from queue', 'success');
        loadQueue();
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
        
        // Summary cards
        const t = data.totals;
        document.getElementById('statTotalFiles').textContent = t.total.toLocaleString();
        document.getElementById('statOriginalSize').textContent = formatBytes(t.total_original);
        document.getElementById('statNewSize').textContent = formatBytes(t.total_new);
        document.getElementById('statSaved').textContent = formatBytes(t.total_saved);
        document.getElementById('statAvgPct').textContent = t.avg_savings_pct.toFixed(1) + '%';
        
        // Daily chart
        renderDailyChart(data.daily);
        
        // Codec breakdown
        renderCodecBreakdown(data.codecs, t.total);
        
        // Recent history
        renderRecentHistory(data.recent);
        
    } catch (err) {
        console.error('Failed to load statistics:', err);
    }
    
    // Also load health and upscalers
    loadHealth();
    loadUpscalers();
}

function formatBytes(bytes) {
    if (!bytes || bytes === 0) return '0 B';
    const gb = bytes / (1024 * 1024 * 1024);
    if (gb >= 1) return gb.toFixed(1) + ' GB';
    const mb = bytes / (1024 * 1024);
    if (mb >= 1) return mb.toFixed(1) + ' MB';
    return (bytes / 1024).toFixed(0) + ' KB';
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
        container.innerHTML = '<p class="text-gray-500 text-sm">No encoding history yet</p>';
        return;
    }
    
    container.innerHTML = recent.map(h => {
        const filename = h.file_path.split(/[/\\]/).pop();
        const savedPct = h.original_size_bytes > 0 
            ? ((h.savings_bytes / h.original_size_bytes) * 100).toFixed(1) 
            : 0;
        const timeStr = h.encoding_time_seconds > 60 
            ? `${Math.floor(h.encoding_time_seconds/60)}m ${h.encoding_time_seconds%60}s`
            : `${h.encoding_time_seconds}s`;
        
        return `
            <div class="flex justify-between items-center p-2 bg-gray-700 rounded text-sm">
                <div class="flex-1 truncate mr-3" title="${h.file_path}">
                    ${filename}
                </div>
                <div class="flex gap-3 text-xs text-gray-400 flex-shrink-0">
                    <span class="text-green-400">-${savedPct}%</span>
                    <span>${formatBytes(h.original_size_bytes)} → ${formatBytes(h.new_size_bytes)}</span>
                    <span>${timeStr}</span>
                </div>
            </div>
        `;
    }).join('');
}

async function loadHealth() {
    try {
        const health = await apiRequest('/health');
        if (!health) return;
        
        const container = document.getElementById('healthStatus');
        container.innerHTML = `
            <div class="p-3 bg-gray-700 rounded">
                <div class="text-xs text-gray-400 mb-1">Service</div>
                <div class="font-bold ${health.status === 'ok' ? 'text-green-400' : 'text-yellow-400'}">
                    ${health.status === 'ok' ? '● Healthy' : '⚠ ' + health.status}
                </div>
                <div class="text-xs text-gray-500 mt-1">v${health.version}</div>
            </div>
            <div class="p-3 bg-gray-700 rounded">
                <div class="text-xs text-gray-400 mb-1">HandBrakeCLI</div>
                <div class="font-bold ${health.handbrake?.installed ? 'text-green-400' : 'text-red-400'}">
                    ${health.handbrake?.installed ? '● Installed' : '✗ Not Found'}
                </div>
                <div class="text-xs text-gray-500 mt-1 truncate">${health.handbrake?.path || 'not in PATH'}</div>
            </div>
            <div class="p-3 bg-gray-700 rounded">
                <div class="text-xs text-gray-400 mb-1">Database</div>
                <div class="font-bold ${health.database?.status === 'ok' ? 'text-green-400' : 'text-red-400'}">
                    ${health.database?.status === 'ok' ? '● OK' : '✗ Error'}
                </div>
                <div class="text-xs text-gray-500 mt-1">${health.database?.profiles || 0} profiles, ${health.database?.history_records || 0} history</div>
            </div>
            ${health.disk ? `
            <div class="p-3 bg-gray-700 rounded">
                <div class="text-xs text-gray-400 mb-1">Disk Space</div>
                <div class="font-bold ${health.disk.percent_used > 90 ? 'text-red-400' : 'text-green-400'}">
                    ${health.disk.free_gb} GB free
                </div>
                <div class="text-xs text-gray-500 mt-1">${health.disk.percent_used}% used of ${health.disk.total_gb} GB</div>
            </div>` : ''}
        `;
    } catch (err) {
        document.getElementById('healthStatus').innerHTML = '<p class="text-red-400">Failed to load health status</p>';
    }
}

async function loadUpscalers() {
    try {
        const info = await apiRequest('/upscalers');
        if (!info) return;
        
        const container = document.getElementById('upscalerStatus');
        const defs = info.definitions;
        const det = info.detection?.details || {};
        
        container.innerHTML = Object.entries(defs).map(([key, up]) => {
            const d = det[key] || {};
            const installed = d.installed;
            
            return `
                <div class="p-3 bg-gray-700 rounded">
                    <div class="flex justify-between items-start">
                        <div>
                            <span class="text-lg">${up.icon}</span>
                            <span class="font-bold ml-1">${up.name}</span>
                            ${installed ? '<span class="ml-2 px-2 py-0.5 bg-green-900 text-green-300 text-xs rounded">Installed</span>' : '<span class="ml-2 px-2 py-0.5 bg-gray-600 text-gray-400 text-xs rounded">Not Found</span>'}
                        </div>
                    </div>
                    <p class="text-xs text-gray-400 mt-1">${up.description}</p>
                    <p class="text-xs text-gray-500 mt-1">Best for: ${up.best_for}</p>
                    ${!installed ? `<a href="${up.download_url}" target="_blank" class="text-xs text-blue-400 hover:text-blue-300 mt-1 inline-block">Download →</a>` : ''}
                    ${installed && d.path ? `<p class="text-xs text-gray-500 mt-1 truncate">Path: ${d.path}</p>` : ''}
                </div>
            `;
        }).join('');
    } catch (err) {
        document.getElementById('upscalerStatus').innerHTML = '<p class="text-gray-500">Failed to detect upscalers</p>';
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
