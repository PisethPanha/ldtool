"""Controller for managing LDPlayer instances via dnconsole.exe.

Provides simple wrappers around the command‑line utility, translating
output into Python data structures and offering start/stop operations.  A
logging callback may be supplied to receive status messages.
"""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple


class LDPlayerController:
    def __init__(
        self,
        dnconsole_path: str,
        log_fn: Callable[[str], None] = lambda msg: None,
    ):
        """Create a controller using the specified dnconsole executable.

        ``log_fn`` will be called with informational messages and errors.
        """

        self.dnconsole = Path(dnconsole_path)
        self._log = log_fn

    # ------------------------------------------------------------------
    # Instance management
    # ------------------------------------------------------------------
    def list_instances(self) -> List[Dict[str, object]]:
        """Return a list of dictionaries describing available instances.

        Each dictionary contains the keys ``index`` (int), ``name`` (str),
        ``is_running`` (bool), ``width`` (int), and ``height`` (int).  If
        the command fails or output cannot be parsed, an empty list is
        returned and an error is logged.
        """

        cmd = [str(self.dnconsole), "list2"]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, check=True
            )
        except subprocess.CalledProcessError as exc:
            self._log(f"dnconsole list failed: {exc}")
            return []
        except Exception as exc:  # pragma: no cover - defensive
            self._log(f"unexpected error running dnconsole: {exc}")
            return []

        # Decode with fallback chain: prefer platform-native, then gbk, then utf-8
        encodings = (
            ["mbcs"] if sys.platform == "win32" else []
        ) + ["gbk", "utf-8"]
        text = None
        for enc in encodings:
            try:
                text = proc.stdout.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue

        if text is None:
            # Last resort: decode with errors='replace'
            text = proc.stdout.decode("utf-8", errors="replace")

        instances: List[Dict[str, object]] = []
        reader = csv.reader(text.splitlines())

        for row in reader:
            # Skip empty rows or rows with too few columns
            if not row or len(row) < 2:
                continue
            # Skip header-like rows
            if row[0].lower() in ("index", "name"):
                continue

            # Parse index; skip if not an integer
            try:
                idx = int(row[0])
            except ValueError:
                continue

            # Extract fields
            name = row[1] if len(row) > 1 else ""
            is_running = row[2] == "1" if len(row) > 2 else False

            # Extract width and height from last 3 columns if available
            width = 0
            height = 0
            if len(row) >= 3:
                try:
                    # Assume width is at -3, height at -2 in the last 3 columns
                    if len(row) >= 3:
                        val = row[-3]
                        width = int(val) if val and self._is_valid_int(val) else 0
                        val = row[-2]
                        height = int(val) if val and self._is_valid_int(val) else 0
                except (ValueError, IndexError):
                    pass

            instances.append(
                {
                    "index": idx,
                    "name": name,
                    "is_running": is_running,
                    "width": width,
                    "height": height,
                }
            )

        self._log(f"found {len(instances)} instance(s)")
        return instances

    @staticmethod
    def _is_valid_int(s: str) -> bool:
        """Check if a string can be safely converted to int."""
        try:
            int(s)
            return True
        except ValueError:
            return False

    def start_instance(self, index: int) -> bool:
        """Start the instance with the given index.

        Returns ``True`` if the command succeeded (exit code 0), ``False``
        otherwise.  Errors are logged.
        """

        ok, _ = self._run_dnconsole_command("launch", index)
        return ok

    def stop_instance(self, index: int) -> bool:
        """Terminate the instance with the given index.

        Returns ``True`` if the command succeeded, ``False`` on failure.
        """

        ok, _ = self._run_dnconsole_command("quit", index)
        return ok

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _run_dnconsole_command(self, cmd_name: str, index: int) -> Tuple[bool, str]:
        cmd = [str(self.dnconsole), cmd_name, "--index", str(index)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                self._log(f"dnconsole {cmd_name} failed: {proc.stderr.strip()}")
                return False, proc.stderr.strip()
            self._log(f"dnconsole {cmd_name}({index}) succeeded")
            return True, proc.stdout.strip()
        except Exception as exc:  # pragma: no cover - defensive
            self._log(f"error running dnconsole {cmd_name}: {exc}")
            return False, str(exc)


class ADBManager:
    """Helper wrapper around `adbutils` to interact with devices.

    Timeouts and errors are caught and surfaced via return values.  A
    logging callback may be passed but is not strictly required.
    """

    def __init__(self, adb_path: str, log_fn: Callable[[str], None] = lambda msg: None):
        self._adb_path = adb_path
        self._log = log_fn

        # ensure adbutils uses the provided executable when spawning
        import adbutils

        adbutils.ADB_PATH = adb_path
        self._adb = adbutils.AdbClient()

    def list_devices(self) -> List[str]:
        try:
            return [d.serial for d in self._adb.device_list()]
        except Exception as exc:  # pragma: no cover - defensive
            self._log(f"error listing devices: {exc}")
            return []

    def connect_localhost_ports(self, ports: List[int]) -> List[str]:
        connected: List[str] = []
        for port in ports:
            target = f"127.0.0.1:{port}"
            try:
                self._adb.connect(target, timeout_s=5)
                connected.append(target)
            except Exception as exc:
                self._log(f"failed to connect to {target}: {exc}")
        return connected

    def shell(self, serial: str, cmd: str) -> str:
        try:
            dev = self._adb.device(serial=serial)
            return dev.shell(cmd, timeout=10)
        except Exception as exc:  # pragma: no cover - defensive
            self._log(f"shell error on {serial}: {exc}")
            return ""

    def is_device_ready(self, serial: str) -> bool:
        # check property sys.boot_completed
        val = self.shell(serial, "getprop sys.boot_completed")
        return val.strip() == "1"
