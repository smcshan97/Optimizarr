// ============================================================
// PROFILE UI IMPROVEMENTS
// ============================================================

// Preset options for different encoders
const PRESET_OPTIONS = {
    'x264': [
        { value: '', label: 'Auto (recommended)' },
        { value: 'ultrafast', label: 'ultrafast (fastest, lower quality)' },
        { value: 'superfast', label: 'superfast' },
        { value: 'veryfast', label: 'veryfast' },
        { value: 'faster', label: 'faster' },
        { value: 'fast', label: 'fast' },
        { value: 'medium', label: 'medium (balanced) ⭐' },
        { value: 'slow', label: 'slow' },
        { value: 'slower', label: 'slower' },
        { value: 'veryslow', label: 'veryslow (slowest, best quality)' }
    ],
    'x265': [
        { value: '', label: 'Auto (recommended)' },
        { value: 'ultrafast', label: 'ultrafast (fastest, lower quality)' },
        { value: 'superfast', label: 'superfast' },
        { value: 'veryfast', label: 'veryfast' },
        { value: 'faster', label: 'faster' },
        { value: 'fast', label: 'fast' },
        { value: 'medium', label: 'medium (balanced) ⭐' },
        { value: 'slow', label: 'slow' },
        { value: 'slower', label: 'slower' },
        { value: 'veryslow', label: 'veryslow (slowest, best quality)' }
    ],
    'svt_av1': [
        { value: '', label: 'Auto (recommended)' },
        { value: '0', label: '0 (slowest, best quality)' },
        { value: '1', label: '1' },
        { value: '2', label: '2' },
        { value: '3', label: '3' },
        { value: '4', label: '4' },
        { value: '5', label: '5' },
        { value: '6', label: '6 (balanced) ⭐' },
        { value: '7', label: '7' },
        { value: '8', label: '8 (faster)' },
        { value: '9', label: '9' },
        { value: '10', label: '10' },
        { value: '11', label: '11' },
        { value: '12', label: '12' },
        { value: '13', label: '13 (fastest, lower quality)' }
    ],
    'nvenc_h264': [
        { value: '', label: 'Auto (recommended)' },
        { value: 'p1', label: 'p1 (fastest)' },
        { value: 'p2', label: 'p2' },
        { value: 'p3', label: 'p3' },
        { value: 'p4', label: 'p4 (balanced) ⭐' },
        { value: 'p5', label: 'p5' },
        { value: 'p6', label: 'p6' },
        { value: 'p7', label: 'p7 (slowest, best quality)' }
    ],
    'nvenc_h265': [
        { value: '', label: 'Auto (recommended)' },
        { value: 'p1', label: 'p1 (fastest)' },
        { value: 'p2', label: 'p2' },
        { value: 'p3', label: 'p3' },
        { value: 'p4', label: 'p4 (balanced) ⭐' },
        { value: 'p5', label: 'p5' },
        { value: 'p6', label: 'p6' },
        { value: 'p7', label: 'p7 (slowest, best quality)' }
    ],
    'nvenc_av1': [
        { value: '', label: 'Auto (recommended)' },
        { value: 'p1', label: 'p1 (fastest)' },
        { value: 'p2', label: 'p2' },
        { value: 'p3', label: 'p3' },
        { value: 'p4', label: 'p4 (balanced) ⭐' },
        { value: 'p5', label: 'p5' },
        { value: 'p6', label: 'p6' },
        { value: 'p7', label: 'p7 (slowest, best quality)' }
    ],
    'qsv_h264': [
        { value: '', label: 'Auto (recommended)' },
        { value: 'veryfast', label: 'veryfast (fastest)' },
        { value: 'faster', label: 'faster' },
        { value: 'fast', label: 'fast' },
        { value: 'medium', label: 'medium (balanced) ⭐' },
        { value: 'slow', label: 'slow' },
        { value: 'slower', label: 'slower' },
        { value: 'veryslow', label: 'veryslow (slowest, best quality)' }
    ],
    'qsv_h265': [
        { value: '', label: 'Auto (recommended)' },
        { value: 'veryfast', label: 'veryfast (fastest)' },
        { value: 'faster', label: 'faster' },
        { value: 'fast', label: 'fast' },
        { value: 'medium', label: 'medium (balanced) ⭐' },
        { value: 'slow', label: 'slow' },
        { value: 'slower', label: 'slower' },
        { value: 'veryslow', label: 'veryslow (slowest, best quality)' }
    ]
};

