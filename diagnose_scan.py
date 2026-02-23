#!/usr/bin/env python3
"""
Optimizarr Scan Diagnostic Tool
================================
Run this script from your optimizarr directory to diagnose why scanning finds 0 files.

Usage:
    cd D:\Downloads\optimizarr
    python diagnose_scan.py
"""
import os
import sys
import sqlite3
from pathlib import Path

print("=" * 70)
print("OPTIMIZARR SCAN DIAGNOSTIC")
print("=" * 70)
print()

# Step 1: Check database exists
db_path = Path("data/optimizarr.db")
if not db_path.exists():
    print("❌ Database not found at data/optimizarr.db")
    print("   Run the server first: python -m app.main")
    sys.exit(1)
print(f"✓ Database found: {db_path.absolute()}")

# Step 2: Check scan roots
conn = sqlite3.connect(str(db_path))
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

cursor.execute("SELECT * FROM scan_roots")
roots = cursor.fetchall()

if not roots:
    print("\n❌ NO SCAN ROOTS CONFIGURED!")
    print("   You need to add a scan root in the web UI first.")
    print("   Go to Scan Roots tab → Add Scan Root → Enter your media folder path")
    conn.close()
    sys.exit(1)

print(f"\n📁 Found {len(roots)} scan root(s):")
for root in roots:
    root_dict = dict(root)
    path = root_dict['path']
    enabled = root_dict.get('enabled', 1)
    profile_id = root_dict.get('profile_id')
    recursive = root_dict.get('recursive', 1)
    
    print(f"\n   ID: {root_dict['id']}")
    print(f"   Path: '{path}'")
    print(f"   Enabled: {bool(enabled)}")
    print(f"   Recursive: {bool(recursive)}")
    print(f"   Profile ID: {profile_id}")
    
    # Check if path exists
    p = Path(path)
    if not p.exists():
        print(f"   ❌ PATH DOES NOT EXIST!")
        print(f"      The path '{path}' cannot be found on this system.")
        print(f"      Common issues:")
        print(f"        - Typo in the path")
        print(f"        - Using forward slashes instead of backslashes on Windows")
        print(f"        - Path was from a container/Docker context (/media/movies)")
        print(f"        - Drive letter missing")
        
        # Try some variations
        variations = [
            path.replace('/', '\\'),
            path.replace('\\', '/'),
            path.strip(),
            path.strip('"').strip("'"),
        ]
        for v in variations:
            if v != path and Path(v).exists():
                print(f"      💡 BUT this path DOES exist: '{v}'")
        continue
    
    if not p.is_dir():
        print(f"   ❌ PATH IS NOT A DIRECTORY!")
        continue
    
    print(f"   ✓ Path exists and is a directory")
    
    # Check what's in the directory
    VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.m4v', '.ts', '.mpg', '.mpeg', '.wmv', '.flv', '.webm', '.m2ts', '.vob', '.divx', '.3gp', '.ogv'}
    
    all_files = []
    video_files = []
    
    try:
        if recursive:
            for fp in p.rglob('*'):
                if fp.is_file():
                    all_files.append(fp)
                    if fp.suffix.lower() in VIDEO_EXTENSIONS:
                        video_files.append(fp)
        else:
            for fp in p.glob('*'):
                if fp.is_file():
                    all_files.append(fp)
                    if fp.suffix.lower() in VIDEO_EXTENSIONS:
                        video_files.append(fp)
    except PermissionError as e:
        print(f"   ❌ PERMISSION ERROR: {e}")
        continue
    except Exception as e:
        print(f"   ❌ ERROR scanning directory: {e}")
        continue
    
    print(f"   Total files found: {len(all_files)}")
    print(f"   Video files found: {len(video_files)}")
    
    if video_files:
        print(f"   ✓ Sample video files:")
        for vf in video_files[:5]:
            print(f"      - {vf.name} ({vf.suffix.lower()}) [{vf.stat().st_size / (1024*1024):.1f} MB]")
        if len(video_files) > 5:
            print(f"      ... and {len(video_files) - 5} more")
    else:
        print(f"   ❌ NO VIDEO FILES FOUND in this directory!")
        # Show what extensions ARE present
        extensions = set()
        for f in all_files[:100]:
            extensions.add(f.suffix.lower())
        if extensions:
            print(f"   File extensions found: {', '.join(sorted(extensions))}")
            # Check if any are video-like but not in our list
            possible_video = {'.264', '.265', '.h264', '.h265', '.hevc', '.av1', '.vp9'}
            missed = extensions & possible_video
            if missed:
                print(f"   💡 Possible video extensions NOT in scanner: {missed}")
        else:
            print(f"   Directory appears to be empty!")
    
    # Check profile exists
    cursor.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,))
    profile = cursor.fetchone()
    if profile:
        print(f"   ✓ Profile found: '{dict(profile)['name']}'")
    else:
        print(f"   ❌ PROFILE ID {profile_id} NOT FOUND IN DATABASE!")
        print(f"      This will cause the scan to return 0 immediately.")
        print(f"      Fix: Create a profile first, then update the scan root.")

