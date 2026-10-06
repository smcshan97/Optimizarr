// ============================================================
// STATISTICS DASHBOARD
// ============================================================

async function loadStatistics() {
    const days = document.getElementById('statsDays').value;

    try {
        const data = await apiRequest(`/stats/dashboard?days=${days}`);
        if (!data) return;

        const t = data.totals;
        document.getElementById('statTotalFiles').textContent  = (t.total || 0).toLocaleString();
        document.getElementById('statOriginalSize').textContent = formatSize(t.total_original || 0);
        document.getElementById('statNewSize').textContent      = formatSize(t.total_new || 0);
        document.getElementById('statSaved').textContent        = formatSize(t.total_saved || 0);
        document.getElementById('statAvgPct').textContent       = (t.avg_savings_pct || 0).toFixed(1) + '%';

        // Avg encode time
        const avgSec = t.avg_encode_seconds || 0;
        const avgTimeEl = document.getElementById('statAvgTime');
        if (avgTimeEl) {
            if (avgSec === 0) {
                avgTimeEl.textContent = '—';
            } else if (avgSec < 60) {
                avgTimeEl.textContent = Math.round(avgSec) + 's';
            } else if (avgSec < 3600) {
                avgTimeEl.textContent = Math.round(avgSec / 60) + 'm';
            } else {
                avgTimeEl.textContent = (avgSec / 3600).toFixed(1) + 'h';
            }
        }

        // Avg encode speed (× realtime) — only present once history has duration data
        const speedEl = document.getElementById('statSpeed');
        if (speedEl) {
            const speed = t.avg_speed_x || 0;
            speedEl.textContent = speed > 0 ? speed.toFixed(1) + '× realtime' : '—';
        }

        renderDailyChart(data.daily);
        renderCodecBreakdown(data.codecs, t.total, data.encode_speed);
        renderRecentHistory(data.recent);

    } catch (err) {
        console.error('Failed to load statistics:', err);
    }

    loadHealth();
    loadUpscalers();
}

async function clearHistoryFileNames() {
    if (!confirm('This will remove all file names from the encoding history.\n\nAggregate stats (total saved, codec breakdown, daily charts) will be preserved.\n\nContinue?')) return;
    const result = await apiRequest('/stats/clear-names', { method: 'POST' });
    if (result) {
        showMessage(result.message, 'success');
        loadStatistics();
    }
}

async function purgeHistory() {
    if (!confirm('⚠️ This will DELETE all encoding history.\n\nTotal stats, daily charts, and codec breakdown will all reset to zero.\n\nThis cannot be undone. Continue?')) return;
    const result = await apiRequest('/stats/purge', { method: 'POST' });
    if (result) {
        showMessage(result.message, 'success');
        loadStatistics();
    }
}

function renderDailyChart(daily) {
    const container = document.getElementById('dailyChart');
    
    if (!daily || daily.length === 0) {
        container.innerHTML = '<p class="text-gray-500 text-sm">No encoding history yet. Process some files to see activity.</p>';
        return;
    }
    
    const maxFiles = Math.max(...daily.map(d => d.files), 1);
    
    container.innerHTML = daily.map(d => {
        const height = Math.max((d.files / maxFiles) * 100, 4);
        const date = new Date(d.date);
        const label = `${date.getMonth()+1}/${date.getDate()}`;
        const savedMB = (d.saved / (1024*1024)).toFixed(0);
        
        return `
            <div class="flex flex-col items-center flex-shrink-0" style="min-width: 30px" title="${d.date}: ${d.files} files, ${savedMB}MB saved">
                <div class="text-xs text-gray-400 mb-1">${d.files}</div>
                <div class="w-6 bg-blue-500 rounded-t transition-all" style="height: ${height}%"></div>
                <div class="text-xs text-gray-500 mt-1 whitespace-nowrap">${label}</div>
            </div>
        `;
    }).join('');
}

