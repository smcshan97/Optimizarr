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