# Step 3: Check existing queue items
cursor.execute("SELECT COUNT(*) as cnt FROM queue")
queue_count = cursor.fetchone()['cnt']
print(f"\n📋 Current queue items: {queue_count}")
if queue_count > 0:
    cursor.execute("SELECT file_path, status FROM queue LIMIT 10")
    for item in cursor.fetchall():
        item_dict = dict(item)
        print(f"   - {Path(item_dict['file_path']).name} [{item_dict['status']}]")
    print(f"   💡 If files are already in queue, re-scanning won't add them again!")

# Step 4: Check if the scan endpoint is routing correctly
print(f"\n🔍 Checking API routes...")
try:
    # Check if routes.py has the scan endpoint
    routes_candidates = [
        Path("app/api/routes.py"),
    ]
    for rp in routes_candidates:
        if rp.exists():
            content = rp.read_text(encoding='utf-8')
            if '/scan' in content or 'scan_root' in content or 'scan_all' in content:
                print(f"   ✓ Scan endpoint found in {rp}")
                
                # Check which scan function is being called
                if 'scanner.scan_all_roots' in content:
                    print(f"   ✓ Uses scanner.scan_all_roots()")
                if 'scanner.scan_root' in content:
                    print(f"   ✓ Uses scanner.scan_root()")
                
                # Check if it's a background task
                if 'background_tasks' in content.lower() or 'BackgroundTasks' in content:
                    print(f"   ⚠️  Scan runs as BackgroundTask - check server console for output!")
                    print(f"      The scan happens AFTER the API response is sent.")
                    print(f"      Look at the terminal where 'python -m app.main' is running.")
            else:
                print(f"   ❌ No scan endpoint found in {rp}")
except Exception as e:
    print(f"   Error checking routes: {e}")

# Step 5: Check scanner.py exists and has correct extensions
print(f"\n🔍 Checking scanner module...")
scanner_path = Path("app/scanner.py")
if scanner_path.exists():
    content = scanner_path.read_text(encoding='utf-8')
    
    # Check VIDEO_EXTENSIONS
    if 'VIDEO_EXTENSIONS' in content:
        # Extract the set
        import re
        match = re.search(r'VIDEO_EXTENSIONS\s*=\s*\{([^}]+)\}', content)
        if match:
            exts = match.group(1)
            print(f"   Supported extensions: {exts.strip()}")
            if '.mp4' in exts:
                print(f"   ✓ .mp4 is supported")
            else:
                print(f"   ❌ .mp4 is NOT in the extension list!")
    
    # Check for common issues
    if 'root.exists()' in content:
        print(f"   ✓ Has path existence check")
    if 'rglob' in content:
        print(f"   ✓ Has recursive scanning (rglob)")
    if 'PermissionError' in content:
        print(f"   ✓ Has permission error handling")
else:
    print(f"   ❌ scanner.py NOT FOUND at app/scanner.py!")

conn.close()

print()
print("=" * 70)
print("DIAGNOSIS COMPLETE")
print("=" * 70)
print()
print("Common fixes:")
print("  1. Make sure the scan root path is the EXACT path to your media folder")
print("     Example: D:\\Movies  or  D:\\Downloads\\Movies")
print("  2. Make sure a profile exists and is assigned to the scan root")
print("  3. Check the terminal running 'python -m app.main' for error messages")
print("  4. If files are already in queue, clear the queue first, then re-scan")
print("  5. Delete data/optimizarr.db and restart to get a fresh database")
print()
