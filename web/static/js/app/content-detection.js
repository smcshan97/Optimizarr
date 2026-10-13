// ============================================================
// CONTENT DETECTION (CLIP classifier) + the 50-question check
// ============================================================

const _CT_LABEL = { '2d': '🎨 2D', '3d': '🧊 3D', live: '🎥 Live', uncertain: '❔ Unsure' };
let _quiz = { questions: [], index: 0, agree: 0, disagree: 0, unsure: 0, answered: 0, url: null };

function _pct(v) { return v == null ? '—' : `${Math.round(v * 100)}%`; }

async function loadClassifier() {
    const el = document.getElementById('classifierStatus');
    if (!el) return;
    const s = await apiRequest('/classifier/status');
    if (!s) { el.textContent = 'Could not load content detection status'; return; }
    const settings = document.getElementById('classifierSettings');
    const quizBtn = document.getElementById('classifierQuizBtn');
    const inst = s.install_state || {};
    if (!s.installed) {
        settings.classList.add('hidden');
        quizBtn.disabled = true;
        el.innerHTML = inst.status === 'downloading'
            ? `⏳ ${_esc(inst.message || 'Installing…')} (${inst.progress || 0}%)`
            : `${inst.status === 'error' ? `<p class="text-red-400 text-xs mb-2">${_esc(inst.error || '')}</p>` : ''}
               Not installed. <button class="btn btn-primary btn-sm ml-2" onclick="installClassifier()">⬇ Install (~160 MB)</button>`;
        if (inst.status === 'downloading') setTimeout(loadClassifier, 2000);
        return;
    }
    quizBtn.disabled = false;
    settings.classList.remove('hidden');
    const c = s.coverage || {}, by = c.by_type || {};
    const done = (c.pending || 0) - (by.unclassified || 0);
    const t = s.self_test;
    el.innerHTML = `
        ✓ Installed (${_esc(s.installed.model || '')}).
        Classified <strong>${done}</strong> of ${c.pending || 0} pending files —
        ${['2d', '3d', 'live', 'uncertain'].map(k => `${_CT_LABEL[k]} ${by[k] || 0}`).join(' · ')}.
        ${c.labels ? `You've labelled ${c.labels}.` : ''}
        ${t ? (t.ok ? `<span class="text-green-400 text-xs ml-1">Self-test passed</span>`
                    : `<span class="text-xs ml-1" style="color:var(--warning)">⚠ Self-test failed: ${_esc(t.message)}</span>`) : ''}`;
    document.getElementById('classifierEnabled').checked = s.detection_setting;
    document.getElementById('classifierThreshold').value = Math.round(s.threshold * 100);
    document.getElementById('classifierFilmGrain').value = s.auto_film_grain_live || 0;
    for (const [id, val] of [['classifierAutoAnimated', s.auto_upscaler_animated],
                             ['classifierAutoLive', s.auto_upscaler_live]]) {
        const sel = document.getElementById(id);
        sel.innerHTML = s.upscaler_choices.map(ch =>
            `<option value="${ch}" ${ch === val ? 'selected' : ''}>${ch.replace(':', ' — ')}</option>`).join('');
    }
}

async function installClassifier() {
    const r = await apiRequest('/classifier/install', { method: 'POST' });
    if (r) setTimeout(loadClassifier, 500);
}

async function saveClassifierSettings() {
    const r = await apiRequest('/classifier/settings', {
        method: 'POST',
        body: JSON.stringify({
            enabled: document.getElementById('classifierEnabled').checked,
            threshold: (parseFloat(document.getElementById('classifierThreshold').value) || 60) / 100,
            auto_upscaler_animated: document.getElementById('classifierAutoAnimated').value,
            auto_upscaler_live: document.getElementById('classifierAutoLive').value,
            auto_film_grain_live: parseInt(document.getElementById('classifierFilmGrain').value) || 0,
        }),
    });
    if (r) { showMessage('✓ Content detection settings saved', 'success'); loadClassifier(); }
}

// ── Quiz ──────────────────────────────────────────────────────────────────

async function startClassifierQuiz() {
    const r = await apiRequest('/classifier/quiz?count=50');
    if (!r) return;
    if (!r.questions.length) {
        showMessage('No classified files to ask about yet — the background check classifies a few each pass', 'warning');
        return;
    }
    _quiz = { questions: r.questions, index: 0, agree: 0, disagree: 0, unsure: 0, answered: 0, url: null };
    document.getElementById('quizSummary').classList.add('hidden');
    document.getElementById('quizQuestion').classList.remove('hidden');
    document.getElementById('classifierQuizModal').classList.remove('hidden');
    document.addEventListener('keydown', _quizKeys);
    _showQuizQuestion();
}

function closeClassifierQuiz() {
    document.getElementById('classifierQuizModal').classList.add('hidden');
    document.removeEventListener('keydown', _quizKeys);
    if (_quiz.url) URL.revokeObjectURL(_quiz.url);
    loadClassifier();
}

function _quizKeys(e) {
    const map = { '1': '2d', '2': '3d', '3': 'live', s: 'skip', S: 'skip' };
    if (map[e.key] && !document.getElementById('quizQuestion').classList.contains('hidden')) answerQuiz(map[e.key]);
}

async function _showQuizQuestion() {
    const q = _quiz.questions[_quiz.index];
    document.getElementById('quizProgress').textContent = `${_quiz.index + 1} / ${_quiz.questions.length}`;
    document.getElementById('quizFile').textContent = q.name;
    const img = document.getElementById('quizStrip');
    img.style.display = 'none';
    document.getElementById('quizLoading').textContent = 'Loading frames…';
    // The image needs the auth header, so fetch it as a blob
    try {
        const resp = await fetch(`${API_BASE}/classifier/quiz/${q.id}/strip`,
                                 { headers: { Authorization: `Bearer ${getToken()}` } });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        if (_quiz.url) URL.revokeObjectURL(_quiz.url);
        _quiz.url = URL.createObjectURL(await resp.blob());
        img.src = _quiz.url;
        img.style.display = '';
        document.getElementById('quizLoading').textContent = '';
    } catch (err) {
        document.getElementById('quizLoading').textContent = `Couldn't load frames (${err.message}) — press Skip`;
    }
}

