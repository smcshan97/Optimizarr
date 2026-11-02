// ============================================================
// QUEUE IMPROVEMENTS
// ============================================================

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

// Inline priority (rank) editor — click the number, type, Enter/blur saves
function editPriority(el, id, current) {
    const input = document.createElement('input');
    input.type = 'number';
    input.min = '1';
    input.value = current;
    input.className = 'js-priority-input';
    input.style.cssText = 'width:64px;background:var(--card-bg);color:var(--text-primary);border:1px solid var(--accent);border-radius:4px;padding:1px 4px;font-size:12px';

    let done = false;
    const commit = async () => {
        if (done) return;
        done = true;
        const v = parseInt(input.value);
        if (Number.isInteger(v) && v >= 1 && v !== current) {
            const res = await apiRequest(`/queue/${id}`, {
                method: 'PATCH',
                body: JSON.stringify({ priority: v })
            });
            if (res) showMessage(`Rank set to ${v}`, 'success');
        }
        loadQueue();
    };

    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') input.blur();
        if (e.key === 'Escape') { done = true; loadQueue(); }
    });
    input.addEventListener('blur', commit);

    el.replaceWith(input);
    input.focus();
    input.select();
}

// Clipboard with a fallback: navigator.clipboard needs a secure context, and
// Optimizarr is routinely reached over plain http on a LAN address.
async function copyToClipboard(text) {
    if (!text) return false;
    try {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(text);
            return true;
        }
    } catch (_) { /* fall through to the legacy path */ }
    try {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        const ok = document.execCommand('copy');
        ta.remove();
        return ok;
    } catch (_) {
        return false;
    }
}

// Open a queue item's folder in the file manager ON THE OPTIMIZARR HOST —
// that is where the media is. Viewing from a phone or another desktop, no
// window can appear for you, so the path is copied to your clipboard instead.
async function revealQueueItem(id) {
    const result = await apiRequest(`/queue/${id}/reveal`, { method: 'POST' });
    if (!result) return;
    if (result.success) {
        showMessage(result.message, 'success');
        return;
    }
    const copied = await copyToClipboard(result.folder || result.path || '');
    showMessage(copied ? `${result.message} — path copied to clipboard instead`
                       : result.message,
                copied ? 'info' : 'warning');
}

async function retryQueueItem(id) {
    const result = await apiRequest(`/queue/${id}/retry`, { method: 'POST' });
    if (result) {
        showMessage(result.message || 'Item re-queued', result.success === false ? 'error' : 'success');
        loadQueue();
    }
}

async function recheckPermissions(id) {
    const result = await apiRequest(`/queue/${id}/recheck-permissions`, { method: 'POST' });
    if (result) {
        showMessage(result.message, result.success === false ? 'warning' : 'success');
        loadQueue();
    }
}

async function recheckQuarantined(id) {
    const result = await apiRequest(`/queue/${id}/recheck-quarantined`, { method: 'POST' });
    if (result) {
        showMessage(result.message, result.success === false ? 'warning' : 'success');
        loadQueue();
    }
}

async function recheckAllQuarantined() {
    const count = queueCounts.quarantined || 0;
    if (count === 0) {
        showMessage('No quarantined items to check', 'info');
        return;
    }
    const result = await apiRequest('/queue/recheck-quarantined', { method: 'POST' });
    if (result) {
        showMessage(result.message, 'success');
        loadQueue();
    }
}

async function recheckAllPermissions() {
    const blocked = queueCounts.permission_error || 0;
    if (blocked === 0) {
        showMessage('No permission-error items to check', 'info');
        return;
    }
    const result = await apiRequest('/queue/recheck-permissions', { method: 'POST' });
    if (result) {
        showMessage(result.message, 'success');
        loadQueue();
    }
}

async function retryAllFailed() {
    const failedCount = queueCounts.failed || 0;
    if (failedCount === 0) {
        showMessage('No failed items to retry', 'info');
        return;
    }
    if (!confirm(`Re-queue all ${failedCount} failed item(s)?`)) return;
    const result = await apiRequest('/queue/retry-failed', { method: 'POST' });
    if (result) {
        showMessage(result.message || 'Failed items re-queued', 'success');
        loadQueue();
    }
}

// Toggle auto-refresh (main interval checks this checkbox directly)
function toggleAutoRefresh() {
    const enabled = document.getElementById('autoRefreshQueue').checked;
    if (enabled) {
        loadQueue(); // Immediate refresh when re-enabled
    }
}


