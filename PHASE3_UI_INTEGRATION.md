# Phase 3 - Schedule UI Integration Guide

## Overview
The backend for Phase 3 (Scheduling System) is complete! This guide will help you integrate the Schedule tab into the web UI.

---

## Files to Modify

1. **web/templates/index.html** - Add Schedule tab
2. **web/static/js/app.js** - Add schedule functions

---

## Part 1: Update index.html

### Step 1: Add "Schedule" to the Tab Navigation

Find this line (around line 88):
```html
<button onclick="switchTab('settings')" id="tab-settings" class="tab-button">Settings</button>
```

**Add AFTER it:**
```html
<button onclick="switchTab('schedule')" id="tab-schedule" class="tab-button">Schedule</button>
```

### Step 2: Add the Schedule Tab Content

Find the closing `</div>` for the "content-settings" div (around line 250).

**Add AFTER the entire settings div:**
```html
        <div id="content-schedule" class="tab-content hidden">
            <div class="bg-gray-800 rounded-lg p-6">
                <h2 class="text-xl font-bold mb-6">Encoding Schedule</h2>
                
                <div class="space-y-6">
                    <!-- Enable/Disable Schedule -->
                    <div class="flex items-center justify-between p-4 bg-gray-700 rounded-lg">
                        <div>
                            <h3 class="text-lg font-semibold">Enable Scheduled Encoding</h3>
                            <p class="text-sm text-gray-400">Automatically start/stop encoding based on schedule</p>
                        </div>
                        <label class="relative inline-flex items-center cursor-pointer">
                            <input type="checkbox" id="scheduleEnabled" class="sr-only peer">
                            <div class="w-11 h-6 bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-blue-600"></div>
                        </label>
                    </div>
                    
                    <!-- Days of Week -->
                    <div>
                        <label class="block text-sm font-medium text-gray-300 mb-3">Days of Week</label>
                        <div class="grid grid-cols-7 gap-2">
                            <button type="button" onclick="toggleDay(0)" id="day-0" class="day-button">Mon</button>
                            <button type="button" onclick="toggleDay(1)" id="day-1" class="day-button">Tue</button>
                            <button type="button" onclick="toggleDay(2)" id="day-2" class="day-button">Wed</button>
                            <button type="button" onclick="toggleDay(3)" id="day-3" class="day-button">Thu</button>
                            <button type="button" onclick="toggleDay(4)" id="day-4" class="day-button">Fri</button>
                            <button type="button" onclick="toggleDay(5)" id="day-5" class="day-button">Sat</button>
                            <button type="button" onclick="toggleDay(6)" id="day-6" class="day-button">Sun</button>
                        </div>
                    </div>
                    
                    <!-- Time Window -->
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <div>
                            <label class="block text-sm font-medium text-gray-300 mb-2">Start Time</label>
                            <input 
                                type="time" 
                                id="startTime" 
                                class="w-full bg-gray-700 border border-gray-600 rounded px-4 py-2 text-white"
                                value="22:00"
                            >
                            <p class="text-sm text-gray-400 mt-1">When to start encoding</p>
                        </div>
                        
                        <div>
                            <label class="block text-sm font-medium text-gray-300 mb-2">End Time</label>
                            <input 
                                type="time" 
                                id="endTime" 
                                class="w-full bg-gray-700 border border-gray-600 rounded px-4 py-2 text-white"
                                value="06:00"
                            >
                            <p class="text-sm text-gray-400 mt-1">When to stop encoding</p>
                        </div>
                    </div>
                    
                    <!-- Current Status -->
                    <div class="p-4 bg-gray-700 rounded-lg">
                        <h3 class="font-semibold mb-2">Current Status</h3>
                        <div class="grid grid-cols-2 gap-4 text-sm">
                            <div>
                                <span class="text-gray-400">Schedule Active:</span>
                                <span id="scheduleStatus" class="ml-2 font-medium">-</span>
                            </div>
                            <div>
                                <span class="text-gray-400">Within Window:</span>
                                <span id="withinWindow" class="ml-2 font-medium">-</span>
                            </div>
                            <div>
                                <span class="text-gray-400">Manual Override:</span>
                                <span id="manualOverride" class="ml-2 font-medium">-</span>
                            </div>
                            <div>
                                <span class="text-gray-400">Next Check:</span>
                                <span id="nextCheck" class="ml-2 font-medium">Every minute</span>
                            </div>
                        </div>
                    </div>
                    
                    <!-- Save Button -->
                    <div class="flex justify-end gap-3">
                        <button onclick="saveSchedule()" class="bg-blue-600 hover:bg-blue-700 px-6 py-2 rounded font-medium">
                            Save Schedule
                        </button>
                    </div>
                    
                    <div id="scheduleMessage" class="hidden mt-4"></div>
                </div>
            </div>
        </div>
```

### Step 3: Add CSS for Day Buttons

Find the `<style>` section at the bottom of index.html (after line 250).

**Add before the closing `</style>` tag:**
```css
    .day-button {
        padding: 0.75rem;
        border-radius: 0.5rem;
        background-color: #374151;
        color: #9ca3af;
        border: 2px solid transparent;
        transition: all 0.2s;
        cursor: pointer;
        font-weight: 500;
    }
    
    .day-button:hover {
        background-color: #4b5563;
    }
    
    .day-button-active {
        background-color: #3b82f6;
        color: white;
        border-color: #60a5fa;
    }
```

---

## Part 2: Update app.js

### Step 1: Update switchTab Function

Find the `switchTab` function (around line 60).

