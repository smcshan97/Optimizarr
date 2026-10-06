// ============================================================
// PROFILES MANAGEMENT
// ============================================================

let currentProfileId = null;

async function loadProfiles() {
    const profiles = await apiRequest('/profiles');
    const container = document.getElementById('profilesList');

    // Always keep the map fresh so queue rows can use it
    if (profiles && profiles.length > 0) {
        _profilesMap = {};
        profiles.forEach(p => { _profilesMap[p.id] = p; });
    }

    if (!profiles || profiles.length === 0) {
        container.innerHTML = '<p class="text-gray-400">No profiles yet. Create one to get started!</p>';
        return;
    }
    
    container.innerHTML = profiles.map(p => `
        <div class="bg-gray-700 rounded-lg p-4 mb-3">
            <div class="flex justify-between items-start">
                <div class="flex-1">
                    <div class="flex items-center gap-2">
                        <h3 class="font-semibold text-lg">${_esc(p.name)}</h3>
                        ${p.is_default ? '<span class="px-2 py-1 bg-blue-900 text-blue-300 text-xs rounded">DEFAULT</span>' : ''}
                    </div>
                    <div class="grid grid-cols-2 gap-2 mt-2 text-sm text-gray-300">
                        <div><span class="text-gray-400">Codec:</span> ${p.codec.toUpperCase()}</div>
                        <div><span class="text-gray-400">Encoder:</span> ${p.encoder}</div>
                        <div><span class="text-gray-400">Quality:</span> CRF ${p.quality}</div>
                        <div><span class="text-gray-400">Audio:</span> ${p.audio_codec.toUpperCase()}</div>
                        <div><span class="text-gray-400">Container:</span> ${(p.container || 'mkv').toUpperCase()}</div>
                        <div><span class="text-gray-400">Audio Mode:</span> ${getAudioLabel(p.audio_handling)}</div>
                        ${p.subtitle_handling && p.subtitle_handling !== 'none' ? `<div><span class="text-gray-400">Subtitles:</span> ${getSubtitleLabel(p.subtitle_handling)}</div>` : ''}
                        ${p.resolution ? `<div><span class="text-gray-400">Resolution:</span> ${p.resolution}</div>` : ''}
                        ${p.preset ? `<div><span class="text-gray-400">Preset:</span> ${p.preset}</div>` : ''}
                    </div>
                    <div class="flex gap-2 mt-2 flex-wrap">
                        ${p.enable_filters ? '<span class="px-2 py-0.5 bg-purple-900 text-purple-300 text-xs rounded">Filters</span>' : ''}
                        ${p.chapter_markers ? '<span class="px-2 py-0.5 bg-gray-600 text-gray-300 text-xs rounded">Chapters</span>' : ''}
                        ${p.hw_accel_enabled ? '<span class="px-2 py-0.5 bg-green-900 text-green-300 text-xs rounded">GPU</span>' : ''}
                        ${p.film_grain ? `<span class="px-2 py-0.5 bg-yellow-900 text-yellow-300 text-xs rounded">Grain ${p.film_grain}</span>` : ''}
                        ${p.target_vmaf ? `<span class="px-2 py-0.5 bg-amber-900 text-amber-300 text-xs rounded" title="Quality searched per file to reach this VMAF score">🎯 VMAF ${p.target_vmaf}</span>` : ''}
                        ${/_10bit$/.test(p.encoder || '') ? '<span class="px-2 py-0.5 bg-gray-600 text-gray-300 text-xs rounded">10-bit</span>' : ''}
                        ${p.upscale_enabled ? `<span class="px-2 py-0.5 bg-blue-900 text-blue-300 text-xs rounded" title="AI Upscale: ${p.upscale_key || 'realesrgan'} ×${p.upscale_factor || 2}, trigger below ${p.upscale_trigger_below || 720}px">🤖 Upscale ×${p.upscale_factor || 2}</span>` : ''}
                        ${p.stereo_enabled ? `<span class="px-2 py-0.5 bg-purple-900 text-purple-300 text-xs rounded" title="3D: ${p.stereo_mode || '2d_to_3d'}, ${p.stereo_format || 'half_sbs'}, divergence ${p.stereo_divergence || 2.0}">🎥 ${p.stereo_mode === '3d_to_2d' ? '3D→2D' : '2D→3D'}</span>` : ''}
                    </div>
                </div>
                <div class="flex gap-2">
                    <button onclick="editProfile(${p.id})" 
                        class="btn btn-primary btn-sm">
                        Edit
                    </button>
                    <button onclick="deleteProfile(${p.id}, '${p.name}')" 
                        class="btn btn-danger btn-sm">
                        Delete
                    </button>
                </div>
            </div>
        </div>
    `).join('');
}

