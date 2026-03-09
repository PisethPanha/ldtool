"""Runner for LDPlayer native .record macros via dnconsole.

Uses the ``operatelist``, ``operateinfo``, and ``operaterecord`` commands
to list, inspect, and play back macros inside the LDPlayer engine.

Every dnconsole invocation captures return-code, stdout, and stderr so
that callers get truthful success/failure rather than fire-and-forget
optimism.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any, Callable, Dict, List, Optional, Tuple

# Known failure phrases in dnconsole output that indicate an error
# even when the process exit code is 0.
_FAILURE_PHRASES = [
    "unknown command",
    "invalid parameter",
    "file not found",
    "instance not found",
    "not found",
    "error",
    "failed",
]

# File-based diagnostic logger (writes to macro_debug.log in cwd)
_diag_logger = logging.getLogger("ldtool.macro_diag")
if not _diag_logger.handlers:
    _fh = logging.FileHandler("macro_debug.log", encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s  %(message)s"))
    _diag_logger.addHandler(_fh)
    _diag_logger.setLevel(logging.DEBUG)


@dataclass
class CommandResult:
    """Structured result of a single dnconsole invocation."""

    ok: bool
    returncode: int
    stdout: str
    stderr: str
    message: str  # human-readable summary


class LDPlayerMacroRunner:
    """Manages playback of LDPlayer .record macros on emulator instances."""

    def __init__(
        self,
        dnconsole_path: str,
        log_fn: Callable[[str], None] = lambda m: None,
    ):
        self.dnconsole = Path(dnconsole_path)
        self._log = log_fn
        self._verified: Optional[bool] = None  # cached capability flag

    # ------------------------------------------------------------------
    # Low-level execution
    # ------------------------------------------------------------------
    def _decode(self, raw: bytes) -> str:
        """Decode dnconsole output trying gbk first (LDPlayer default)."""
        encodings = ["gbk"] + (["mbcs"] if sys.platform == "win32" else []) + ["utf-8"]
        for enc in encodings:
            try:
                text = raw.decode(enc)
                if text.strip().startswith(("{", "[")):
                    try:
                        json.loads(text)
                        return text
                    except (json.JSONDecodeError, ValueError):
                        pass
                return text
            except (UnicodeDecodeError, LookupError):
                continue
        return raw.decode("utf-8", errors="replace")

    def _run_cmd(
        self,
        args: List[str],
        label: str = "",
        timeout: int = 30,
    ) -> CommandResult:
        """Execute a dnconsole command with full result capture.

        *label* is used in log lines (e.g. the instance name).
        """
        cmd = [str(self.dnconsole)] + args
        cmd_str = " ".join(cmd)
        tag = f"[{label}] " if label else ""

        self._log(f"{tag}Running: {cmd_str}")

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            msg = f"{tag}Command timed out after {timeout}s"
            self._log(msg)
            _diag_logger.error("%s | cmd=%s | TIMEOUT", label, cmd_str)
            return CommandResult(ok=False, returncode=-1, stdout="", stderr="", message=msg)
        except Exception as exc:
            msg = f"{tag}Failed to execute: {exc}"
            self._log(msg)
            _diag_logger.error("%s | cmd=%s | EXEC_ERROR: %s", label, cmd_str, exc)
            return CommandResult(ok=False, returncode=-1, stdout="", stderr="", message=msg)

        stdout = self._decode(proc.stdout)
        stderr = self._decode(proc.stderr)

        # Log raw output
        if stdout.strip():
            self._log(f"{tag}stdout: {stdout.strip()}")
        if stderr.strip():
            self._log(f"{tag}stderr: {stderr.strip()}")

        # Diagnostic file log
        _diag_logger.info(
            "%s | cmd=%s | rc=%d | stdout=%s | stderr=%s",
            label, cmd_str, proc.returncode,
            stdout.strip()[:500], stderr.strip()[:500],
        )

        # Evaluate success
        if proc.returncode != 0:
            msg = f"{tag}Command failed (rc={proc.returncode}): {stderr.strip() or stdout.strip()}"
            self._log(msg)
            return CommandResult(ok=False, returncode=proc.returncode, stdout=stdout, stderr=stderr, message=msg)

        # Check for known failure phrases in output
        combined = (stdout + stderr).lower()
        for phrase in _FAILURE_PHRASES:
            if phrase in combined:
                # Exceptions: JSON responses with "code": 0 are OK
                try:
                    data = json.loads(stdout)
                    if isinstance(data, dict) and data.get("code") == 0:
                        break
                except (json.JSONDecodeError, ValueError):
                    pass
                msg = f"{tag}Command output contains failure indicator: '{phrase}'"
                self._log(msg)
                return CommandResult(ok=False, returncode=proc.returncode, stdout=stdout, stderr=stderr, message=msg)

        return CommandResult(ok=True, returncode=proc.returncode, stdout=stdout, stderr=stderr, message="ok")

    # ------------------------------------------------------------------
    # Capability verification
    # ------------------------------------------------------------------
    def verify_macro_support(self) -> Tuple[bool, str]:
        """Check if this dnconsole build has operaterecord support.

        Runs the help output and searches for `operaterecord`.
        Result is cached after first call.
        """
        if self._verified is not None:
            return self._verified, "" if self._verified else "operaterecord not found in help"

        if not self.dnconsole.is_file():
            self._verified = False
            return False, f"dnconsole not found: {self.dnconsole}"

        # Run dnconsole with no args to get help text
        res = self._run_cmd([], label="verify", timeout=10)
        help_text = (res.stdout + res.stderr).lower()

        if "operaterecord" in help_text:
            self._verified = True
            self._log("Verified: operaterecord command available")
            return True, ""

        self._verified = False
        msg = (
            "This LDPlayer build does not expose a verified CLI "
            "macro playback command (operaterecord not found in help output)."
        )
        self._log(msg)
        return False, msg

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def list_records(self, index: int) -> List[Dict[str, str]]:
        """Return the list of .record files available on an instance."""
        res = self._run_cmd(["operatelist", "--index", str(index)], label=f"idx-{index}")
        if not res.ok:
            return []
        try:
            data = json.loads(res.stdout)
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, ValueError):
            self._log("Failed to parse operatelist JSON")
        return []

    def record_info(self, index: int, filename: str) -> Optional[Dict[str, Any]]:
        """Retrieve metadata for a specific .record file."""
        res = self._run_cmd(
            ["operateinfo", "--index", str(index), "--file", filename],
            label=f"idx-{index}",
        )
        if not res.ok:
            return None
        try:
            data = json.loads(res.stdout)
            if data.get("code") == 0:
                return data.get("info")
        except (json.JSONDecodeError, ValueError):
            pass
        return None

    def run_ldplayer_macro_command(
        self,
        instance_index: int,
        instance_name: str,
        filename: str,
        loop: int = 1,
        interval: int = 0,
    ) -> Tuple[bool, str]:
        """Run a macro playback command with full validation.

        Returns ``(True, detail)`` only when the command actually succeeds.
        Returns ``(False, detail)`` with a diagnostic message otherwise.
        """
        tag = instance_name or f"idx-{instance_index}"

        # Pre-flight checks
        if not self.dnconsole.is_file():
            return False, f"[{tag}] dnconsole not found: {self.dnconsole}"
        if not filename:
            return False, f"[{tag}] No record file specified"

        payload = json.dumps({
            "playback": {
                "file": filename,
                "loop": loop,
                "interval": interval,
            }
        })

        res = self._run_cmd(
            ["operaterecord", "--index", str(instance_index), "--content", payload],
            label=tag,
        )

        if not res.ok:
            return False, res.message

        # Parse the JSON response for confirmation
        try:
            data = json.loads(res.stdout)
            if data.get("code") == 0:
                self._log(f"[{tag}] Macro playback confirmed by LDPlayer")
                return True, f"[{tag}] Playback started successfully"
            else:
                msg = f"[{tag}] LDPlayer returned code {data.get('code')}"
                self._log(msg)
                return False, msg
        except (json.JSONDecodeError, ValueError):
            msg = f"[{tag}] Could not parse playback response"
            self._log(msg)
            return False, msg

    def stop_playback(self, index: int, label: str = "") -> bool:
        """Stop any active macro playback on the given instance."""
        tag = label or f"idx-{index}"
        payload = json.dumps({"stopplayback": {}})
        res = self._run_cmd(
            ["operaterecord", "--index", str(index), "--content", payload],
            label=tag,
        )
        if not res.ok:
            return False
        try:
            data = json.loads(res.stdout)
            return data.get("code") == 0
        except (json.JSONDecodeError, ValueError):
            return False

    # ------------------------------------------------------------------
    # High-level worker (designed to run in a background thread)
    # ------------------------------------------------------------------
    def run_macro_loop(
        self,
        index: int,
        instance_name: str,
        filename: str,
        loops: int,
        delay_between_loops: int,
        stop_event: Event,
        pause_event: Optional[Event] = None,
        log_fn: Optional[Callable[[str], None]] = None,
        progress_fn: Optional[Callable[[int, int], None]] = None,
    ) -> Dict[str, Any]:
        """Execute the macro for *loops* iterations with delay between each.

        This method blocks until all loops finish, the stop_event is set,
        or an error occurs.  It is meant to be called from a worker thread.

        ``progress_fn`` is called with ``(instance_index, percent)`` after
        each loop completes.
        """
        _log = log_fn or self._log
        result: Dict[str, Any] = {"success": True, "errors": []}
        tag = instance_name

        # Verify capability first
        supported, reason = self.verify_macro_support()
        if not supported:
            _log(f"[{tag}] {reason}")
            result["success"] = False
            result["errors"].append(reason)
            result["status"] = "unsupported"
            return result

        for loop_num in range(1, loops + 1):
            # honour pause
            if pause_event is not None:
                while not pause_event.is_set():
                    if stop_event.is_set():
                        break
                    time.sleep(0.2)

            if stop_event.is_set():
                result["success"] = False
                result["errors"].append("stopped")
                _log(f"[{tag}] Macro stopped")
                break

            _log(f"[{tag}] Loop {loop_num}/{loops} — launching playback")

            # Start playback with validated command
            ok, detail = self.run_ldplayer_macro_command(
                instance_index=index,
                instance_name=tag,
                filename=filename,
                loop=1,
                interval=0,
            )
            if not ok:
                _log(f"[{tag}] Playback FAILED: {detail}")
                result["success"] = False
                result["errors"].append(detail)
                result["status"] = "failed"
                break

            _log(f"[{tag}] Playback confirmed — waiting for completion")

            # Get record info to know playback duration
            info = self.record_info(index, filename)
            duration_ms = 30000  # fallback 30s
            if info and info.get("circleDuration"):
                duration_ms = int(info["circleDuration"])

            # Wait for playback to finish (chunk-sleep for responsiveness)
            wait_ms = duration_ms + 2000
            elapsed = 0
            while elapsed < wait_ms:
                if stop_event.is_set():
                    self.stop_playback(index, label=tag)
                    result["success"] = False
                    result["errors"].append("stopped")
                    _log(f"[{tag}] Macro stopped mid-playback")
                    break
                if pause_event is not None and not pause_event.is_set():
                    self.stop_playback(index, label=tag)
                    _log(f"[{tag}] Paused")
                    while not pause_event.is_set():
                        if stop_event.is_set():
                            break
                        time.sleep(0.2)
                    if stop_event.is_set():
                        break
                    _log(f"[{tag}] Resumed — restarting playback")
                    ok2, _ = self.run_ldplayer_macro_command(
                        index, tag, filename, loop=1, interval=0,
                    )
                    if not ok2:
                        result["success"] = False
                        result["errors"].append("resume failed")
                        break
                chunk = min(500, wait_ms - elapsed)
                time.sleep(chunk / 1000.0)
                elapsed += chunk

            if stop_event.is_set():
                break
            if not result["success"]:
                break

            # emit progress
            if progress_fn is not None:
                pct = int(loop_num * 100 / loops)
                try:
                    progress_fn(index, pct)
                except Exception:
                    pass

            # delay between loops
            if loop_num < loops and delay_between_loops > 0:
                _log(f"[{tag}] Waiting {delay_between_loops}s before next loop")
                waited = 0.0
                while waited < delay_between_loops:
                    if stop_event.is_set():
                        break
                    time.sleep(min(0.5, delay_between_loops - waited))
                    waited += 0.5

        if result["success"]:
            _log(f"[{tag}] Macro finished ({loops} loop(s))")
            result["status"] = "success"
        elif "status" not in result:
            result["status"] = "failed"
        return result
