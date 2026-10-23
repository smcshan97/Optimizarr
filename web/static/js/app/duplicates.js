// ============================================================
// DUPLICATE FINDER
// ============================================================

let _dupeStatusTimer = null;

async function startDupeScan(mode) {
    if (mode === 'full' && !confirm(
        'A full scan decodes every video in your library to fingerprint it.\n\n' +
        'This can take many hours (or days) for a large library. Progress is saved ' +
        'as it goes — you can stop any time and resume later.\n\nStart full scan?')) {
        return;
    }
    const result = await apiRequest('/duplicates/scan', {
        method: 'POST',
        body: JSON.stringify({ mode: mode || 'full' }),
    });
    if (result) {
        showMessage(result.message, result.success === false ? 'warning' : 'info');
        if (result.success !== false) _pollDupeStatus();
    }
}

async function cancelDupeScan() {
    const result = await apiRequest('/duplicates/scan/cancel', { method: 'POST' });
    if (result) showMessage(result.message, result.success === false ? 'warning' : 'info');
}

function _pollDupeStatus() {
    if (_dupeStatusTimer) clearInterval(_dupeStatusTimer);
    _dupeStatusTimer = setInterval(async () => {
        const s = await apiRequest('/duplicates/status');
        if (!s) return;
        const el = document.getElementById('dupeScanStatus');
        const btn = document.getElementById('dupeScanBtn');
        const cancelBtn = document.getElementById('dupeCancelBtn');
        if (s.status === 'running') {
            if (el) {
                el.style.display = 'block';
                const pct = s.total > 0 ? ` (${s.done}/${s.total})` : '';
                let eta = '';
                if (s.phase === 'fingerprinting' && s.done > 0 && s.started_at) {
                    const elapsed = (Date.now() / 1000) - s.started_at;
                    const rate = s.done / elapsed;
                    if (rate > 0 && s.total > s.done) {
                        eta = ` · ~${formatDuration((s.total - s.done) / rate)} left`;
                    }
                }
                el.textContent = `⏳ ${s.phase}${pct}${eta} — ${s.message || ''}`;
            }
            if (btn) btn.disabled = true;
            const qb = document.getElementById('dupeQuickBtn');
            if (qb) qb.disabled = true;
            if (cancelBtn) cancelBtn.style.display = '';
        } else {
            clearInterval(_dupeStatusTimer);
            _dupeStatusTimer = null;
            if (btn) btn.disabled = false;
            const qb2 = document.getElementById('dupeQuickBtn');
            if (qb2) qb2.disabled = false;
            if (cancelBtn) cancelBtn.style.display = 'none';
            if (el) {
                el.style.display = 'block';
                const icon = { complete: '✅', error: '❌', cancelled: '⏹️' }[s.status] || '';
                el.textContent = icon ? `${icon} ${s.message}` : '';
            }
            if (s.status === 'complete') loadDuplicates();
        }
    }, 2000);
}

const DUPE_KIND_LABELS = {
    'exact':     { label: 'Exact copy',   color: 'var(--danger)',  hint: 'Byte-identical file' },
    'duplicate': { label: 'Duplicate',    color: 'var(--danger)',  hint: 'Same video, different encode' },
    'near':      { label: 'Near dupe',    color: 'var(--warning)', hint: 'Same video, slightly trimmed or altered' },
    'clip':      { label: 'Clip of',      color: 'var(--info)',    hint: 'Segment of the longer video' },
    'partial':   { label: 'Partial',      color: 'var(--text-muted)', hint: 'Sections overlap' },
};

async function loadDuplicates() {
    const data = await apiRequest('/duplicates');
    const container = document.getElementById('dupeResults');
    if (!container) return;
    // Resume live status display if a scan is running
    const s = await apiRequest('/duplicates/status');
    if (s && s.status === 'running') _pollDupeStatus();

    if (!data || !data.groups || data.groups.length === 0) {
        container.innerHTML = '<p style="color:var(--text-muted)">No duplicates found. Run a scan to (re)check the library.</p>';
        return;
    }

    let html = `<p style="font-size:12px;color:var(--text-secondary);margin-bottom:10px">
        ${data.total_groups} group(s) — <strong style="color:var(--success)">${formatSize(data.reclaimable_bytes)}</strong>
        reclaimable from exact/duplicate copies</p>`;

    data.groups.forEach(g => {
        const nameA = _esc(g.file_a.split(/[/\\]/).pop());
        html += `
        <div style="border:1px solid var(--border);border-radius:8px;padding:10px 12px;margin-bottom:10px">
            <div style="font-size:13px;margin-bottom:6px" title="${_esc(g.file_a)}">
                📁 <strong>${nameA}</strong>
                <span style="color:var(--text-muted);font-size:11px">
                    ${formatSize(g.size_a)}${g.duration_a ? ' · ' + formatDuration(g.duration_a) : ''} · keep
                </span>
            </div>`;
        g.matches.forEach(m => {
            const k = DUPE_KIND_LABELS[m.kind] || DUPE_KIND_LABELS['partial'];
            const nameB = _esc(m.file_b.split(/[/\\]/).pop());
            const clipInfo = m.kind === 'clip'
                ? ` · matches at ${formatDuration(m.offset_seconds)} for ${formatDuration(m.overlap_seconds)}`
                : '';
            html += `
            <div style="display:flex;align-items:center;gap:8px;padding:4px 0 4px 20px;font-size:12px">
                <span style="color:${k.color};font-weight:600;min-width:80px" title="${k.hint}">${k.label}</span>
                <span style="color:var(--text-muted)">${m.score.toFixed(0)}%</span>
                <span class="truncate" style="flex:1" title="${_esc(m.file_b)}">${nameB}</span>
                <span style="color:var(--text-muted);white-space:nowrap">${formatSize(m.size_b)}${clipInfo}</span>
                <button class="btn btn-danger btn-xs" data-path="${_esc(m.file_b)}" onclick="deleteDupeFile(this, this.dataset.path)">Delete</button>
            </div>`;
        });
        html += '</div>';
    });
    container.innerHTML = html;
}

async function deleteDupeFile(btn, filePath) {
    if (!confirm(`Permanently delete this file from disk?\n\n${filePath}\n\nThis cannot be undone.`)) return;
    btn.disabled = true;
    const result = await apiRequest('/duplicates/delete-file', {
        method: 'POST',
        body: JSON.stringify({ file_path: filePath }),
    });
    if (result) {
        showMessage(result.message, result.success === false ? 'error' : 'success');
        if (result.success !== false) loadDuplicates();
        else btn.disabled = false;
    } else {
        btn.disabled = false;
    }
}

