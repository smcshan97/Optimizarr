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
                        ${p.resolution ? `<div><span class="text-gray-400">Resolution:</span> ${p.resolution}</div>` : ''}
                        ${p.preset ? `<div><span class="text-gray-400">Preset:</span> ${p.preset}</div>` : ''}
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
// FOLDER BROWSER
// ============================================================

function browseFolderPath() {
    document.getElementById('folderBrowser').click();
}

function handleFolderSelect(event) {
    const files = event.target.files;
    if (files.length > 0) {
        // Try multiple methods to get the full path
        const firstFile = files[0];
        let dirPath = '';
        
        // Method 1: webkitRelativePath (most reliable for directory)
        if (firstFile.webkitRelativePath) {
            const pathParts = firstFile.webkitRelativePath.split('/');
            if (pathParts.length > 1) {
                pathParts.pop(); // Remove filename
                dirPath = pathParts.join('/');
            }
        }
        
        // Method 2: Try to construct from multiple files
        if (!dirPath && files.length > 1) {
            const commonPath = findCommonPath(Array.from(files).map(f => f.webkitRelativePath || f.name));
            if (commonPath) dirPath = commonPath;
        }
        
        // Method 3: Just use the folder name (last resort)
        if (!dirPath && firstFile.webkitRelativePath) {
            dirPath = firstFile.webkitRelativePath.split('/')[0];
        }
        
        // If we got something, use it
        if (dirPath) {
            const pathInput = document.getElementById('scanRootPath');
            const currentValue = pathInput.value;
            
            // If input is empty or just a placeholder, replace it
            if (!currentValue || currentValue.includes('\\Media\\') || currentValue.includes('/mnt/media/')) {
                pathInput.value = dirPath;
            } else {
                // Append to existing path if it looks like a base path
                pathInput.value = currentValue.replace(/\/$/, '') + '/' + dirPath;
            }
            
            showMessage('Folder selected! You may need to adjust the full path manually.', 'info');
        } else {
            showMessage('Could not extract path. Please enter the full path manually.', 'error');
        }
    }
}

function findCommonPath(paths) {
    if (paths.length === 0) return '';
    
    const splitPaths = paths.map(p => p.split('/'));
    const commonParts = [];
    
    for (let i = 0; i < splitPaths[0].length - 1; i++) {
        const part = splitPaths[0][i];
        if (splitPaths.every(p => p[i] === part)) {
            commonParts.push(part);
        } else {
            break;
        }
    }
    
    return commonParts.join('/');
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
    
    let html = `
        <table class="w-full text-sm">
            <thead class="border-b border-gray-700">
                <tr class="text-left">
                    <th class="py-2">File</th>
                    <th class="py-2">Status</th>
                    <th class="py-2">Profile</th>
                    <th class="py-2">Priority</th>
                    <th class="py-2">Progress</th>
                    <th class="py-2">Actions</th>
                </tr>
            </thead>
            <tbody>
    `;
    
    items.forEach(item => {
        const fileName = item.file_path.split(/[/\\]/).pop();
        const statusEmoji = {
            'pending': '⏳',
            'processing': '⚙️',
            'completed': '✅',
            'failed': '❌',
            'paused': '⏸️'
        }[item.status] || '❓';
        
        html += `
            <tr class="border-b border-gray-700 hover:bg-gray-700">
                <td class="py-2 max-w-xs truncate" title="${item.file_path}">${fileName}</td>
                <td class="py-2">${statusEmoji} ${item.status}</td>
                <td class="py-2">${item.profile_id || 'N/A'}</td>
                <td class="py-2">${item.priority}</td>
                <td class="py-2">${item.progress.toFixed(1)}%</td>
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

