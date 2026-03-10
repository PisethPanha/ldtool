from __future__ import annotations
from src.utils.resource_path import resource_path

def icon_path(filename: str) -> str:
    """Return an absolute path for icon files under assets/icons using resource_path."""
    return resource_path(f"assets/icons/{filename}")