function renderCodecBreakdown(codecs, total, encodeSpeed) {
    const container = document.getElementById('codecBreakdown');

    if (!codecs || codecs.length === 0) {
        container.innerHTML = '<p class="text-gray-500 text-sm">No data yet</p>';
        return;
    }

    const colors = ['bg-blue-500', 'bg-green-500', 'bg-purple-500', 'bg-yellow-500', 'bg-red-500', 'bg-cyan-500'];
    // by_codec stores encode-seconds per video-second; invert for "× realtime"
    const speedRatios = (encodeSpeed && encodeSpeed.by_codec) || {};

    container.innerHTML = codecs.map((c, i) => {
        const pct = total > 0 ? ((c.count / total) * 100).toFixed(1) : 0;
        const savedMB = (c.saved / (1024*1024)).toFixed(0);
        const ratio = speedRatios[c.codec];
        const speedTag = ratio > 0
            ? ` · <span class="text-cyan-400" title="Average encode speed for this codec (all history)">${(1 / ratio).toFixed(1)}× realtime</span>`
            : '';
        return `
            <div>
                <div class="flex justify-between text-sm mb-1">
                    <span>${(c.codec || 'unknown').toUpperCase()}</span>
                    <span class="text-gray-400">${c.count} files (${pct}%) — ${savedMB}MB saved${speedTag}</span>
                </div>
                <div class="w-full bg-gray-600 rounded h-2">
                    <div class="${colors[i % colors.length]} rounded h-2" style="width: ${pct}%"></div>
                </div>
            </div>
        `;
    }).join('');
}

function renderRecentHistory(recent) {
    const container = document.getElementById('recentHistory');

    if (!recent || recent.length === 0) {
        container.innerHTML = '<p class="text-gray-500 text-sm">No encoding history yet. Process some files to see activity.</p>';
        return;
    }

    container.innerHTML = recent.map(h => {
        const filename = h.file_path === 'cleared' ? '(cleared)' : h.file_path.split(/[/\\]/).pop();
        const savedPct = h.original_size_bytes > 0
            ? ((h.savings_bytes / h.original_size_bytes) * 100).toFixed(1)
            : '0.0';
        const savings_positive = parseFloat(savedPct) > 0;

        const secs = Math.round(h.encoding_time_seconds || 0);
        let timeStr;
        if (secs <= 0)       timeStr = '—';
        else if (secs < 60)  timeStr = `${secs}s`;
        else if (secs < 3600) timeStr = `${Math.floor(secs/60)}m ${secs%60}s`;
        else                  timeStr = `${Math.floor(secs/3600)}h ${Math.floor((secs%3600)/60)}m`;

        const codec     = (h.codec     || '—').toUpperCase();
        const container_fmt = (h.container || '—').toUpperCase();
        const dateStr   = h.completed_at ? h.completed_at.split('T')[0].replace(/-/g, '/').slice(2) : '—';

        return `
            <div class="p-3 bg-gray-700 rounded-lg hover:bg-gray-650 transition-colors">
                <div class="flex justify-between items-start gap-2">
                    <span class="text-sm text-gray-100 truncate flex-1" title="${_esc(h.file_path)}">${_esc(filename)}</span>
                    <span class="text-xs ${savings_positive ? 'text-green-400' : 'text-gray-400'} flex-shrink-0 font-medium">
                        ${savings_positive ? '↓' : ''}${savedPct}%
                    </span>
                </div>
                <div class="flex gap-3 mt-1 text-xs text-gray-500">
                    <span>${formatSize(h.original_size_bytes || 0)} → ${formatSize(h.new_size_bytes || 0)}</span>
                    <span class="text-gray-600">•</span>
                    <span>${codec}${container_fmt !== '—' ? ' / ' + container_fmt : ''}</span>
                    <span class="text-gray-600">•</span>
                    <span>⏱ ${timeStr}</span>
                    <span class="ml-auto">${dateStr}</span>
                </div>
            </div>
        `;
    }).join('');
}

