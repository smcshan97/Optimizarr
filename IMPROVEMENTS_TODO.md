# Remaining UI/UX Improvements

## Status: In Progress

### ✅ COMPLETED
- [x] Windows auto-fix PowerShell script (WINDOWS_SETUP.ps1)
- [x] PUT endpoints for profiles and scan roots (fixes 405 errors)
- [x] GET endpoint for single scan root
- [x] Default profile set to 1080p AV1 24fps
- [x] Resolution dropdown with clear labels
- [x] Framerate dropdown with recommendations
- [x] Default profile badge display

### 🔨 TODO

#### 1. Browse Button for Scan Root Path
- Add file browser button next to path input
- Use HTML5 `<input type="file" webkitdirectory>` for folder selection
- Fallback: Show helpful text about pasting absolute path

#### 2. Preset Dropdown in Profile Form
- Convert "Preset" text input to dropdown
- Options depend on encoder:
  - x264/x265: ultrafast, superfast, veryfast, faster, fast, medium, slow, slower, veryslow
  - SVT-AV1: 0-13 (0=slowest/best, 13=fastest)
  - NVENC: p1-p7 (p1=fastest, p7=slowest/best)
- Add helper text explaining preset trade-offs

#### 3. Custom FPS Option
- Add "Custom" option to framerate dropdown
- Show number input when "Custom" selected
- Allow any FPS value (e.g., 25, 48, 120)

#### 4. Codec Guide/Helper
- Add info icon (ℹ️) next to Codec field
- Tooltip or modal with codec comparison:
  ```
  H.264: Universal compatibility, largest files
  H.265: 50% smaller than H.264, wide support
  AV1: 70% smaller than H.264, best compression ⭐
  VP9: Google codec, good for web streaming
  ```

#### 5. Settings Cog Icon
- Move Settings tab button to top-right near Logout
- Replace text button with gear/cog icon
- Keep hover tooltip: "Settings"

#### 6. Tailwind CDN Warning
- Not critical for development
- For production: Install Tailwind CLI or PostCSS plugin
- Document in deployment guide

### 📝 NICE TO HAVE

- Real-time file browser integration
- Preset templates (e.g., "Netflix Optimized", "YouTube Upload")
- Batch profile operations
- Profile import/export
- Before/after file size preview
- Encoding time estimates

### 🐛 KNOWN ISSUES

- None currently!

---

## Implementation Priority

1. **High**: Fix 405 errors (DONE ✅)
2. **High**: Windows setup script (DONE ✅)
3. **Medium**: Preset dropdown (improves UX)
4. **Medium**: Custom FPS (adds flexibility)
5. **Low**: Browse button (nice to have, path input works)
6. **Low**: Codec guide (educational, not critical)
7. **Low**: Settings icon (cosmetic)

---

## Next Actions

Focus on Medium priority items:
1. Preset dropdown with encoder-specific options
2. Custom FPS number input
3. Then tackle Low priority UI polish

Target: Phase 3.6 "Polish Release"
