/**
 * Optimizarr Server-Side Folder Browser
 * ======================================
 * Replaces the broken <input webkitdirectory> Browse button with a proper
 * server-side directory browser, inspired by Stash and Sonarr/Radarr.
 *
 * The backend reads the filesystem and sends listings via REST API.
 * The browser never needs direct filesystem access.
 *
 * Usage:
 *   1. Include this script in index.html: <script src="/static/js/folder_browser.js"></script>
 *   2. The openFolderBrowser() function replaces browseFolderPath()
 *   3. It injects a modal with directory navigation and video file preview
 *
 * API endpoints used:
 *   GET /api/filesystem/browse?path=     → list drives/root
 *   GET /api/filesystem/browse?path=D:\  → list contents
 *   GET /api/filesystem/validate?path=   → validate and count videos
 */

// ============================================================
// FOLDER BROWSER MODAL
// ============================================================

/**
 * Open the server-side folder browser modal.
 * When user selects a folder, it sets the scanRootPath input value.
 */
function openFolderBrowser() {
    // Create modal if it doesn't exist yet
    if (!document.getElementById('folderBrowserModal')) {
        _createFolderBrowserModal();
    }

    const modal = document.getElementById('folderBrowserModal');
    modal.classList.remove('hidden');

    // Start browsing from empty (shows drives on Windows, / on Linux)
    _browseTo('');
}

/** Alias so existing onclick="browseFolderPath()" still works */
function browseFolderPath() {
    openFolderBrowser();
}

function _closeFolderBrowser() {
    const modal = document.getElementById('folderBrowserModal');
    if (modal) modal.classList.add('hidden');
}

function _selectCurrentFolder() {
    const pathDisplay = document.getElementById('fb-current-path');
    if (pathDisplay && pathDisplay.textContent) {
        const path = pathDisplay.textContent.trim();
        if (path && path !== 'Loading...') {
            document.getElementById('scanRootPath').value = path;
            _closeFolderBrowser();

            // Show validation info
            _validatePath(path);
        }
    }
}

