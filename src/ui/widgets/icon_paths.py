from __future__ import annotations

from pathlib import Path


def icon_path(filename: str) -> str:
    """Return an absolute path for icon files under project ui/icons."""
    project_root = Path(__file__).resolve().parents[3]
    return str(project_root / "ui" / "icons" / filename)
