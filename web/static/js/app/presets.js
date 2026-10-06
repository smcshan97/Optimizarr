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
        const token = localStorage.getItem('token');
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
        const token = localStorage.getItem('token');
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

async function exportConfigJson() {
    const result = await apiRequest('/backup/json');
    if (!result) return;
    const blob = new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' });
    const stamp = new Date().toISOString().slice(0, 10);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `optimizarr_config_${stamp}.json`;
    document.body.appendChild(a); a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showMessage('Config exported', 'success');
}

async function importConfigJson(event) {
    const file = event.target.files[0];
    if (!file) return;

    const fileNameEl = document.getElementById('importConfigFileName');
    const statusEl   = document.getElementById('importConfigStatus');
    if (fileNameEl) fileNameEl.textContent = file.name;

    let config;
    try {
        config = JSON.parse(await file.text());
    } catch (e) {
        if (statusEl) { statusEl.className = 'mt-3 text-xs text-red-400'; statusEl.classList.remove('hidden'); statusEl.textContent = '✗ Not valid JSON'; }
        event.target.value = '';
        return;
    }

    if (statusEl) { statusEl.className = 'mt-3 text-xs text-yellow-400'; statusEl.classList.remove('hidden'); statusEl.textContent = '⏳ Importing…'; }

    const result = await apiRequest('/restore/json', {
        method: 'POST',
        body: JSON.stringify(config)
    });

    if (result && result.summary) {
        if (statusEl) { statusEl.className = 'mt-3 text-xs text-green-400'; statusEl.textContent = '✓ ' + result.message; }
        showMessage(result.message, 'success');
        // Refresh whatever the import may have changed
        loadProfiles();
        loadScanRoots();
        loadSettings();
    } else if (statusEl) {
        statusEl.className = 'mt-3 text-xs text-red-400';
        statusEl.textContent = '✗ Import failed';
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
        'fastest_first':      { sort_by: 'fastest_first',      order: 'asc'  },
        'slowest_first':      { sort_by: 'slowest_first',      order: 'desc' },
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

