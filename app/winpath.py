"""Windows long paths.

Past MAX_PATH (260 characters) the Win32 file APIs fail on a plain path
unless the OS opts in (the LongPathsEnabled registry value, off by
default). HandBrake writes such paths fine, so a finished encode at
263 characters looked "not found" to Optimizarr. The extended-length
``\\\\?\\`` prefix lifts the limit regardless of that setting.
"""
import os

MAX_PATH = 260
# Most file names may be at most this long (one path component, NTFS).
MAX_COMPONENT = 255
# Directories stop at MAX_PATH minus room for an 8.3 file name, so prefix
# a little early.
_PREFIX_FROM = MAX_PATH - 12


def fs_path(path) -> str:
    """``path`` in a form the file APIs accept at any length. Use it only
    for file operations — store, log and show the plain path."""
    p = str(path)
    if os.name != 'nt' or p.startswith('\\\\?\\') or len(p) < _PREFIX_FROM:
        return p
    p = os.path.abspath(p)
    if p.startswith('\\\\'):                     # \\server\share\...
        return '\\\\?\\UNC\\' + p[2:]
    return '\\\\?\\' + p
