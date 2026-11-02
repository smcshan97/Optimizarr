// ============================================================
// NOTIFICATION WEBHOOK MANAGEMENT
// ============================================================

async function loadNotifications() {
    const container = document.getElementById('notificationsList');
    if (!container) return;

    const notifs = await apiRequest('/notifications');
    if (!notifs || notifs.length === 0) {
        container.innerHTML = '<p class="text-gray-500 text-sm">No notification webhooks configured. Click "+ Add Webhook" to get started.</p>';
        return;
    }

    container.innerHTML = notifs.map(n => {
        const events = (n.events || []).map(e => {
            const labels = { encode_complete: '✅ Complete', encode_failed: '❌ Failed', queue_empty: '🏁 Queue Empty' };
            return `<span class="px-2 py-0.5 bg-gray-600 text-gray-300 text-xs rounded">${labels[e] || e}</span>`;
        }).join(' ');
        const typeBadge = {
            discord: '<span class="px-2 py-0.5 bg-purple-900 text-purple-300 text-xs rounded">Discord</span>',
            slack: '<span class="px-2 py-0.5 bg-green-900 text-green-300 text-xs rounded">Slack</span>',
            generic: '<span class="px-2 py-0.5 bg-gray-600 text-gray-300 text-xs rounded">Generic</span>',
        }[n.webhook_type] || '';
        const enabledBadge = n.enabled
            ? '<span class="px-2 py-0.5 bg-green-900 text-green-300 text-xs rounded">Active</span>'
            : '<span class="px-2 py-0.5 bg-gray-600 text-gray-400 text-xs rounded">Disabled</span>';

        return `
            <div class="p-4 rounded-lg border border-gray-700" style="background:var(--card-inner-bg, rgba(0,0,0,0.15))">
                <div class="flex justify-between items-start">
                    <div>
                        <div class="flex items-center gap-2 mb-2">
                            <span class="font-medium text-gray-100">${_esc(n.name)}</span>
                            ${typeBadge} ${enabledBadge}
                        </div>
                        <div class="text-xs text-gray-500 mb-2 truncate max-w-[400px]" title="${_esc(n.webhook_url)}">${_esc(n.webhook_url)}</div>
                        <div class="flex gap-1 flex-wrap">${events}</div>
                    </div>
                    <div class="flex gap-2">
                        <button onclick="testNotification(${n.id})" class="btn btn-secondary btn-xs">Test</button>
                        <button onclick="toggleNotification(${n.id}, ${n.enabled ? 'false' : 'true'})" class="btn btn-secondary btn-xs">${n.enabled ? 'Disable' : 'Enable'}</button>
                        <button onclick="deleteNotification(${n.id}, '${(n.name||'').replace(/\\/g,'\\\\').replace(/'/g,"\\'").replace(/"/g,'&quot;')}')" class="btn btn-danger btn-xs">Delete</button>
                    </div>
                </div>
            </div>`;
    }).join('');
}

function showAddNotificationModal() {
    const html = `
        <div class="modal-backdrop" id="notifModal" onclick="if(event.target===this)this.remove()">
            <div class="modal-panel" style="max-width:500px">
                <div class="modal-header">
                    <h3 class="modal-title">Add Notification Webhook</h3>
                    <button class="modal-close" onclick="document.getElementById('notifModal').remove()">✕</button>
                </div>
                <div class="modal-body space-y-4">
                    <div>
                        <label class="block text-sm font-medium text-gray-300 mb-1">Name</label>
                        <input id="notifName" type="text" placeholder="My Discord Server" class="form-input w-full">
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-300 mb-1">Webhook URL</label>
                        <input id="notifUrl" type="url" placeholder="https://discord.com/api/webhooks/..." class="form-input w-full">
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-300 mb-1">Type</label>
                        <select id="notifType" class="form-select w-full">
                            <option value="discord">Discord</option>
                            <option value="slack">Slack</option>
                            <option value="generic">Generic JSON</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-300 mb-2">Events</label>
                        <div class="space-y-2">
                            <label class="flex items-center gap-2 cursor-pointer">
                                <input type="checkbox" id="notifEvtComplete" checked class="w-4 h-4"> 
                                <span class="text-sm text-gray-300">Encode Complete</span>
                            </label>
                            <label class="flex items-center gap-2 cursor-pointer">
                                <input type="checkbox" id="notifEvtFailed" checked class="w-4 h-4"> 
                                <span class="text-sm text-gray-300">Encode Failed</span>
                            </label>
                            <label class="flex items-center gap-2 cursor-pointer">
                                <input type="checkbox" id="notifEvtEmpty" checked class="w-4 h-4"> 
                                <span class="text-sm text-gray-300">Queue Empty</span>
                            </label>
                        </div>
                    </div>
                </div>
                <div class="modal-footer">
                    <button onclick="document.getElementById('notifModal').remove()" class="btn btn-secondary">Cancel</button>
                    <button onclick="saveNotification()" class="btn btn-primary">Save</button>
                </div>
            </div>
        </div>`;
    document.body.insertAdjacentHTML('beforeend', html);
}

async function saveNotification() {
    const name = document.getElementById('notifName').value.trim();
    const url = document.getElementById('notifUrl').value.trim();
    const type = document.getElementById('notifType').value;
    const events = [];
    if (document.getElementById('notifEvtComplete').checked) events.push('encode_complete');
    if (document.getElementById('notifEvtFailed').checked) events.push('encode_failed');
    if (document.getElementById('notifEvtEmpty').checked) events.push('queue_empty');

    if (!name || !url) {
        showMessage('Name and URL are required', 'error');
        return;
    }

    const result = await apiRequest('/notifications', {
        method: 'POST',
        body: JSON.stringify({ name, webhook_url: url, webhook_type: type, events })
    });
    if (result) {
        document.getElementById('notifModal').remove();
        showMessage('Webhook added', 'success');
        loadNotifications();
    }
}

async function testNotification(id) {
    const result = await apiRequest(`/notifications/${id}/test`, { method: 'POST' });
    if (result) showMessage('Test notification sent', 'success');
}

async function toggleNotification(id, enabled) {
    await apiRequest(`/notifications/${id}`, {
        method: 'PUT',
        body: JSON.stringify({ enabled })
    });
    loadNotifications();
}

async function deleteNotification(id, name) {
    if (!confirm(`Delete webhook "${name}"?`)) return;
    const result = await apiRequest(`/notifications/${id}`, { method: 'DELETE' });
    if (result) {
        showMessage('Webhook deleted', 'success');
        loadNotifications();
    }
}

// Schedule management
let selectedDays = new Set([0, 1, 2, 3, 4, 5, 6]); // All days selected by default

// loadSchedule and saveSchedule are defined in the Windows hours section below (~line 2560+)

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