async function answerQuiz(label) {
    const q = _quiz.questions[_quiz.index];
    if (!q) return;
    const r = await apiRequest('/classifier/quiz/answer', {
        method: 'POST', body: JSON.stringify({ queue_id: q.id, label }),
    });
    const fb = document.getElementById('quizFeedback');
    if (r && label !== 'skip') {
        _quiz.answered++;
        if (r.agrees === true) _quiz.agree++;
        else if (r.agrees === false) _quiz.disagree++;
        else _quiz.unsure++;
        const p = r.model_probs || {};
        fb.innerHTML = `Model said <strong>${_CT_LABEL[r.model] || '—'}</strong>
            (2D ${_pct(p['2d'])} · 3D ${_pct(p['3d'])} · live ${_pct(p.live)})
            ${r.agrees === true ? '<span class="text-green-400">— agrees</span>'
              : r.agrees === false ? '<span class="text-red-400">— disagrees (your answer is kept)</span>'
              : '<span class="text-gray-400">— it wasn\'t sure</span>'}`;
    } else {
        fb.textContent = '';
    }
    _quiz.index++;
    if (_quiz.index >= _quiz.questions.length) return _showQuizSummary();
    _showQuizQuestion();
}

async function _showQuizSummary() {
    document.getElementById('quizQuestion').classList.add('hidden');
    const box = document.getElementById('quizSummary');
    box.classList.remove('hidden');
    const s = await apiRequest('/classifier/quiz/summary');
    const decided = _quiz.agree + _quiz.disagree;
    const rows = ((s && s.table) || []).map(r => `
        <tr><td class="pr-4">${Math.round(r.threshold * 100)}%</td>
            <td class="pr-4">${_pct(r.coverage)}</td><td>${_pct(r.accuracy)}</td></tr>`).join('');
    const rec = s && s.recommended;
    box.innerHTML = `
        <h4 class="font-bold mb-2">This round</h4>
        <p class="text-sm">You labelled ${_quiz.answered}. Where the model was confident it agreed with you
           <strong>${decided ? Math.round(_quiz.agree / decided * 100) : 0}%</strong> of the time
           (${_quiz.agree} agree, ${_quiz.disagree} disagree); it was unsure on ${_quiz.unsure}.
           Your answers replace the model's for those files.</p>
        <h4 class="font-bold mt-4 mb-2">All your labels so far (${s ? s.labeled : 0})</h4>
        <table class="text-sm"><thead><tr class="text-gray-400"><th class="pr-4 text-left">Confidence needed</th>
            <th class="pr-4 text-left">Files decided</th><th class="text-left">Agrees with you</th></tr></thead>
            <tbody>${rows}</tbody></table>
        <p class="text-sm mt-3">Current setting: <strong>${s ? Math.round(s.threshold * 100) : '—'}%</strong>.
           ${rec != null
             ? `Recommended: <strong>${Math.round(rec * 100)}%</strong> (lowest that's ≥97% right on your labels).
                <button class="btn btn-primary btn-sm ml-2" onclick="applyQuizThreshold(${rec})">Use ${Math.round(rec * 100)}%</button>`
             : 'Label at least 30 files (or more where the model is right) for a recommendation.'}</p>
        <div class="mt-4"><button class="btn btn-secondary" onclick="closeClassifierQuiz()">Done</button></div>`;
}

async function applyQuizThreshold(th) {
    const r = await apiRequest('/classifier/settings', { method: 'POST', body: JSON.stringify({ threshold: th }) });
    if (r) showMessage(`✓ Confidence threshold set to ${Math.round(th * 100)}%`, 'success');
}
