"""
Folder Watcher for Optimizarr.
Monitors directories for new media files and auto-queues them for encoding.
Uses polling (no external dependencies) for maximum compatibility.
"""
import os
import time
import threading
from pathlib import Path
from typing import Dict, Set, Optional
from datetime import datetime

from app.database import db
from app.logger import optimizarr_logger


# Common video extensions
VIDEO_EXTENSIONS = {
    '.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm',
    '.m4v', '.ts', '.mpg', '.mpeg', '.m2ts', '.vob'
}


class FolderWatcher:
    """Watches directories for new media files and auto-queues them."""
    
    def __init__(self, poll_interval: int = 60):
        self.poll_interval = poll_interval  # seconds between scans
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self._known_files: Dict[int, Set[str]] = {}  # watch_id -> set of known file paths
        self._lock = threading.Lock()
    
    def start(self):
        """Start the folder watcher in a background thread."""
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._poll_loop, daemon=True, name="FolderWatcher")
        self.thread.start()
        optimizarr_logger.app_logger.info("Folder watcher started (poll interval: %ds)", self.poll_interval)
    
    def stop(self):
        """Stop the folder watcher."""
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        optimizarr_logger.app_logger.info("Folder watcher stopped")
    
    def _poll_loop(self):
        """Main polling loop."""
        # Initial scan to build known files (don't queue existing files)
        self._initial_scan()
        
        while self.running:
            try:
                self._check_watches()
            except Exception as e:
                optimizarr_logger.app_logger.error("Watcher error: %s", str(e))
            
            # Sleep in small increments so we can stop quickly
            for _ in range(self.poll_interval):
                if not self.running:
                    return
                time.sleep(1)
    
    def _initial_scan(self):
        """Build the initial set of known files (don't queue them)."""
        watches = db.get_folder_watches(enabled_only=True)
        for watch in watches:
            extensions = set(watch.get('extensions', '').split(','))
            files = self._scan_directory(watch['path'], watch.get('recursive', True), extensions)
            with self._lock:
                self._known_files[watch['id']] = files
            optimizarr_logger.app_logger.info(
                "Watcher initialized: %s (%d existing files)", watch['path'], len(files)
            )
    
    def _check_watches(self):
        """Check all enabled watches for new files."""
        watches = db.get_folder_watches(enabled_only=True)
        
        for watch in watches:
            if not watch.get('auto_queue', True):
                continue
            
            extensions = set(watch.get('extensions', '').split(','))
            current_files = self._scan_directory(watch['path'], watch.get('recursive', True), extensions)
            
            with self._lock:
                known = self._known_files.get(watch['id'], set())
                new_files = current_files - known
                
                if new_files:
                    self._queue_new_files(new_files, watch)
                
                self._known_files[watch['id']] = current_files
            
            # Update last check timestamp
            db.update_folder_watch(watch['id'], last_check=datetime.now().isoformat())
    
    def _scan_directory(self, path: str, recursive: bool, extensions: Set[str]) -> Set[str]:
        """Scan a directory and return all matching files."""
        files = set()
        try:
            root_path = Path(path)
            if not root_path.exists():
                return files
            
            if recursive:
                for f in root_path.rglob('*'):
                    if f.is_file() and f.suffix.lower() in extensions:
                        # Skip _optimized files (our own output)
                        if '_optimized' not in f.stem:
                            files.add(str(f))
            else:
                for f in root_path.iterdir():
                    if f.is_file() and f.suffix.lower() in extensions:
                        if '_optimized' not in f.stem:
                            files.add(str(f))
        except PermissionError:
            optimizarr_logger.app_logger.warning("Permission denied: %s", path)
        except Exception as e:
            optimizarr_logger.app_logger.error("Scan error for %s: %s", path, str(e))
        
        return files
    
    def _queue_new_files(self, new_files: Set[str], watch: Dict):
        """Queue newly detected files."""
        # Get existing queue paths to avoid duplicates
        queue_items = db.get_queue_items()
        queued_paths = {item['file_path'] for item in queue_items}
        
        queued_count = 0
        for file_path in sorted(new_files):
            if file_path in queued_paths:
                continue
            
            try:
                file_size = os.path.getsize(file_path)
                db.add_to_queue(
                    file_path=file_path,
                    root_id=None,
                    profile_id=watch['profile_id'],
                    file_size=file_size,
                    status='pending'
                )
                queued_count += 1
            except Exception as e:
                optimizarr_logger.app_logger.error("Failed to queue %s: %s", file_path, str(e))
        
        if queued_count > 0:
            optimizarr_logger.app_logger.info(
                "Watcher auto-queued %d new file(s) from %s", queued_count, watch['path']
            )
    
    def get_status(self) -> Dict:
        """Get watcher status for API."""
        watches = db.get_folder_watches()
        with self._lock:
            known_counts = {wid: len(files) for wid, files in self._known_files.items()}
        
        return {
            'running': self.running,
            'poll_interval': self.poll_interval,
            'total_watches': len(watches),
            'active_watches': len([w for w in watches if w.get('enabled')]),
            'known_files': known_counts
        }
    
    def force_check(self, watch_id: Optional[int] = None) -> Dict:
        """Force an immediate check of one or all watches."""
        watches = db.get_folder_watches(enabled_only=True)
        if watch_id:
            watches = [w for w in watches if w['id'] == watch_id]
        
        total_new = 0
        for watch in watches:
            extensions = set(watch.get('extensions', '').split(','))
            current_files = self._scan_directory(watch['path'], watch.get('recursive', True), extensions)
            
            with self._lock:
                known = self._known_files.get(watch['id'], set())
                new_files = current_files - known
                if new_files:
                    self._queue_new_files(new_files, watch)
                    total_new += len(new_files)
                self._known_files[watch['id']] = current_files
        
        return {'checked': len(watches), 'new_files': total_new}


# Global folder watcher instance
folder_watcher = FolderWatcher(poll_interval=60)