// Update preset dropdown when encoder changes
function updatePresetOptions() {
    const encoder = document.getElementById('profileEncoder').value;
    const presetSelect = document.getElementById('profilePreset');
    const presetHelp = document.getElementById('presetHelp');
    
    // 10-bit variants share their 8-bit encoder's presets
    const options = PRESET_OPTIONS[encoder]
        || PRESET_OPTIONS[encoder.replace(/_10bit$/, '')]
        || [{ value: '', label: 'Auto (recommended)' }];

    // Save current value
    const currentValue = presetSelect.value;
    
    // Clear and repopulate
    presetSelect.innerHTML = options.map(opt => 
        `<option value="${opt.value}">${opt.label}</option>`
    ).join('');
    
    // Restore value if it exists in new options
    if (options.find(opt => opt.value === currentValue)) {
        presetSelect.value = currentValue;
    }
    
    // Update help text
    if (encoder.startsWith('nvenc')) {
        presetHelp.textContent = 'NVENC: p1=fastest, p7=slowest/best quality';
    } else if (encoder.startsWith('svt_av1')) {
        presetHelp.textContent = 'SVT-AV1: 0=slowest/best, 13=fastest';
    } else {
        presetHelp.textContent = 'Speed vs quality trade-off';
    }
}

// Handle framerate custom input
function handleFramerateChange() {
    const fps = document.getElementById('profileFramerate').value;
    const customInput = document.getElementById('profileFramerateCustom');
    
    if (fps === 'custom') {
        customInput.classList.remove('hidden');
        customInput.focus();
    } else {
        customInput.classList.add('hidden');
    }
}

// Show codec guide modal
function showCodecGuide() {
    const guide = `
╔══════════════════════════════════════════════════════════════╗
║                    VIDEO CODEC GUIDE                         ║
╚══════════════════════════════════════════════════════════════╝

🎬 H.264 (AVC)
   • Universal compatibility - plays on everything
   • Largest file sizes
   • Fast encoding
   • Use for: Maximum compatibility, older devices

📦 H.265 (HEVC)  
   • 50% smaller than H.264 at same quality
   • Wide device support (2016+)
   • Moderate encoding speed
   • Use for: Balance of size and compatibility

⭐ AV1 (RECOMMENDED)
   • 70% smaller than H.264 at same quality
   • Best compression available
   • Newer devices (2020+), all modern browsers
   • Slower encoding
   • Royalty-free and open source
   • Use for: Maximum space savings, modern libraries

🌐 VP9
   • Google's codec, similar to H.265
   • 50-60% smaller than H.264
   • Great for web streaming (YouTube)
   • Use for: YouTube uploads, web content

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 FILE SIZE COMPARISON (2-hour 1080p movie):

   H.264:  ~8 GB   ■■■■■■■■■■■■■■■■ 100%
   H.265:  ~4 GB   ■■■■■■■■         50%  
   AV1:    ~2.5 GB ■■■■■            30% ⭐
   VP9:    ~3.5 GB ■■■■■■■          44%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💡 RECOMMENDATION: Use AV1 for best compression!
`;
    
    alert(guide);
}

function _setFilmGrain(value) {
    const slider = document.getElementById('profileFilmGrain');
    const label = document.getElementById('profileFilmGrainValue');
    if (slider) slider.value = value;
    if (label) label.textContent = value ? String(value) : 'Off';
}

// Grey out encoders the installed HandBrake can't run (it only lists GPU
// encoders the hardware actually supports, e.g. no nvenc_av1 before RTX 40).
async function markUnavailableEncoders() {
    const select = document.getElementById('profileEncoder');
    if (!select || !getToken()) return; // not logged in — checkAuth is redirecting
    const hw = await apiRequest('/hardware-detect');
    const names = new Set((hw && hw.encoder_names) || []);
    if (!names.size) return; // couldn't read the list — leave everything enabled
    select.querySelectorAll('option').forEach(opt => {
        const ok = names.has(opt.value);
        opt.disabled = !ok;
        opt.textContent = opt.textContent.replace(/ \(unavailable\)$/, '') + (ok ? '' : ' (unavailable)');
    });
}

// Initialize encoder change listener
document.addEventListener('DOMContentLoaded', function() {
    const encoderSelect = document.getElementById('profileEncoder');
    if (encoderSelect) {
        encoderSelect.addEventListener('change', updatePresetOptions);
        // Initialize on load
        updatePresetOptions();
        markUnavailableEncoders();
    }
});