async function loadHealth() {
    const container = document.getElementById('healthStatus');
    try {
        const h = await apiRequest('/health');
        if (!h) return;

        const ok  = (v) => `<span class="font-bold text-green-400">● ${v}</span>`;
        const err = (v) => `<span class="font-bold text-red-400">✗ ${v}</span>`;
        const warn= (v) => `<span class="font-bold text-yellow-400">⚠ ${v}</span>`;
        const sub = (v) => `<div class="text-xs text-gray-500 mt-0.5 truncate">${v}</div>`;

        const card = (title, body) => `
            <div class="p-4 bg-gray-700 rounded-lg">
                <div class="text-xs text-gray-400 font-medium uppercase tracking-wider mb-2">${title}</div>
                ${body}
            </div>`;

        // Service card
        const overall = h.status === 'ok' ? ok('Healthy') : h.status === 'warning' ? warn('Warning') : err('Degraded');
        let cards = card('Service', `${overall}${sub('v' + (h.version || '?'))}`);

        // HandBrakeCLI
        cards += card('HandBrakeCLI',
            (h.handbrake?.installed ? ok('Installed') : err('Not Found')) +
            sub(h.handbrake?.path || 'not in PATH'));

        // ffprobe
        cards += card('FFprobe',
            (h.ffprobe?.installed ? ok('Installed') : err('Not Found')) +
            sub(h.ffprobe?.path || 'not in PATH'));

        // ffmpeg
        cards += card('FFmpeg',
            (h.ffmpeg?.installed ? ok('Installed') : err('Not Found')) +
            sub(h.ffmpeg?.path || 'not in PATH'));

        // Database
        cards += card('Database',
            (h.database?.status === 'ok' ? ok('OK') : err('Error')) +
            sub(`${h.database?.profiles||0} profiles · ${h.database?.queue_items||0} queued · ${h.database?.history_records||0} history`));

        // Scheduler
        if (h.scheduler) {
            cards += card('Scheduler',
                (h.scheduler.enabled ? (h.scheduler.running ? ok('Active') : warn('Enabled, not running')) : '<span class="text-gray-400">Disabled</span>') +
                sub(h.scheduler.next_window || ''));
        }

        // Upscalers
        if (h.upscalers && Object.keys(h.upscalers).length > 0) {
            const upLines = Object.entries(h.upscalers).map(([k, v]) =>
                `<div class="flex justify-between text-xs mt-1"><span class="text-gray-300">${k}</span>${v.installed ? ok('✓') : '<span class="text-gray-500">—</span>'}</div>`
            ).join('');
            cards += card('AI Upscalers', upLines);
        }

        // Queue summary
        if (h.queue) {
            const q = h.queue;
            cards += card('Queue',
                `<div class="text-xs space-y-0.5">
                    ${q.processing ? `<div class="text-blue-400">⚙ Processing: ${q.processing}</div>` : ''}
                    <div class="text-gray-300">Pending: ${q.pending}</div>
                    <div class="text-green-400">Completed: ${q.completed}</div>
                    ${q.failed ? `<div class="text-red-400">Failed: ${q.failed}</div>` : ''}
                </div>`);
        }

        // Disk — global
        if (h.disk) {
            const pct = h.disk.percent_used;
            cards += card('App Disk',
                (pct > 90 ? err(`${h.disk.free_gb} GB free`) : ok(`${h.disk.free_gb} GB free`)) +
                sub(`${pct}% used of ${h.disk.total_gb} GB`));
        }

        // Disk per scan root
        if (h.scan_root_disk && h.scan_root_disk.length > 0) {
            const rootLines = h.scan_root_disk.map(d => {
                const name = d.path.split(/[/\\]/).pop() || d.path;
                if (d.status === 'missing') return `<div class="flex justify-between text-xs mt-1"><span class="text-gray-400 truncate">${_esc(name)}</span>${err('Missing')}</div>`;
                if (d.status === 'error')   return `<div class="flex justify-between text-xs mt-1"><span class="text-gray-400 truncate">${_esc(name)}</span>${err('Error')}</div>`;
                const free = d.free_gb;
                const pct  = d.percent_used;
                return `<div class="flex justify-between text-xs mt-1" title="${_esc(d.path)}">
                    <span class="text-gray-300 truncate max-w-[120px]">${_esc(name)}</span>
                    <span class="${pct > 90 ? 'text-red-400' : 'text-green-400'}">${free} GB free</span>
                </div>`;
            }).join('');
            cards += card('Library Disk', rootLines);
        }

        container.innerHTML = `<div class="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-4">${cards}</div>`;

    } catch (err) {
        container.innerHTML = '<p class="text-red-400 text-sm">Failed to load health status</p>';
    }
}

