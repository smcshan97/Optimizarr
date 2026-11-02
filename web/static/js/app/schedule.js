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

// Save schedule configuration
async function saveSchedule() {
    const useWinEl = document.getElementById('useWindowsRestHours');
    const config = {
        enabled: document.getElementById('scheduleEnabled').checked,
        days_of_week: Array.from(selectedDays).sort().join(','),
        start_time: document.getElementById('startTime').value,
        end_time: document.getElementById('endTime').value,
        timezone: 'local',
        use_windows_rest_hours: useWinEl ? useWinEl.checked : false,
        max_concurrent_jobs: parseInt((document.getElementById('maxConcurrentJobs') || {}).value) || 1,
        finish_before_stop: (document.getElementById('finishBeforeStop') || {}).checked || false,
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

// Load schedule configuration (includes Windows hours support)
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

    const finishEl = document.getElementById('finishBeforeStop');
    if (finishEl) finishEl.checked = config.finish_before_stop || false;

    const maxConcEl = document.getElementById('maxConcurrentJobs');
    if (maxConcEl) maxConcEl.value = config.max_concurrent_jobs || 1;

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
        .map(p => `<option value="${p.id}" ${p.id === currentId ? 'selected' : ''}>${_esc(p.name)}</option>`)
        .join('');

    return `
        <select
            title="Profile: ${_esc(currentName)}"
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


