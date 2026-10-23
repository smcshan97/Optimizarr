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
    // Delegates to _esc so double quotes are escaped too — the textContent
    // trick left " intact, which is unsafe inside HTML attributes.
    return _esc(text);
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


