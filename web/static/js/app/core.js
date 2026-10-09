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
    
    try {
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
        
        if (!response.ok) {
            const errorText = await response.text();
            let detail = `HTTP ${response.status}`;
            try { detail = JSON.parse(errorText).detail || detail; } catch {}
            console.error(`API error ${endpoint}: ${detail}`);
            return null;
        }
        
        return response.json();
    } catch (err) {
        console.error(`API request failed ${endpoint}:`, err);
        return null;
    }
}

// Tab switching — sidebar nav + tab panels
function switchTab(tabName) {
    // Hide all tab panels
    document.querySelectorAll('.tab-panel').forEach(el => el.classList.remove('active'));
    // Deactivate all nav items
    document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));

    // Show selected tab panel
    const panel = document.getElementById(`content-${tabName}`);
    if (panel) panel.classList.add('active');

    // Activate nav item
    const navItem = document.querySelector(`.nav-item[data-tab="${tabName}"]`);
    if (navItem) navItem.classList.add('active');

    // Close mobile sidebar if open
    if (window.innerWidth < 769) {
        const sb = document.getElementById('sidebar');
        const ov = document.getElementById('mobileOverlay');
        if (sb) sb.classList.remove('open');
        if (ov) ov.classList.add('hidden');
    }

    // Load tab data
    if (tabName === 'queue') { loadQueue(); loadQueueAutoSort(); }
    if (tabName === 'profiles') loadProfiles();
    if (tabName === 'scanroots') loadScanRoots();
    if (tabName === 'settings') { loadSettings(); loadConnections(); }
    if (tabName === 'schedule') loadSchedule();
    if (tabName === 'logs') loadLogs();
    if (tabName === 'statistics') loadStatistics();
    if (tabName === 'duplicates') loadDuplicates();
}

// Mobile sidebar toggle
function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('mobileOverlay');
    if (sidebar) sidebar.classList.toggle('open');
    if (overlay) overlay.classList.toggle('hidden');
}

// Load statistics
async function loadStats() {
    const stats = await apiRequest('/stats');
    if (stats) {
        document.getElementById('spaceSaved').textContent = `${stats.total_space_saved_gb} GB`;
        document.getElementById('filesProcessed').textContent = stats.total_files_processed;
        document.getElementById('queuePending').textContent = stats.queue_pending;
        document.getElementById('activeJobs').textContent = stats.queue_processing;

        // Update sidebar nav badge
        const navBadge = document.getElementById('navQueueCount');
        if (navBadge) {
            const total = (stats.queue_pending || 0) + (stats.queue_processing || 0);
            navBadge.textContent = total;
            navBadge.style.display = total > 0 ? '' : 'none';
        }
    }
}

