// ============================================================
// AI UPSCALE SETTINGS — Scan Root Modal
// ============================================================

const _UPSCALER_MODELS = {
    realesrgan: ['realesrgan-x4plus', 'realesrgan-x4plus-anime', 'realesr-animevideov3'],
    realcugan:  ['models-se', 'models-pro', 'models-nose'],
    waifu2x:    ['models-cunet', 'models-upconv_7_anime_style_art_rgb'],
    // RTX VSR "models" are quality levels; HIGHBITRATE_* suit clean sources
    rtxvsr:     ['ULTRA', 'HIGH', 'MEDIUM', 'LOW', 'HIGHBITRATE_ULTRA', 'HIGHBITRATE_HIGH'],
};

function toggleUpscaleSection() {
    const section = document.getElementById('upscaleSection');
    const icon    = document.getElementById('upscaleSectionToggleIcon');
    const hidden  = section.classList.toggle('hidden');
    icon.textContent = hidden ? '▶' : '▼';
}

function handleUpscaleToggle() {
    const enabled = document.getElementById('scanRootUpscaleEnabled').checked;
    const fields  = document.getElementById('upscaleSettingsFields');
    const badge   = document.getElementById('upscaleEnabledBadge');
    fields.classList.toggle('opacity-50',       !enabled);
    fields.classList.toggle('pointer-events-none', !enabled);
    badge.classList.toggle('hidden', !enabled);
}

function handleUpscalerKeyChange() {
    const key     = document.getElementById('scanRootUpscaleKey').value;
    const models  = _UPSCALER_MODELS[key] || [];
    const sel     = document.getElementById('scanRootUpscaleModel');
    sel.innerHTML = models.map((m, i) => `<option value="${m}" ${i === 0 ? 'selected' : ''}>${m}</option>`).join('');
}

// Call once on page load to set initial model list
handleUpscalerKeyChange();

function _fillUpscaleFields(root) {
    const ue = document.getElementById('scanRootUpscaleEnabled');
    if (!ue) return;
    ue.checked = !!root.upscale_enabled;

    const triggerEl = document.getElementById('scanRootUpscaleTrigger');
    const targetEl  = document.getElementById('scanRootUpscaleTarget');
    const keyEl     = document.getElementById('scanRootUpscaleKey');
    const factorEl  = document.getElementById('scanRootUpscaleFactor');

    if (triggerEl) triggerEl.value = root.upscale_trigger_below ?? 720;
    if (targetEl)  targetEl.value  = root.upscale_target_height  ?? 1080;
    if (keyEl)     keyEl.value     = root.upscale_key            ?? 'realesrgan';
    if (factorEl)  factorEl.value  = root.upscale_factor         ?? 2;

    // Populate model dropdown for the selected key then set saved model
    handleUpscalerKeyChange();
    const modelEl = document.getElementById('scanRootUpscaleModel');
    if (modelEl && root.upscale_model) modelEl.value = root.upscale_model;

    // Keep fields enabled/disabled in sync
    handleUpscaleToggle();
}

// ============================================================
// PROFILE UPSCALE FUNCTIONS
// ============================================================

function toggleProfileUpscaleSection() {
    const section = document.getElementById('profileUpscaleSection');
    const icon    = document.getElementById('profileUpscaleToggleIcon');
    if (!section) return;
    const hidden = section.classList.toggle('hidden');
    if (icon) icon.textContent = hidden ? '▶' : '▼';
}

function handleProfileUpscaleToggle() {
    const enabled = document.getElementById('profileUpscaleEnabled')?.checked;
    const fields  = document.getElementById('profileUpscaleFields');
    const badge   = document.getElementById('profileUpscaleEnabledBadge');
    if (!fields) return;
    fields.classList.toggle('opacity-50',          !enabled);
    fields.classList.toggle('pointer-events-none', !enabled);
    if (badge) badge.classList.toggle('hidden', !enabled);
}

function handleProfileUpscalerKeyChange() {
    const key    = document.getElementById('profileUpscaleKey')?.value;
    const models = _UPSCALER_MODELS[key] || [];
    const sel    = document.getElementById('profileUpscaleModel');
    if (!sel) return;
    sel.innerHTML = models.map((m, i) =>
        `<option value="${_esc(m)}" ${i === 0 ? 'selected' : ''}>${_esc(m)}</option>`
    ).join('');
}

function _fillProfileUpscaleFields(profile) {
    const ue = document.getElementById('profileUpscaleEnabled');
    if (!ue) return;

    ue.checked = !!profile.upscale_enabled;

    const triggerEl = document.getElementById('profileUpscaleTrigger');
    const targetEl  = document.getElementById('profileUpscaleTarget');
    const keyEl     = document.getElementById('profileUpscaleKey');
    const factorEl  = document.getElementById('profileUpscaleFactor');

    if (triggerEl) triggerEl.value = profile.upscale_trigger_below ?? 720;
    if (targetEl)  targetEl.value  = profile.upscale_target_height  ?? 1080;
    if (keyEl)     keyEl.value     = profile.upscale_key            ?? 'realesrgan';
    if (factorEl)  factorEl.value  = profile.upscale_factor         ?? 2;

    handleProfileUpscalerKeyChange();
    const modelEl = document.getElementById('profileUpscaleModel');
    if (modelEl && profile.upscale_model) modelEl.value = profile.upscale_model;

    handleProfileUpscaleToggle();
}