async function _validatePath(path) {
    try {
        const token = localStorage.getItem('token');
        const resp = await fetch(`/api/filesystem/validate?path=${encodeURIComponent(path)}`, {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        const data = await resp.json();
        if (data.video_count > 0) {
            showMessage(`Path validated: ${data.video_count} video file(s) found`, 'success');
        } else if (data.is_valid) {
            showMessage(`Path exists but no video files found (${data.total_files} files total)`, 'warning');
        } else {
            showMessage(data.message || 'Path validation failed', 'error');
        }
    } catch (e) {
        console.error('Validation error:', e);
    }
}

async function _browseTo(path) {
    const listing = document.getElementById('fb-listing');
    const pathDisplay = document.getElementById('fb-current-path');
    const statsDisplay = document.getElementById('fb-stats');
    const selectBtn = document.getElementById('fb-select-btn');

    listing.innerHTML = '<div class="text-center text-gray-400 py-8">Loading...</div>';
    pathDisplay.textContent = path || 'My Computer';
    selectBtn.disabled = !path;

    try {
        const token = localStorage.getItem('token');
        const url = path
            ? `/api/filesystem/browse?path=${encodeURIComponent(path)}`
            : '/api/filesystem/browse';

        const resp = await fetch(url, {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        const data = await resp.json();

        if (data.error) {
            listing.innerHTML = `<div class="text-red-400 py-4 px-3">${_esc(data.error)}</div>`;
            return;
        }

        pathDisplay.textContent = data.path || 'My Computer';

        // Build stats line
        if (data.video_count !== undefined) {
            const videoInfo = data.video_count > 0
                ? `<span class="text-green-400">${data.video_count} video file(s)</span> (${data.total_video_size_human || ''})`
                : `<span class="text-gray-500">No video files</span>`;
            statsDisplay.innerHTML = `${data.total_directories || 0} folders, ${data.total_files || 0} files — ${videoInfo}`;
        } else {
            statsDisplay.innerHTML = '';
        }

        // Enable/disable select button
        selectBtn.disabled = !data.path;

        // Build directory listing
        let html = '';

        // Parent directory (go up)
        if (data.parent !== undefined && data.parent !== null) {
            html += `
                <div class="fb-entry fb-parent" onclick="_browseTo('${_escAttr(data.parent)}')">
                    <span class="fb-icon">📁</span>
                    <span class="fb-name">..</span>
                    <span class="fb-hint">Go up</span>
                </div>`;
        } else if (data.path && data.path !== '') {
            // On Windows, go back to drive list
            html += `
                <div class="fb-entry fb-parent" onclick="_browseTo('')">
                    <span class="fb-icon">💻</span>
                    <span class="fb-name">..</span>
                    <span class="fb-hint">Back to drives</span>
                </div>`;
        }

        // Directories
        const dirs = data.directories || [];
        for (const dir of dirs) {
            html += `
                <div class="fb-entry fb-dir" onclick="_browseTo('${_escAttr(dir.path)}')">
                    <span class="fb-icon">${dir.type === 'drive' ? '💾' : '📁'}</span>
                    <span class="fb-name">${_esc(dir.name)}</span>
                </div>`;
        }

        // Files (show video files highlighted)
        const files = data.files || [];
        for (const file of files) {
            const cls = file.is_video ? 'fb-video' : 'fb-file';
            const icon = file.is_video ? '🎬' : '📄';
            html += `
                <div class="fb-entry ${cls}">
                    <span class="fb-icon">${icon}</span>
                    <span class="fb-name">${_esc(file.name)}</span>
                    <span class="fb-size">${_esc(file.size_human)}</span>
                </div>`;
        }

        if (!dirs.length && !files.length && !data.parent) {
            html = '<div class="text-gray-500 py-4 px-3">Empty or no accessible entries</div>';
        }

        listing.innerHTML = html;

    } catch (e) {
        listing.innerHTML = `<div class="text-red-400 py-4 px-3">Error: ${_esc(e.message)}</div>`;
        console.error('Browse error:', e);
    }
}

function _esc(s) {
    if (!s) return '';
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

function _escAttr(s) {
    if (!s) return '';
    return s.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}

// ============================================================
// MODAL HTML + CSS INJECTION
// ============================================================

function _createFolderBrowserModal() {
    // Inject CSS
    if (!document.getElementById('fb-styles')) {
        const style = document.createElement('style');
        style.id = 'fb-styles';
        style.textContent = `
            #folderBrowserModal {
                z-index: 60;
            }
            .fb-container {
                max-height: 500px;
                overflow-y: auto;
                border: 1px solid #374151;
                border-radius: 6px;
                background: #111827;
            }
            .fb-entry {
                display: flex;
                align-items: center;
                padding: 8px 12px;
                border-bottom: 1px solid #1f2937;
                cursor: default;
                font-size: 14px;
                gap: 8px;
            }
            .fb-entry:last-child {
                border-bottom: none;
            }
            .fb-dir, .fb-parent {
                cursor: pointer;
            }
            .fb-dir:hover, .fb-parent:hover {
                background: #1e3a5f;
            }
            .fb-icon {
                flex-shrink: 0;
                width: 20px;
                text-align: center;
            }
            .fb-name {
                flex: 1;
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
            }
            .fb-size {
                flex-shrink: 0;
                color: #6b7280;
                font-size: 12px;
                margin-left: 8px;
            }
            .fb-hint {
                flex-shrink: 0;
                color: #6b7280;
                font-size: 12px;
                font-style: italic;
            }
            .fb-video {
                color: #34d399;
            }
            .fb-file {
                color: #6b7280;
            }
            .fb-dir .fb-name {
                color: #93c5fd;
                font-weight: 500;
            }
            .fb-parent .fb-name {
                color: #9ca3af;
            }
            .fb-path-bar {
                background: #111827;
                border: 1px solid #374151;
                border-radius: 6px;
                padding: 8px 12px;
                font-family: monospace;
                font-size: 13px;
                color: #e5e7eb;
                word-break: break-all;
                min-height: 20px;
            }
            .fb-stats {
                font-size: 12px;
                color: #9ca3af;
                padding: 4px 0;
                min-height: 20px;
            }
            .fb-manual-input {
                background: #111827;
                border: 1px solid #374151;
                border-radius: 6px;
                padding: 6px 10px;
                font-family: monospace;
                font-size: 13px;
                color: #e5e7eb;
                width: 100%;
            }
            .fb-manual-input:focus {
                outline: none;
                border-color: #3b82f6;
            }
        `;
        document.head.appendChild(style);
    }

    // Inject modal HTML
    const modal = document.createElement('div');
    modal.id = 'folderBrowserModal';
    modal.className = 'hidden fixed inset-0 bg-black bg-opacity-60 flex items-center justify-center';
    modal.innerHTML = `
        <div class="bg-gray-800 rounded-lg p-6 w-full max-w-2xl shadow-2xl" style="max-height: 90vh;">
            <div class="flex justify-between items-center mb-4">
                <h3 class="text-lg font-bold text-white">Browse Folders</h3>
                <button onclick="_closeFolderBrowser()" class="text-gray-400 hover:text-white text-xl">✕</button>
            </div>

            <!-- Current path display -->
            <div class="mb-2">
                <div id="fb-current-path" class="fb-path-bar">Loading...</div>
            </div>

            <!-- Stats line -->
            <div id="fb-stats" class="fb-stats"></div>

            <!-- Directory listing -->
            <div id="fb-listing" class="fb-container mb-4">
                <div class="text-center text-gray-400 py-8">Loading...</div>
            </div>

            <!-- Manual path input -->
            <div class="mb-4">
                <label class="block text-xs text-gray-400 mb-1">Or type a path directly:</label>
                <div class="flex gap-2">
                    <input type="text" id="fb-manual-path" class="fb-manual-input"
                        placeholder="D:\\Media\\Movies or /mnt/media/movies">
                    <button onclick="_browseManualPath()" class="bg-gray-600 hover:bg-gray-500 px-3 py-1 rounded text-sm whitespace-nowrap">
                        Go
                    </button>
                </div>
            </div>

            <!-- Action buttons -->
            <div class="flex justify-end gap-3">
                <button onclick="_closeFolderBrowser()"
                    class="bg-gray-700 hover:bg-gray-600 px-4 py-2 rounded text-sm">
                    Cancel
                </button>
                <button id="fb-select-btn" onclick="_selectCurrentFolder()"
                    class="bg-blue-600 hover:bg-blue-700 px-4 py-2 rounded text-sm font-medium"
                    disabled>
                    Select This Folder
                </button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);

    // Handle Enter key in manual input
    document.getElementById('fb-manual-path').addEventListener('keydown', function(e) {
        if (e.key === 'Enter') {
            _browseManualPath();
        }
    });
}

function _browseManualPath() {
    const input = document.getElementById('fb-manual-path');
    const path = input.value.trim();
    if (path) {
        _browseTo(path);
    }
}


// ============================================================
// OVERRIDE: Replace the old handleFolderSelect that doesn't work
// ============================================================

// Prevent the old broken browse function from doing anything
function handleFolderSelect(event) {
    // Intentionally empty - old browser-based folder select is disabled
    // The server-side folder browser (openFolderBrowser) is used instead
    console.log('[Optimizarr] Browser folder select disabled. Using server-side browser.');
    openFolderBrowser();
}
