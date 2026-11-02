"""Startup check: are requirements.txt's packages installed in this Python?

The launcher falls back to the system Python when there's no project venv,
and a package missing there (cryptography) made every Sonarr/Radarr save
crash with nothing shown. Missing packages are logged as a warning at
startup with the exact pip command for the interpreter that's running.
"""
import importlib.metadata as metadata
import re
import sys
from pathlib import Path
from typing import List

REQUIREMENTS = Path(__file__).resolve().parent.parent / "requirements.txt"


def missing_requirements(path: Path = REQUIREMENTS) -> List[str]:
    """Requirement lines whose package isn't installed (versions not checked)."""
    missing = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return missing
    for line in lines:
        req = line.split("#", 1)[0].strip()
        if not req:
            continue
        name = re.split(r"[<>=!~\[; ]", req, maxsplit=1)[0]
        try:
            metadata.version(name)
        except metadata.PackageNotFoundError:
            missing.append(req)
    return missing


def warn_missing():
    from app.logger import optimizarr_logger
    missing = missing_requirements()
    if missing:
        quoted = " ".join(f'"{m}"' for m in missing)
        optimizarr_logger.app_logger.warning(
            "Missing Python packages: %s — some features will fail until installed. "
            "Run: \"%s\" -m pip install %s", ", ".join(missing), sys.executable, quoted)