// ============================================================
// PROFILE STEREO FUNCTIONS
// ============================================================

function toggleProfileStereoSection() {
    const section = document.getElementById('profileStereoSection');
    const icon    = document.getElementById('profileStereoToggleIcon');
    if (!section) return;
    const hidden = section.classList.toggle('hidden');
    if (icon) icon.textContent = hidden ? '▶' : '▼';
}

function handleProfileStereoToggle() {
    const enabled = document.getElementById('profileStereoEnabled')?.checked;
    const fields  = document.getElementById('profileStereoFields');
    const badge   = document.getElementById('profileStereoEnabledBadge');
    if (!fields) return;
    fields.classList.toggle('opacity-50',          !enabled);
    fields.classList.toggle('pointer-events-none', !enabled);
    if (badge) badge.classList.toggle('hidden', !enabled);
}

function _fillProfileStereoFields(profile) {
    const el = document.getElementById('profileStereoEnabled');
    if (!el) return;
    el.checked = !!profile.stereo_enabled;

    const modeEl = document.getElementById('profileStereoMode');
    const fmtEl  = document.getElementById('profileStereoFormat');
    const divEl  = document.getElementById('profileStereoDivergence');
    const conEl  = document.getElementById('profileStereoConvergence');
    const depEl  = document.getElementById('profileStereoDepthModel');

    if (modeEl) modeEl.value = profile.stereo_mode       || '2d_to_3d';
    if (fmtEl)  fmtEl.value  = profile.stereo_format     || 'half_sbs';
    if (divEl)  divEl.value  = profile.stereo_divergence  ?? 2.0;
    if (conEl)  conEl.value  = profile.stereo_convergence ?? 0.5;
    if (depEl)  depEl.value  = profile.stereo_depth_model || 'Any_V2_S';

    handleProfileStereoToggle();
}

// ============================================================
// SCAN ROOT STEREO FUNCTIONS
// ============================================================

function toggleScanRootStereoSection() {
    const section = document.getElementById('scanRootStereoSection');
    const icon    = document.getElementById('scanRootStereoToggleIcon');
    if (!section) return;
    const hidden = section.classList.toggle('hidden');
    if (icon) icon.textContent = hidden ? '▶' : '▼';
}

function handleScanRootStereoToggle() {
    const enabled = document.getElementById('scanRootStereoEnabled')?.checked;
    const fields  = document.getElementById('scanRootStereoFields');
    const badge   = document.getElementById('scanRootStereoEnabledBadge');
    if (!fields) return;
    fields.classList.toggle('opacity-50',          !enabled);
    fields.classList.toggle('pointer-events-none', !enabled);
    if (badge) badge.classList.toggle('hidden', !enabled);
}

function _fillScanRootStereoFields(root) {
    const el = document.getElementById('scanRootStereoEnabled');
    if (!el) return;
    el.checked = !!root.stereo_enabled;

    const modeEl = document.getElementById('scanRootStereoMode');
    const fmtEl  = document.getElementById('scanRootStereoFormat');
    const divEl  = document.getElementById('scanRootStereoDivergence');
    const conEl  = document.getElementById('scanRootStereoConvergence');
    const depEl  = document.getElementById('scanRootStereoDepthModel');

    if (modeEl) modeEl.value = root.stereo_mode       || '2d_to_3d';
    if (fmtEl)  fmtEl.value  = root.stereo_format     || 'half_sbs';
    if (divEl)  divEl.value  = root.stereo_divergence  ?? 2.0;
    if (conEl)  conEl.value  = root.stereo_convergence ?? 0.5;
    if (depEl)  depEl.value  = root.stereo_depth_model || 'Any_V2_S';

    handleScanRootStereoToggle();
}

// ============================================================
// CONNECTION TYPE CHANGE — update hints & privacy notice
// ============================================================

function handleConnectionTypeChange() {
    const type       = document.getElementById('connectionAppType')?.value || 'radarr';
    const urlInput   = document.getElementById('connectionUrl');
    const urlHint    = document.getElementById('connectionUrlHint');
    const keyHint    = document.getElementById('connectionKeyHint');
    const privNote   = document.getElementById('connectionPrivacyNote');
    const statsCheck = document.getElementById('connectionShowInStats');

    const hints = {
        radarr: {
            placeholder: 'http://localhost:7878',
            urlHint:     'Radarr default: :7878 · Sonarr default: :8989',
            keyHint:     'Settings → General → API Key in Radarr',
        },
        sonarr: {
            placeholder: 'http://localhost:8989',
            urlHint:     'Sonarr default: :8989 · Radarr default: :7878',
            keyHint:     'Settings → General → API Key in Sonarr',
        },
        stash: {
            placeholder: 'http://localhost:9999',
            urlHint:     'Stash default port: :9999',
            keyHint:     'Stash → Settings → Security → API Key',
        },
    };

    const h = hints[type] || hints.radarr;
    if (urlInput)   urlInput.placeholder = h.placeholder;
    if (urlHint)    urlHint.textContent  = h.urlHint;
    if (keyHint)    keyHint.textContent  = h.keyHint;

    // Show/hide Stash privacy notice
    if (privNote) {
        if (type === 'stash') {
            privNote.classList.remove('hidden');
            // Default: hide Stash from stats for privacy
            if (statsCheck && !_connectionEditId) statsCheck.checked = false;
        } else {
            privNote.classList.add('hidden');
        }
    }
}
