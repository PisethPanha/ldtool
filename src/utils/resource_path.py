from pathlib import Path
import sys

def resource_path(relative_path: str) -> str:
    """
    Get absolute path to resource for both dev and PyInstaller builds.
    """
    if getattr(sys, 'frozen', False):
        base_path = Path(sys._MEIPASS)
    else:
        base_path = Path(__file__).resolve().parents[2]

    return str(base_path / relative_path)
