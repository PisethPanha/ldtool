"""Utilities for locating LDPlayer and related executables.

The primary operations are to search a provided LDPlayer root directory for
`dnconsole.exe` and `adb.exe`, as well as to validate that given paths
point to existing executables.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple


def _is_executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def find_dnconsole(ld_dir: str) -> Optional[str]:
    """Return full path to `dnconsole.exe` if found below ``ld_dir``.

    LDPlayer installations often place the helper under one of a few
    subdirectories; we look in the most common ones and return the first
    match.  If ``ld_dir`` does not exist or nothing is found, ``None`` is
    returned.
    """

    if not ld_dir:
        return None

    root = Path(ld_dir)
    if not root.exists():
        return None

    candidates = [
        root / "dnconsole.exe",
        root / "LDPlayer9" / "dnconsole.exe",
        root / "LDPlayer4" / "dnconsole.exe",
        root / "vms" / "dnconsole.exe",
    ]

    for p in candidates:
        if _is_executable(p):
            return str(p)
    return None


def find_adb(ld_dir: str) -> Optional[str]:
    """Return path to ``adb.exe`` somewhere under ``ld_dir``.

    Performs a recursive search; returns the first match found.  If the
    directory does not exist or no ``adb.exe`` is present, ``None`` is
    returned.
    """

    if not ld_dir:
        return None

    root = Path(ld_dir)
    if not root.exists():
        return None

    for p in root.rglob("adb.exe"):
        if _is_executable(p):
            return str(p)
    return None


def validate_paths(dnconsole_path: str, adb_path: str) -> Tuple[bool, str]:
    """Return ``(True, "")`` if both paths exist and are executable.

    Otherwise returns ``(False, reason)`` describing the failure.
    """

    if not dnconsole_path:
        return False, "dnconsole path is empty"
    if not adb_path:
        return False, "adb path is empty"

    dn = Path(dnconsole_path)
    ad = Path(adb_path)

    if not dn.is_file():
        return False, f"dnconsole not found at {dn}"
    if not _is_executable(dn):
        return False, f"dnconsole at {dn} is not executable"

    if not ad.is_file():
        return False, f"adb not found at {ad}"
    if not _is_executable(ad):
        return False, f"adb at {ad} is not executable"

    return True, ""  