function showCreateProfileForm() {
    currentProfileId = null;
    document.getElementById('profileModalTitle').textContent = 'Create Profile';
    document.getElementById('profileForm').reset();
    document.getElementById('profileId').value = '';
    document.getElementById('profileFramerateCustom').classList.add('hidden');

    // Reset Phase 2 fields to defaults
    document.getElementById('profileAudioHandling').value = 'preserve_all';
    document.getElementById('profileSubtitleHandling').value = 'none';
    document.getElementById('profileEnableFilters').checked = false;
    document.getElementById('profileChapterMarkers').checked = true;
    document.getElementById('profileHwAccel').checked = false;
    document.getElementById('profileContainer').value = 'mkv';
    _setFilmGrain(0);

    // Reset AI upscale section to defaults and collapse it
    _fillProfileUpscaleFields({
        upscale_enabled: false, upscale_trigger_below: 720, upscale_target_height: 1080,
        upscale_key: 'realesrgan', upscale_model: 'realesrgan-x4plus', upscale_factor: 2,
    });
    const upSec  = document.getElementById('profileUpscaleSection');
    const upIcon = document.getElementById('profileUpscaleToggleIcon');
    if (upSec && !upSec.classList.contains('hidden')) upSec.classList.add('hidden');
    if (upIcon) upIcon.textContent = '▶';

    // Reset stereo section to defaults and collapse it
    _fillProfileStereoFields({
        stereo_enabled: false, stereo_mode: '2d_to_3d', stereo_format: 'half_sbs',
        stereo_divergence: 2.0, stereo_convergence: 0.5, stereo_depth_model: 'Any_V2_S',
    });
    const stSec  = document.getElementById('profileStereoSection');
    const stIcon = document.getElementById('profileStereoToggleIcon');
    if (stSec && !stSec.classList.contains('hidden')) stSec.classList.add('hidden');
    if (stIcon) stIcon.textContent = '▶';

    // Initialize preset options for default encoder (svt_av1)
    updatePresetOptions();

    document.getElementById('profileModal').classList.remove('hidden');
}

async function editProfile(id) {
    currentProfileId = id;
    const profile = await apiRequest(`/profiles/${id}`);
    
    if (!profile) return;
    
    document.getElementById('profileModalTitle').textContent = 'Edit Profile';
    document.getElementById('profileId').value = profile.id;
    document.getElementById('profileName').value = profile.name;
    document.getElementById('profileCodec').value = profile.codec;
    const encSel = document.getElementById('profileEncoder');
    if (![...encSel.options].some(o => o.value === profile.encoder)) {
        // Encoder set via import/API that the dropdown doesn't list — keep it
        encSel.add(new Option(profile.encoder, profile.encoder));
    }
    encSel.value = profile.encoder;
    document.getElementById('profileResolution').value = profile.resolution || '';
    document.getElementById('profileContainer').value = profile.container || 'mkv';
    
    // Handle framerate - check if it's a standard value
    const standardFps = ['', '24', '30', '60'];
    const fpsValue = profile.framerate ? profile.framerate.toString() : '';
    if (standardFps.includes(fpsValue)) {
        document.getElementById('profileFramerate').value = fpsValue;
        document.getElementById('profileFramerateCustom').classList.add('hidden');
    } else if (fpsValue) {
        // Custom FPS
        document.getElementById('profileFramerate').value = 'custom';
        document.getElementById('profileFramerateCustom').value = fpsValue;
        document.getElementById('profileFramerateCustom').classList.remove('hidden');
    }
    
    document.getElementById('profileQuality').value = profile.quality;
    _setTargetVmaf(profile.target_vmaf || 0);
    document.getElementById('profilePreset').value = profile.preset || '';
    document.getElementById('profileAudioCodec').value = profile.audio_codec;
    document.getElementById('profileAudioHandling').value = profile.audio_handling || 'preserve_all';
    document.getElementById('profileSubtitleHandling').value = profile.subtitle_handling || 'none';
    document.getElementById('profileEnableFilters').checked = profile.enable_filters || false;
    document.getElementById('profileChapterMarkers').checked = profile.chapter_markers !== false;
    document.getElementById('profileHwAccel').checked = profile.hw_accel_enabled || false;
    document.getElementById('profileTwoPass').checked = profile.two_pass;
    _setFilmGrain(profile.film_grain || 0);
    document.getElementById('profileIsDefault').checked = profile.is_default;
    document.getElementById('profileCustomArgs').value = profile.custom_args || '';

    // Populate AI upscale fields
    _fillProfileUpscaleFields(profile);

    // Populate stereo fields
    _fillProfileStereoFields(profile);

    // Update preset options based on encoder
    updatePresetOptions();

    document.getElementById('profileModal').classList.remove('hidden');
}

