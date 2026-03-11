# Dashboard UI Completion Guide

## Overview
This guide adds complete UI forms for Profiles and Scan Roots management.

---

## Part 1: Add Modals to index.html

Open `web/templates/index.html` and find the line with `<div id="content-schedule"` (around line 268).

**ADD BEFORE that line (around line 267):**

```html
<!-- Profile Modal -->
<div id="profileModal" class="hidden fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
    <div class="bg-gray-800 rounded-lg p-6 max-w-2xl w-full max-h-[90vh] overflow-y-auto">
        <div class="flex justify-between items-center mb-6">
            <h3 class="text-xl font-bold" id="profileModalTitle">Create Profile</h3>
            <button onclick="closeProfileModal()" class="text-gray-400 hover:text-white">✕</button>
        </div>
        
        <form id="profileForm" class="space-y-4">
            <input type="hidden" id="profileId">
            
            <div>
                <label class="block text-sm font-medium mb-2">Profile Name *</label>
                <input type="text" id="profileName" required 
                    class="w-full bg-gray-700 border border-gray-600 rounded px-4 py-2"
                    placeholder="e.g., 1080p AV1 High Quality">
            </div>
            
            <div class="grid grid-cols-2 gap-4">
                <div>
                    <label class="block text-sm font-medium mb-2">Codec *</label>
                    <select id="profileCodec" required 
                        class="w-full bg-gray-700 border border-gray-600 rounded px-4 py-2">
                        <option value="h264">H.264</option>
                        <option value="h265">H.265 (HEVC)</option>
                        <option value="av1">AV1</option>
                        <option value="vp9">VP9</option>
                    </select>
                </div>
                
                <div>
                    <label class="block text-sm font-medium mb-2">Encoder *</label>
                    <select id="profileEncoder" required 
                        class="w-full bg-gray-700 border border-gray-600 rounded px-4 py-2">
                        <option value="x264">x264 (CPU)</option>
                        <option value="x265">x265 (CPU)</option>
                        <option value="svt_av1">SVT-AV1 (CPU)</option>
                        <option value="nvenc_h264">NVENC H.264 (GPU)</option>
                        <option value="nvenc_h265">NVENC H.265 (GPU)</option>
                        <option value="nvenc_av1">NVENC AV1 (GPU)</option>
                        <option value="qsv_h264">QuickSync H.264 (GPU)</option>
                        <option value="qsv_h265">QuickSync H.265 (GPU)</option>
                    </select>
                </div>
            </div>
            
            <div class="grid grid-cols-2 gap-4">
                <div>
                    <label class="block text-sm font-medium mb-2">Resolution</label>
                    <input type="text" id="profileResolution" 
                        class="w-full bg-gray-700 border border-gray-600 rounded px-4 py-2"
                        placeholder="1920x1080 or leave empty to preserve">
                </div>
                
                <div>
                    <label class="block text-sm font-medium mb-2">Framerate (FPS)</label>
                    <input type="number" id="profileFramerate" 
                        class="w-full bg-gray-700 border border-gray-600 rounded px-4 py-2"
                        placeholder="Leave empty for VFR">
                </div>
            </div>
            
            <div class="grid grid-cols-2 gap-4">
                <div>
                    <label class="block text-sm font-medium mb-2">Quality (CRF) *</label>
                    <input type="number" id="profileQuality" required min="18" max="51" value="28"
                        class="w-full bg-gray-700 border border-gray-600 rounded px-4 py-2">
                    <p class="text-xs text-gray-400 mt-1">Lower = better quality (18-28 recommended)</p>
                </div>
                
                <div>
                    <label class="block text-sm font-medium mb-2">Preset</label>
                    <input type="text" id="profilePreset" 
                        class="w-full bg-gray-700 border border-gray-600 rounded px-4 py-2"
                        placeholder="e.g., medium, 6, p4">
                </div>
            </div>
            
            <div>
                <label class="block text-sm font-medium mb-2">Audio Codec *</label>
                <select id="profileAudioCodec" required 
                    class="w-full bg-gray-700 border border-gray-600 rounded px-4 py-2">
                    <option value="aac">AAC</option>
                    <option value="opus">Opus</option>
                    <option value="ac3">AC3</option>
                    <option value="passthrough">Passthrough (copy)</option>
                </select>
            </div>
            
            <div>
                <label class="flex items-center">
                    <input type="checkbox" id="profileTwoPass" class="mr-2">
                    <span class="text-sm">Enable two-pass encoding (better quality, slower)</span>
                </label>
            </div>
            
            <div>
                <label class="block text-sm font-medium mb-2">Custom Arguments</label>
                <input type="text" id="profileCustomArgs" 
                    class="w-full bg-gray-700 border border-gray-600 rounded px-4 py-2"
                    placeholder="Additional HandBrakeCLI arguments">
            </div>
            
            <div class="flex justify-end gap-3 pt-4">
                <button type="button" onclick="closeProfileModal()" 
                    class="bg-gray-700 hover:bg-gray-600 px-6 py-2 rounded">
                    Cancel
                </button>
                <button type="submit" 
                    class="bg-blue-600 hover:bg-blue-700 px-6 py-2 rounded">
                    Save Profile
                </button>
            </div>
        </form>
    </div>
</div>

<!-- Scan Root Modal -->
<div id="scanRootModal" class="hidden fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
    <div class="bg-gray-800 rounded-lg p-6 max-w-xl w-full">
        <div class="flex justify-between items-center mb-6">
            <h3 class="text-xl font-bold" id="scanRootModalTitle">Add Scan Root</h3>
            <button onclick="closeScanRootModal()" class="text-gray-400 hover:text-white">✕</button>
        </div>
        
        <form id="scanRootForm" class="space-y-4">
            <input type="hidden" id="scanRootId">
            
            <div>
                <label class="block text-sm font-medium mb-2">Directory Path *</label>
                <input type="text" id="scanRootPath" required 
                    class="w-full bg-gray-700 border border-gray-600 rounded px-4 py-2"
                    placeholder="C:\Media\Movies or /mnt/media/movies">
                <p class="text-xs text-gray-400 mt-1">Absolute path to scan for video files</p>
            </div>
            
            <div>
                <label class="block text-sm font-medium mb-2">Encoding Profile *</label>
                <select id="scanRootProfile" required 
                    class="w-full bg-gray-700 border border-gray-600 rounded px-4 py-2">
                    <option value="">Loading profiles...</option>
                </select>
            </div>
            
            <div>
                <label class="flex items-center">
                    <input type="checkbox" id="scanRootRecursive" checked class="mr-2">
                    <span class="text-sm">Scan subdirectories recursively</span>
                </label>
            </div>
            
            <div>
                <label class="flex items-center">
                    <input type="checkbox" id="scanRootEnabled" checked class="mr-2">
                    <span class="text-sm">Enabled</span>
                </label>
            </div>
            
            <div class="flex justify-end gap-3 pt-4">
                <button type="button" onclick="closeScanRootModal()" 
                    class="bg-gray-700 hover:bg-gray-600 px-6 py-2 rounded">
                    Cancel
                </button>
                <button type="submit" 
                    class="bg-blue-600 hover:bg-blue-700 px-6 py-2 rounded">
                    Save Scan Root
                </button>
            </div>
        </form>
    </div>
</div>
```

