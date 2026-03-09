"""Fallback macro runner that clicks LDPlayer's Operation Recorder UI.

When the dnconsole CLI ``operaterecord`` command is unavailable this module
provides an alternative: it finds each LDPlayer instance window, activates
it, then clicks the Operation Recorder toolbar button and the target macro
entry using Win32 desktop automation.

Dependencies (all bundled with pywin32 / stdlib):
    win32gui, win32api, win32con, ctypes, PIL (Pillow — for failure screenshots)
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import win32api
    import win32con
    import win32gui
except ImportError:
    raise ImportError("pywin32 is required; install with: pip install pywin32")

from src.core.window_manager import WindowManager

# ---------------------------------------------------------------------------
# Diagnostic file logger
# ---------------------------------------------------------------------------
_diag = logging.getLogger("ldtool.desktop_macro")
if not _diag.handlers:
    _fh = logging.FileHandler("desktop_macro_debug.log", encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s  %(message)s"))
    _diag.addHandler(_fh)
    _diag.setLevel(logging.DEBUG)

# Per-window lock so two workers never click the same window concurrently.
_window_locks: Dict[int, threading.Lock] = {}
_window_locks_guard = threading.Lock()

SCREENSHOT_DIR = Path("screenshots")


def _get_window_lock(hwnd: int) -> threading.Lock:
    with _window_locks_guard:
        if hwnd not in _window_locks:
            _window_locks[hwnd] = threading.Lock()
        return _window_locks[hwnd]


# ---------------------------------------------------------------------------
# Calibration config
# ---------------------------------------------------------------------------
@dataclass
class DesktopClickCalibration:
    """Relative coordinates (0.0 – 1.0) for the macro UI within an LDPlayer
    window.  Coordinates are fractions of window width / height so they work
    regardless of window size or monitor DPI.
    """

    # Macro / Operation Recorder toolbar button
    macro_toolbar_rel_x: float = 0.985
    macro_toolbar_rel_y: float = 0.30

    # First macro row inside the macro panel
    macro_item_rel_x: float = 0.93
    macro_item_rel_y: float = 0.55

    # Optional close button for the macro panel
    close_panel_rel_x: float = 0.0
    close_panel_rel_y: float = 0.0

    # Extra delay (ms) after opening the macro panel
    panel_open_delay_ms: int = 800

    @classmethod
    def from_config(cls, cfg: Dict[str, Any]) -> "DesktopClickCalibration":
        """Build from a flat config dict (keys prefixed ``dc_``)."""
        return cls(
            macro_toolbar_rel_x=float(cfg.get("dc_macro_toolbar_rel_x", cls.macro_toolbar_rel_x)),
            macro_toolbar_rel_y=float(cfg.get("dc_macro_toolbar_rel_y", cls.macro_toolbar_rel_y)),
            macro_item_rel_x=float(cfg.get("dc_macro_item_rel_x", cls.macro_item_rel_x)),
            macro_item_rel_y=float(cfg.get("dc_macro_item_rel_y", cls.macro_item_rel_y)),
            close_panel_rel_x=float(cfg.get("dc_close_panel_rel_x", cls.close_panel_rel_x)),
            close_panel_rel_y=float(cfg.get("dc_close_panel_rel_y", cls.close_panel_rel_y)),
            panel_open_delay_ms=int(cfg.get("dc_panel_open_delay_ms", cls.panel_open_delay_ms)),
        )

    def to_config(self) -> Dict[str, str]:
        """Flatten for storage in config.json."""
        return {
            "dc_macro_toolbar_rel_x": str(self.macro_toolbar_rel_x),
            "dc_macro_toolbar_rel_y": str(self.macro_toolbar_rel_y),
            "dc_macro_item_rel_x": str(self.macro_item_rel_x),
            "dc_macro_item_rel_y": str(self.macro_item_rel_y),
            "dc_close_panel_rel_x": str(self.close_panel_rel_x),
            "dc_close_panel_rel_y": str(self.close_panel_rel_y),
            "dc_panel_open_delay_ms": str(self.panel_open_delay_ms),
        }


# ---------------------------------------------------------------------------
# Low-level click helpers (win32)
# ---------------------------------------------------------------------------

def _click_at(x: int, y: int) -> None:
    """Move the cursor to (x, y) screen coords and click."""
    ctypes.windll.user32.SetCursorPos(x, y)
    time.sleep(0.05)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
    time.sleep(0.03)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)


def _get_window_rect(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    """Return (left, top, right, bottom) or None."""
    try:
        return win32gui.GetWindowRect(hwnd)
    except Exception:
        return None


def _is_minimized(hwnd: int) -> bool:
    try:
        return bool(win32gui.IsIconic(hwnd))
    except Exception:
        return False


def _is_foreground(hwnd: int) -> bool:
    try:
        return win32gui.GetForegroundWindow() == hwnd
    except Exception:
        return False


def _capture_window_screenshot(hwnd: int, instance_name: str) -> Optional[str]:
    """Capture a screenshot of the given window area and save to disk.

    Returns the file path on success, or None.
    """
    try:
        from PIL import ImageGrab

        rect = _get_window_rect(hwnd)
        if rect is None:
            return None
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in instance_name)
        fname = SCREENSHOT_DIR / f"{safe_name}_{ts}.png"
        img = ImageGrab.grab(bbox=rect)
        img.save(str(fname))
        return str(fname)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public modular API
# ---------------------------------------------------------------------------

def find_instance_window(instance_name: str) -> Optional[int]:
    """Find an LDPlayer window whose title contains *instance_name*.

    Returns the hwnd or ``None``.
    """
    matches = WindowManager.find_windows_by_title_keywords([instance_name])
    hwnd = matches.get(instance_name)
    if hwnd:
        _diag.info("find_instance_window(%s) → hwnd=%s title=%s",
                    instance_name, hwnd, win32gui.GetWindowText(hwnd))
    else:
        _diag.warning("find_instance_window(%s) → NOT FOUND", instance_name)
    return hwnd


def activate_instance_window(
    hwnd: int,
    retries: int = 3,
    log_fn: Callable[[str], None] = lambda m: None,
) -> bool:
    """Restore (if minimized) and bring *hwnd* to the foreground.

    Retries up to *retries* times with small delays.
    """
    for attempt in range(1, retries + 1):
        try:
            if _is_minimized(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                time.sleep(0.3)

            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.4 + 0.1 * attempt)  # 400–600 ms settle

            if _is_foreground(hwnd):
                _diag.info("activate hwnd=%s attempt=%d OK", hwnd, attempt)
                return True

            log_fn(f"Window not yet foreground (attempt {attempt}/{retries})")
        except Exception as exc:
            log_fn(f"Activation error (attempt {attempt}): {exc}")
            _diag.error("activate hwnd=%s attempt=%d error=%s", hwnd, attempt, exc)
        time.sleep(0.3)

    _diag.warning("activate hwnd=%s FAILED after %d retries", hwnd, retries)
    return False


def click_macro_toolbar(
    hwnd: int,
    cal: DesktopClickCalibration,
    retries: int = 3,
    log_fn: Callable[[str], None] = lambda m: None,
) -> bool:
    """Click the Operation Recorder toolbar button inside *hwnd*.

    Uses calibrated relative coordinates translated to absolute screen coords.
    Retries up to *retries* times.
    """
    for attempt in range(1, retries + 1):
        rect = _get_window_rect(hwnd)
        if rect is None:
            log_fn(f"Cannot get window rect (attempt {attempt})")
            time.sleep(0.3)
            continue

        left, top, right, bottom = rect
        w = right - left
        h = bottom - top
        cx = left + int(w * cal.macro_toolbar_rel_x)
        cy = top + int(h * cal.macro_toolbar_rel_y)

        log_fn(f"Clicking macro toolbar at ({cx}, {cy}) [attempt {attempt}]")
        _diag.info("click_macro_toolbar hwnd=%s rect=%s click=(%d,%d) attempt=%d",
                    hwnd, rect, cx, cy, attempt)

        _click_at(cx, cy)

        # Wait for panel to open
        time.sleep(cal.panel_open_delay_ms / 1000.0)
        return True

    return False


def click_macro_item(
    hwnd: int,
    macro_name: str,
    cal: DesktopClickCalibration,
    retries: int = 3,
    log_fn: Callable[[str], None] = lambda m: None,
) -> bool:
    """Click the target macro entry inside the macro panel of *hwnd*.

    Since text detection within rendered LDPlayer UI is unreliable we fall
    back to calibrated relative coordinates.  *macro_name* is logged for
    diagnostics but not used for pixel detection.
    """
    for attempt in range(1, retries + 1):
        rect = _get_window_rect(hwnd)
        if rect is None:
            log_fn(f"Cannot get window rect for macro item click (attempt {attempt})")
            time.sleep(0.3)
            continue

        left, top, right, bottom = rect
        w = right - left
        h = bottom - top
        cx = left + int(w * cal.macro_item_rel_x)
        cy = top + int(h * cal.macro_item_rel_y)

        log_fn(f"Clicking macro '{macro_name}' at ({cx}, {cy}) [attempt {attempt}]")
        _diag.info("click_macro_item hwnd=%s macro=%s click=(%d,%d) attempt=%d",
                    hwnd, macro_name, cx, cy, attempt)

        _click_at(cx, cy)
        time.sleep(0.5)
        return True

    return False


def run_macro_via_desktop_click(
    instance_name: str,
    macro_name: str,
    cal: DesktopClickCalibration,
    log_fn: Callable[[str], None] = lambda m: None,
) -> Tuple[bool, str]:
    """Full flow: find window → activate → click toolbar → click macro item.

    Returns ``(True, detail)`` on success, ``(False, detail)`` on failure.
    Captures a screenshot on failure.
    """
    tag = f"[{instance_name}]"
    log_fn(f"{tag} Desktop click fallback: starting")
    _diag.info("run_macro_via_desktop_click instance=%s macro=%s", instance_name, macro_name)

    # 1. Find window
    hwnd = find_instance_window(instance_name)
    if hwnd is None:
        msg = f"{tag} Could not find LDPlayer window"
        log_fn(msg)
        return False, msg

    rect = _get_window_rect(hwnd)
    title = win32gui.GetWindowText(hwnd)
    log_fn(f"{tag} Found window: hwnd={hwnd} title='{title}' rect={rect}")

    # 2. Acquire per-window lock
    lock = _get_window_lock(hwnd)
    if not lock.acquire(timeout=30):
        msg = f"{tag} Timed out waiting for window lock"
        log_fn(msg)
        return False, msg

    try:
        # 3. Activate window
        if not activate_instance_window(hwnd, retries=3, log_fn=log_fn):
            msg = f"{tag} Failed to activate window"
            log_fn(msg)
            _save_failure_screenshot(hwnd, instance_name, log_fn)
            return False, msg

        # 4. Click macro toolbar
        if not click_macro_toolbar(hwnd, cal, retries=3, log_fn=log_fn):
            msg = f"{tag} Failed to click macro toolbar button"
            log_fn(msg)
            _save_failure_screenshot(hwnd, instance_name, log_fn)
            return False, msg

        # 5. Click macro item
        if not click_macro_item(hwnd, macro_name, cal, retries=3, log_fn=log_fn):
            msg = f"{tag} Failed to click macro item"
            log_fn(msg)
            _save_failure_screenshot(hwnd, instance_name, log_fn)
            return False, msg

        msg = f"{tag} Desktop click macro triggered successfully"
        log_fn(msg)
        _diag.info("run_macro_via_desktop_click SUCCESS instance=%s", instance_name)
        return True, msg

    finally:
        lock.release()


def _save_failure_screenshot(
    hwnd: int,
    instance_name: str,
    log_fn: Callable[[str], None],
) -> None:
    path = _capture_window_screenshot(hwnd, instance_name)
    if path:
        log_fn(f"[{instance_name}] Failure screenshot saved: {path}")
        _diag.info("screenshot saved: %s", path)
    else:
        _diag.warning("screenshot capture failed for %s", instance_name)


# ---------------------------------------------------------------------------
# High-level loop runner (mirrors LDPlayerMacroRunner.run_macro_loop)
# ---------------------------------------------------------------------------

class DesktopMacroRunner:
    """Desktop-click fallback runner with the same loop interface as
    ``LDPlayerMacroRunner`` so the UI layer can swap between them
    transparently.
    """

    def __init__(
        self,
        calibration: DesktopClickCalibration,
        log_fn: Callable[[str], None] = lambda m: None,
    ):
        self.cal = calibration
        self._log = log_fn

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
        """Execute desktop-click macro for *loops* iterations.

        Blocks until completion, stop, or failure.
        """
        _log = log_fn or self._log
        tag = instance_name
        result: Dict[str, Any] = {"success": True, "errors": [], "status": "success"}

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
                result["status"] = "failed"
                _log(f"[{tag}] Stopped")
                break

            _log(f"[{tag}] Desktop click loop {loop_num}/{loops}")

            ok, detail = run_macro_via_desktop_click(
                instance_name=instance_name,
                macro_name=filename,
                cal=self.cal,
                log_fn=_log,
            )

            if not ok:
                result["success"] = False
                result["errors"].append(detail)
                result["status"] = "failed"
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
            _log(f"[{tag}] Desktop click macro finished ({loops} loop(s))")
            result["status"] = "success"
        elif result["status"] != "failed":
            result["status"] = "failed"
        return result