function closeProfileModal() {
    document.getElementById('profileModal').classList.add('hidden');
    currentProfileId = null;
}

document.getElementById('profileForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    
    // Handle framerate - check for custom input
    let framerate = document.getElementById('profileFramerate').value;
    if (framerate === 'custom') {
        framerate = document.getElementById('profileFramerateCustom').value;
    }
    
    const data = {
        name: document.getElementById('profileName').value,
        codec: document.getElementById('profileCodec').value,
        encoder: document.getElementById('profileEncoder').value,
        resolution: document.getElementById('profileResolution').value || null,
        framerate: parseInt(framerate) || null,
        quality: parseInt(document.getElementById('profileQuality').value),
        target_vmaf: parseFloat(document.getElementById('profileTargetVmaf')?.value || '0') || 0,
        preset: document.getElementById('profilePreset').value || null,
        audio_codec: document.getElementById('profileAudioCodec').value,
        container: document.getElementById('profileContainer').value,
        audio_handling: document.getElementById('profileAudioHandling').value,
        subtitle_handling: document.getElementById('profileSubtitleHandling').value,
        enable_filters: document.getElementById('profileEnableFilters').checked,
        chapter_markers: document.getElementById('profileChapterMarkers').checked,
        hw_accel_enabled: document.getElementById('profileHwAccel').checked,
        two_pass: document.getElementById('profileTwoPass').checked,
        film_grain: parseInt(document.getElementById('profileFilmGrain')?.value || '0', 10) || 0,
        custom_args: document.getElementById('profileCustomArgs').value || null,
        is_default: document.getElementById('profileIsDefault').checked,
        // AI upscale settings
        upscale_enabled:       document.getElementById('profileUpscaleEnabled')?.checked ?? false,
        upscale_trigger_below: parseInt(document.getElementById('profileUpscaleTrigger')?.value) || 720,
        upscale_target_height: parseInt(document.getElementById('profileUpscaleTarget')?.value)  || 1080,
        upscale_key:           document.getElementById('profileUpscaleKey')?.value   || 'realesrgan',
        upscale_model:         document.getElementById('profileUpscaleModel')?.value || 'realesrgan-x4plus',
        upscale_factor:        parseInt(document.getElementById('profileUpscaleFactor')?.value)  || 2,
        // 3D / stereo settings
        stereo_enabled:       document.getElementById('profileStereoEnabled')?.checked ?? false,
        stereo_mode:          document.getElementById('profileStereoMode')?.value || '2d_to_3d',
        stereo_format:        document.getElementById('profileStereoFormat')?.value || 'half_sbs',
        stereo_divergence:    parseFloat(document.getElementById('profileStereoDivergence')?.value) || 2.0,
        stereo_convergence:   parseFloat(document.getElementById('profileStereoConvergence')?.value) || 0.5,
        stereo_depth_model:   document.getElementById('profileStereoDepthModel')?.value || 'Any_V2_S',
    };
    
    const method = currentProfileId ? 'PUT' : 'POST';
    const url = currentProfileId ? `/profiles/${currentProfileId}` : '/profiles';
    
    const result = await apiRequest(url, {
        method: method,
        body: JSON.stringify(data)
    });
    
    if (result) {
        closeProfileModal();
        loadProfiles();
        showMessage('Profile saved successfully!', 'success');
    }
});

async function deleteProfile(id, name) {
    if (!confirm(`Delete profile "${name}"?`)) return;
    
    const result = await apiRequest(`/profiles/${id}`, { method: 'DELETE' });
    
    if (result) {
        loadProfiles();
        showMessage('Profile deleted successfully!', 'success');
    }
}



function _setTargetVmaf(value) {
    const sel = document.getElementById('profileTargetVmaf');
    if (!sel) return;
    const v = String(Number(value) || 0);
    if (![...sel.options].some(o => o.value === v)) sel.add(new Option(`${v} (custom)`, v));
    sel.value = v;
}
