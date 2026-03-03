"""Simple configuration manager using a JSON file.

The configuration is stored in ``config.json`` at the project root.  The
file contains three keys:

* ``ldplayer_dir``
* ``dnconsole_path``
* ``adb_path``

The module exports helpers to load the configuration (returning sensible
defaults if the file does not exist) and to save a dictionary back to
disk.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict


CONFIG_FILE = Path("config.json")
DEFAULTS: Dict[str, str] = {
    "ldplayer_dir": "",
    "dnconsole_path": "",
    "adb_path": "",
}


def load_config() -> Dict[str, str]:
    """Read configuration from disk.

    If the configuration file cannot be found or parsed, a dictionary of
    defaults is returned (each value an empty string).  The returned value
    is safe to modify; callers may update fields and then call
    ``save_config`` to persist changes.
    """

    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return DEFAULTS.copy()

    # Ensure all expected keys exist; fall back to default if missing.
    result = DEFAULTS.copy()
    result.update({k: str(v) for k, v in cfg.items() if k in result})
    return result


def save_config(cfg: Dict[str, str]) -> None:
    """Write ``cfg`` to the configuration file.

    The dictionary should at least contain the three known keys; extra keys
    are preserved but not required.  Existing file is overwritten.
    """

    # make sure the directory exists
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_FILE.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