---

## Part 2: Update switchTab in app.js

Find the `switchTab` function and update it to load profiles and scan roots:

```javascript
function switchTab(tabName) {
    // Hide all tabs
    document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
    document.querySelectorAll('.tab-button').forEach(el => el.classList.remove('active'));
    
    // Show selected tab
    document.getElementById(`content-${tabName}`).classList.remove('hidden');
    document.getElementById(`tab-${tabName}`).classList.add('active');
    
    // Load tab data
    if (tabName === 'queue') loadQueue();
    if (tabName === 'profiles') loadProfiles();
    if (tabName === 'scanroots') loadScanRoots();
    if (tabName === 'settings') loadSettings();
    if (tabName === 'schedule') loadSchedule();
}
```

---

## Part 3: Add Functions to app.js

Add all the dashboard functions at the END of `app.js` (before the last line):

```javascript
// [PASTE THE ENTIRE CONTENTS OF dashboard_functions.js HERE]
```

See the attached `dashboard_functions.js` file for the complete code.

---

## Testing

1. Restart server: `python -m app.main`
2. Open browser: http://localhost:5000
3. Test **Profiles tab**:
   - Click "New Profile"
   - Fill out form
   - Click "Save Profile"
   - Edit and delete profiles
4. Test **Scan Roots tab**:
   - Click "Add Scan Root"
   - Enter a path (e.g., `C:\TestMedia`)
   - Select a profile
   - Click "Save Scan Root"
   - Click "Scan Now" to test scanning

---

## Files Modified

- `web/templates/index.html` (added 2 modals)
- `web/static/js/app.js` (added ~250 lines of functions)

Total new code: ~400 lines