// Load resource monitoring data
async function loadResources() {
    const resources = await apiRequest('/resources/current');
    if (!resources) return;

    // Handle degraded response (resource monitoring timed out)
    if (resources._degraded) {
        console.warn('Resource monitoring degraded:', resources._error);
        // Show warning in GPU status areas
        const gpuTempStatus = document.getElementById('gpuTempStatus');
        if (gpuTempStatus) gpuTempStatus.innerHTML = '<span style="color:var(--danger)">⚠️ Monitoring timeout</span>';
        return;
    }

    // Helper: set bar color class and value text color based on temperature
    function setTempBarColor(barEl, valueEl, tempC, normalColor, normalVar) {
        barEl.className = barEl.className.replace(/\b(cyan|purple|orange|red|yellow)\b/g, '');
        if (tempC === null || tempC === undefined) {
            barEl.classList.add(normalColor);
            valueEl.style.color = `var(--${normalVar})`;
            return;
        }
        if (tempC > 90) {
            barEl.classList.add('red');
            valueEl.style.color = 'var(--danger)';
        } else if (tempC > 75) {
            barEl.classList.add('yellow');
            valueEl.style.color = 'var(--warning)';
        } else {
            barEl.classList.add(normalColor);
            valueEl.style.color = `var(--${normalVar})`;
        }
    }

    function setPercentBarColor(barEl, valueEl, percent, normalColor, normalVar) {
        barEl.className = barEl.className.replace(/\b(cyan|purple|orange|red|yellow)\b/g, '');
        if (percent > 90) {
            barEl.classList.add('red');
            valueEl.style.color = 'var(--danger)';
        } else if (percent > 75) {
            barEl.classList.add('yellow');
            valueEl.style.color = 'var(--warning)';
        } else {
            barEl.classList.add(normalColor);
            valueEl.style.color = `var(--${normalVar})`;
        }
    }

    // CPU Temperature (primary) + usage (secondary)
    const cpuTempC = resources.cpu.temperature_c;
    const cpuTempEl = document.getElementById('cpuTemp');
    const cpuTempBar = document.getElementById('cpuTempBar');
    const cpuTempStatus = document.getElementById('cpuTempStatus');
    const cpuUsageInfo = document.getElementById('cpuUsageInfo');

    if (cpuTempC !== null && cpuTempC !== undefined) {
        cpuTempEl.textContent = `${cpuTempC.toFixed(0)}°C`;
        // Map temp to bar width: 30°C=0%, 105°C=100%
        const barPct = Math.min(100, Math.max(0, ((cpuTempC - 30) / 75) * 100));
        cpuTempBar.style.width = `${barPct}%`;
        setTempBarColor(cpuTempBar, cpuTempEl, cpuTempC, 'cyan', 'accent');
        if (cpuTempStatus) cpuTempStatus.textContent = cpuTempC > 90 ? '⚠️ Hot' : cpuTempC > 75 ? '⚠️ Warm' : '✓ Normal';
    } else {
        cpuTempEl.textContent = 'N/A';
        cpuTempBar.style.width = '0%';
        if (cpuTempStatus) cpuTempStatus.textContent = 'Sensor unavailable';
    }
    if (cpuUsageInfo) cpuUsageInfo.textContent = `Usage: ${resources.cpu.percent.toFixed(0)}%  ·  ${resources.cpu.count} cores`;

    // Memory (unchanged logic)
    const memPercent = resources.memory.percent.toFixed(1);
    const memUsedGB = (resources.memory.used_mb / 1024).toFixed(1);
    const memTotalGB = (resources.memory.total_mb / 1024).toFixed(1);
    const memBar = document.getElementById('memoryBar');
    const memVal = document.getElementById('memoryUsage');
    memVal.textContent = `${memPercent}%`;
    memBar.style.width = `${memPercent}%`;
    setPercentBarColor(memBar, memVal, memPercent, 'purple', 'processing');
    const memStatus = document.getElementById('memoryStatus');
    if (memStatus) memStatus.textContent = `${memUsedGB}/${memTotalGB} GB`;

    // GPU Temperature (primary) + usage (secondary)
    const gpuTempEl = document.getElementById('gpuTemp');
    const gpuTempBar = document.getElementById('gpuTempBar');
    const gpuTempStatus = document.getElementById('gpuTempStatus');
    const gpuUsageInfo = document.getElementById('gpuUsageInfo');
    const gpuVramEl = document.getElementById('gpuVram');
    const gpuVramBar = document.getElementById('gpuVramBar');
    const gpuVramStatus = document.getElementById('gpuVramStatus');

    if (resources.gpu && resources.gpu.length > 0) {
        const gpu = resources.gpu[0];
        const gpuTempC = gpu.temperature_c;

        if (gpuTempC !== null && gpuTempC !== undefined) {
            gpuTempEl.textContent = `${gpuTempC}°C`;
            const barPct = Math.min(100, Math.max(0, ((gpuTempC - 30) / 75) * 100));
            gpuTempBar.style.width = `${barPct}%`;
            setTempBarColor(gpuTempBar, gpuTempEl, gpuTempC, 'orange', 'warning');
            if (gpuTempStatus) gpuTempStatus.textContent = gpuTempC > 85 ? '⚠️ Hot' : gpuTempC > 72 ? '⚠️ Warm' : '✓ Normal';
        } else {
            gpuTempEl.textContent = 'N/A';
            gpuTempBar.style.width = '0%';
            if (gpuTempStatus) gpuTempStatus.textContent = 'Temp unavailable';
        }
        if (gpuUsageInfo) gpuUsageInfo.textContent = `${gpu.name.substring(0, 24)}  ·  Util: ${gpu.utilization_percent}%`;

        // GPU VRAM
        const vramUsedGB = (gpu.memory_used_mb / 1024).toFixed(1);
        const vramTotalGB = (gpu.memory_total_mb / 1024).toFixed(1);
        const vramPct = gpu.memory_total_mb > 0 ? ((gpu.memory_used_mb / gpu.memory_total_mb) * 100).toFixed(1) : 0;
        gpuVramEl.textContent = `${vramPct}%`;
        gpuVramBar.style.width = `${vramPct}%`;
        setPercentBarColor(gpuVramBar, gpuVramEl, vramPct, 'cyan', 'accent');
        if (gpuVramStatus) gpuVramStatus.textContent = `${vramUsedGB}/${vramTotalGB} GB`;
    } else {
        gpuTempEl.textContent = 'N/A';
        gpuTempBar.style.width = '0%';
        if (gpuTempStatus) gpuTempStatus.innerHTML = 'No GPU detected <button onclick="reinitGpu()" class="btn btn-primary btn-xs ml-2" style="font-size:10px">Reinit</button>';
        if (gpuUsageInfo) gpuUsageInfo.textContent = '';
        gpuVramEl.textContent = 'N/A';
        gpuVramBar.style.width = '0%';
        if (gpuVramStatus) gpuVramStatus.textContent = '';
    }

    // Update availability badges in settings (if settings tab is visible)
    const cpuTempAvail = document.getElementById('cpuTempAvail');
    if (cpuTempAvail) {
        if (resources.cpu.temp_available) {
            cpuTempAvail.textContent = '✓ Available';
            cpuTempAvail.style.background = 'rgba(46,204,113,0.15)';
            cpuTempAvail.style.color = '#2ecc71';
        } else {
            cpuTempAvail.textContent = '✗ Unavailable';
            cpuTempAvail.style.background = 'rgba(231,76,60,0.15)';
            cpuTempAvail.style.color = '#e74c3c';
        }
    }
    const gpuTempAvail = document.getElementById('gpuTempAvail');
    if (gpuTempAvail) {
        if (resources.gpu && resources.gpu.length > 0 && resources.gpu[0].temperature_c !== null) {
            gpuTempAvail.textContent = '✓ Available';
            gpuTempAvail.style.background = 'rgba(46,204,113,0.15)';
            gpuTempAvail.style.color = '#2ecc71';
        } else {
            gpuTempAvail.textContent = '✗ Unavailable';
            gpuTempAvail.style.background = 'rgba(231,76,60,0.15)';
            gpuTempAvail.style.color = '#e74c3c';
        }
    }
}

