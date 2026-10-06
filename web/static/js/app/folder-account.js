// ============================================================
// FOLDER BROWSER (Server-Side Directory Browser)
// ============================================================

let _folderBrowserTarget = 'scanRootPath'; // default target field

async function browseFolderPath(targetFieldId) {
    _folderBrowserTarget = targetFieldId || 'scanRootPath';
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
            // Escapes for a JS string inside an HTML attribute: backslashes +
            // single quotes for JS, then double quotes so a quoted directory
            // name can't break out of the attribute.
            const escapedPath = dir.path.replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '&quot;');
            html += `
                <div class="flex items-center gap-3 px-4 py-2 hover:bg-gray-600 cursor-pointer rounded group"
                     ondblclick="loadDirectoryListing('${escapedPath}')">
                    <span class="text-yellow-400">📁</span>
                    <span class="flex-1" onclick="loadDirectoryListing('${escapedPath}')">${_esc(dir.name)}</span>
                    <button onclick="event.stopPropagation(); selectBrowsedFolder('${escapedPath}')"
                        class="hidden group-hover:block btn btn-primary btn-xs">
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
    const target = document.getElementById(_folderBrowserTarget);
    if (target) target.value = path;
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
    
    if (newPass.length < 8) {
        showMessage('Password must be at least 8 characters', 'error');
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
        // The change revoked every older token, including ours — keep this
        // session alive with the fresh one the server returned.
        if (result.access_token) localStorage.setItem('token', result.access_token);
        showMessage('Password updated — other sessions have been signed out.', 'success');
        document.getElementById('currentPassword').value = '';
        document.getElementById('newPassword').value = '';
        document.getElementById('confirmPassword').value = '';
        // Clear the must_change_password flag in localStorage
        try {
            const user = JSON.parse(localStorage.getItem('user') || '{}');
            if (user.must_change_password) {
                user.must_change_password = false;
                localStorage.setItem('user', JSON.stringify(user));
            }
        } catch (e) { /* ignore */ }
    }
}

// Load account info when settings tab opens
function loadAccountInfo() {
    // Login stores a single 'user' JSON blob (login.html) — the old code
    // read 'username'/'is_admin' keys nothing ever set, so this panel
    // always showed "admin / Administrator" regardless of who logged in.
    let user = {};
    try { user = JSON.parse(localStorage.getItem('user') || '{}'); } catch (_) {}

    document.getElementById('accountUsername').value = user.username || '';
    document.getElementById('accountRole').value = user.is_admin ? 'Administrator' : 'User';
}