**Change line:**
```javascript
if (tabName === 'settings') loadSettings();
```

**To:**
```javascript
if (tabName === 'settings') loadSettings();
if (tabName === 'schedule') loadSchedule();
```

### Step 2: Add Schedule Functions

**Add at the END of app.js (before the last line):**
```javascript

// Schedule management
let selectedDays = new Set([0, 1, 2, 3, 4, 5, 6]); // All days selected by default

async function loadSchedule() {
    const schedule = await apiRequest('/schedule');
    if (schedule && schedule.config) {
        const config = schedule.config;
        
        // Set enabled state
        document.getElementById('scheduleEnabled').checked = config.enabled;
        
        // Set days
        selectedDays = new Set(config.days_of_week.split(',').map(d => parseInt(d)));
        updateDayButtons();
        
        // Set times
        document.getElementById('startTime').value = config.start_time;
        document.getElementById('endTime').value = config.end_time;
        
        // Update status
        document.getElementById('scheduleStatus').textContent = config.enabled ? '✓ Enabled' : '✗ Disabled';
        document.getElementById('scheduleStatus').className = config.enabled ? 'ml-2 font-medium text-green-400' : 'ml-2 font-medium text-gray-400';
        
        document.getElementById('withinWindow').textContent = schedule.within_schedule ? '✓ Yes' : '✗ No';
        document.getElementById('withinWindow').className = schedule.within_schedule ? 'ml-2 font-medium text-green-400' : 'ml-2 font-medium text-gray-400';
        
        document.getElementById('manualOverride').textContent = schedule.manual_override ? '✓ Active' : '✗ Inactive';
        document.getElementById('manualOverride').className = schedule.manual_override ? 'ml-2 font-medium text-yellow-400' : 'ml-2 font-medium text-gray-400';
    }
}

function toggleDay(day) {
    if (selectedDays.has(day)) {
        selectedDays.delete(day);
    } else {
        selectedDays.add(day);
    }
    updateDayButtons();
}

function updateDayButtons() {
    for (let i = 0; i < 7; i++) {
        const btn = document.getElementById(`day-${i}`);
        if (selectedDays.has(i)) {
            btn.className = 'day-button day-button-active';
        } else {
            btn.className = 'day-button';
        }
    }
}

async function saveSchedule() {
    const config = {
        enabled: document.getElementById('scheduleEnabled').checked,
        days_of_week: Array.from(selectedDays).sort().join(','),
        start_time: document.getElementById('startTime').value,
        end_time: document.getElementById('endTime').value,
        timezone: 'UTC'
    };
    
    const result = await apiRequest('/schedule', {
        method: 'POST',
        body: JSON.stringify(config)
    });
    
    if (result) {
        const msgEl = document.getElementById('scheduleMessage');
        msgEl.textContent = '✓ Schedule saved successfully';
        msgEl.className = 'mt-4 p-3 bg-green-900/50 border border-green-700 rounded text-green-300';
        msgEl.classList.remove('hidden');
        
        setTimeout(() => msgEl.classList.add('hidden'), 3000);
        
        // Reload schedule to update status
        loadSchedule();
    }
}
```

---

## Part 3: Install APScheduler

Phase 3 requires APScheduler. Install it:

```powershell
pip install apscheduler
```

---

## Testing the Schedule Tab

After making the changes:

1. **Restart the server:**
   ```powershell
   python -m app.main
   ```

2. **Open browser:** http://localhost:5000

3. **Click "Schedule" tab**

4. **Test features:**
   - Toggle schedule on/off
   - Click days of week (they should highlight)
   - Set start/end times
   - Click "Save Schedule"
   - Check status updates

---

## What You Should See

- **Schedule Tab** in navigation
- **Toggle switch** to enable/disable
- **7 day buttons** (Mon-Sun) that highlight when clicked
- **Time pickers** for start/end
- **Status panel** showing:
  - Schedule Active (Yes/No)
  - Within Window (Yes/No)
  - Manual Override (Active/Inactive)
- **Save button** that persists settings

---

## How It Works

1. **Every minute**, scheduler checks if current time is in window
2. **Checks** if today is a selected day
3. **Starts encoding** automatically when schedule activates
4. **Stops encoding** automatically when schedule ends
5. **Manual start** sets override flag (schedule won't stop it)
6. **Manual stop** clears override flag

---

## Example Use Cases

### Overnight Encoding
- Days: Mon-Fri
- Start: 22:00 (10 PM)
- End: 06:00 (6 AM)
- Result: Encodes only on weeknight evenings

### Weekend Only
- Days: Sat, Sun
- Start: 00:00
- End: 23:59
- Result: Encodes all day on weekends

### Business Hours OFF
- Days: All days
- Start: 18:00 (6 PM)
- End: 09:00 (9 AM)
- Result: Encodes only outside business hours

---

## Troubleshooting

**Schedule tab doesn't appear:**
- Check that you added the tab button to navigation
- Check that the tab content div was added
- Refresh the page hard (Ctrl+F5)

**Days don't highlight:**
- Check that CSS was added to `<style>` section
- Check that `toggleDay()` function was added to app.js

**Save doesn't work:**
- Check browser console for errors (F12)
- Verify `saveSchedule()` function was added
- Check that API endpoint is working: http://localhost:5000/api/schedule

**Status doesn't update:**
- Check that `loadSchedule()` function was added
- Verify it's called in `switchTab()`
- Check browser console for API errors

---

**Phase 3 Complete!** 🎉

Once you've made these changes, commit and push to GitHub!
