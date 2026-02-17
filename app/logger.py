"""
Comprehensive logging system for Optimizarr.
Multiple log files with rotation, structured statistics, and web UI support.
"""
import logging
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Optional, List, Dict


class OptimizarrLogger:
    """Centralized logging for application, HandBrake, errors, and statistics."""
    
    def __init__(self, log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # Log file paths
        self.log_files = {
            "app": self.log_dir / "optimizarr.log",
            "handbrake": self.log_dir / "handbrake.log",
            "errors": self.log_dir / "errors.log",
        }
        self.stats_file = self.log_dir / "statistics.jsonl"
        
        # Create loggers
        self.app = self._create_logger("optimizarr", self.log_files["app"], logging.INFO)
        self.handbrake = self._create_logger("handbrake", self.log_files["handbrake"], logging.DEBUG)
        self.errors = self._create_logger("errors", self.log_files["errors"], logging.ERROR)
        
        # Also add error handler to app logger so errors show in both
        error_handler = RotatingFileHandler(
            str(self.log_files["errors"]),
            maxBytes=10 * 1024 * 1024,
            backupCount=3
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(self._get_formatter())
        self.app.addHandler(error_handler)
    
    def _get_formatter(self) -> logging.Formatter:
        return logging.Formatter(
            '[%(asctime)s] %(levelname)s [%(name)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
    
    def _create_logger(self, name: str, log_file: Path, level: int) -> logging.Logger:
        """Create a logger with rotating file handler and console output."""
        logger = logging.getLogger(f"optimizarr.{name}")
        logger.setLevel(level)
        
        # Prevent duplicate handlers on reload
        if logger.handlers:
            logger.handlers.clear()
        
        # Rotating file handler (10MB, keep 5 backups)
        fh = RotatingFileHandler(
            str(log_file),
            maxBytes=10 * 1024 * 1024,
            backupCount=5
        )
        fh.setLevel(level)
        fh.setFormatter(self._get_formatter())
        logger.addHandler(fh)
        
        # Console handler (INFO and above)
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(self._get_formatter())
        logger.addHandler(ch)
        
        return logger
    
    # ---- Application Events ----
    
    def log_startup(self, version: str, host: str, port: int):
        self.app.info(f"Optimizarr v{version} starting on {host}:{port}")
    
    def log_shutdown(self):
        self.app.info("Optimizarr shutting down")
    
    def log_scan_start(self, path: str, library_type: str = "custom"):
        self.app.info(f"Scanning library: {path} (type: {library_type})")
    
    def log_scan_complete(self, path: str, files_found: int, duration_seconds: float = 0):
        self.app.info(f"Scan complete: {path} — {files_found} files found ({duration_seconds:.1f}s)")
        self._write_stat({
            "event": "scan_complete",
            "path": path,
            "files_found": files_found,
            "duration_seconds": round(duration_seconds, 1)
        })
    
    def log_scan_error(self, path: str, error: str):
        self.app.error(f"Scan failed: {path} — {error}")
    
    # ---- HandBrake Events ----
    
    def log_handbrake_start(self, file_path: str, command: List[str]):
        filename = Path(file_path).name
        cmd_str = ' '.join(command)
        self.handbrake.info(f"Starting transcode: {filename}")
        self.handbrake.debug(f"Command: {cmd_str}")
    
    def log_handbrake_progress(self, file_path: str, progress: float):
        filename = Path(file_path).name
        self.handbrake.debug(f"Progress: {filename} — {progress:.1f}%")
    
    def log_handbrake_complete(self, file_path: str, stats: Dict):
        filename = Path(file_path).name
        original_mb = stats.get("original_size_mb", 0)
        new_mb = stats.get("new_size_mb", 0)
        savings = stats.get("savings_percent", 0)
        duration = stats.get("duration_seconds", 0)
        
        self.handbrake.info(
            f"Completed: {filename} — "
            f"{original_mb:.0f}MB → {new_mb:.0f}MB "
            f"({savings:.1f}% saved, {duration:.0f}s)"
        )
        self.app.info(
            f"Transcode complete: {filename} — "
            f"{savings:.1f}% space saved"
        )
        
        self._write_stat({
            "event": "transcode_complete",
            "file": filename,
            "original_size_mb": round(original_mb, 1),
            "new_size_mb": round(new_mb, 1),
            "savings_percent": round(savings, 1),
            "duration_seconds": round(duration, 1)
        })
    
    def log_handbrake_error(self, file_path: str, error: str):
        filename = Path(file_path).name
        self.handbrake.error(f"Transcode failed: {filename} — {error}")
        self.errors.error(f"Transcode failed: {filename} — {error}")
        
        self._write_stat({
            "event": "transcode_error",
            "file": filename,
            "error": str(error)[:200]
        })
    
    def log_handbrake_output(self, line: str):
        """Log raw HandBrake stderr/stdout output."""
        self.handbrake.debug(f"[HB] {line.strip()}")
    
    # ---- Queue Events ----
    
    def log_queue_add(self, file_path: str, profile_name: str):
        filename = Path(file_path).name
        self.app.info(f"Queued: {filename} (profile: {profile_name})")
    
    def log_queue_clear(self, count: int, status: Optional[str] = None):
        if status:
            self.app.info(f"Cleared {count} queue items (status: {status})")
        else:
            self.app.info(f"Cleared {count} queue items (all)")
    
    # ---- Statistics ----
    
    def _write_stat(self, data: Dict):
        """Write a structured JSON stat line."""
        data["timestamp"] = datetime.now().isoformat()
        try:
            with open(self.stats_file, "a") as f:
                f.write(json.dumps(data) + "\n")
        except Exception:
            pass  # Don't let stats logging break anything
    
    # ---- Web UI API ----
    
    def get_logs(self, log_type: str = "app", lines: int = 100, level: str = "ALL") -> Dict:
        """Read log entries for the web UI."""
        log_file = self.log_files.get(log_type)
        if not log_file or not log_file.exists():
            return {"logs": [], "total": 0, "log_type": log_type}
        
        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
            
            # Filter by level
            if level != "ALL":
                all_lines = [l for l in all_lines if f"] {level} [" in l]
            
            # Return last N lines
            recent = all_lines[-lines:]
            
            return {
                "logs": [l.rstrip() for l in recent],
                "total": len(all_lines),
                "showing": len(recent),
                "log_type": log_type
            }
        except Exception as e:
            return {"logs": [], "total": 0, "error": str(e), "log_type": log_type}
    
    def get_statistics(self, days: int = 7) -> Dict:
        """Read structured statistics for the web UI."""
        if not self.stats_file.exists():
            return {"statistics": [], "days": days}
        
        cutoff = datetime.now() - timedelta(days=days)
        stats = []
        
        try:
            with open(self.stats_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        ts = datetime.fromisoformat(entry.get("timestamp", ""))
                        if ts >= cutoff:
                            stats.append(entry)
                    except (json.JSONDecodeError, ValueError):
                        continue
        except Exception:
            pass
        
        # Summary
        total_transcodes = len([s for s in stats if s.get("event") == "transcode_complete"])
        total_errors = len([s for s in stats if s.get("event") == "transcode_error"])
        total_saved_mb = sum(
            s.get("original_size_mb", 0) - s.get("new_size_mb", 0)
            for s in stats if s.get("event") == "transcode_complete"
        )
        avg_savings = 0
        if total_transcodes > 0:
            avg_savings = sum(
                s.get("savings_percent", 0) for s in stats 
                if s.get("event") == "transcode_complete"
            ) / total_transcodes
        
        return {
            "statistics": stats,
            "summary": {
                "total_transcodes": total_transcodes,
                "total_errors": total_errors,
                "total_saved_mb": round(total_saved_mb, 1),
                "avg_savings_percent": round(avg_savings, 1)
            },
            "days": days
        }
    
    def clear_log(self, log_type: str) -> bool:
        """Clear a log file."""
        log_file = self.log_files.get(log_type)
        if log_file and log_file.exists():
            try:
                with open(log_file, "w") as f:
                    f.write("")
                self.app.info(f"Log cleared: {log_type}")
                return True
            except Exception:
                return False
        return False


# Global logger instance
optimizarr_logger = OptimizarrLogger()
