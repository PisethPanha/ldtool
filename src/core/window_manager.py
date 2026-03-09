"""Windows window management utilities using pywin32.

Provides utilities for listing, finding, and manipulating windows across
one or more monitors on Windows systems.
"""

from typing import Dict, List, Optional, Tuple, Callable, Any
import ctypes
import math
from ctypes.wintypes import RECT

try:
    import win32gui
    import win32con
    import win32process
except ImportError:
    raise ImportError("pywin32 is required; install with: pip install pywin32")


class WindowManager:
    """Manager for window operations on Windows."""

    @staticmethod
    def list_top_level_windows() -> List[Dict[str, Any]]:
        """List all visible top-level windows.

        Returns a list of dicts with keys:
        - hwnd: window handle (int)
        - title: window title (str)
        - pid: process ID (int)

        Invisible windows and windows with empty titles are filtered out.
        """
        windows: List[Dict[str, Any]] = []

        def enum_callback(hwnd: int, lParam: int) -> bool:
            # skip invisible windows
            if not win32gui.IsWindowVisible(hwnd):
                return True

            try:
                title = win32gui.GetWindowText(hwnd).strip()
                # skip windows with no title
                if not title:
                    return True

                # get process ID
                _, pid = win32process.GetWindowThreadProcessId(hwnd)

                windows.append({
                    "hwnd": hwnd,
                    "title": title,
                    "pid": pid,
                })
            except Exception:  # pragma: no cover - defensive
                pass

            return True

        try:
            win32gui.EnumWindows(enum_callback, 0)
        except Exception:  # pragma: no cover - defensive
            pass

        return windows

    @staticmethod
    def find_windows_by_title_keywords(keywords: List[str]) -> Dict[str, int]:
        """Find best-matching windows for each keyword.

        For each keyword, searches all visible windows for a title containing
        that keyword (case-insensitive).  Returns a dict mapping keyword to hwnd.

        If no match is found for a keyword, it is omitted from the result.
        """
        result: Dict[str, int] = {}
        windows = WindowManager.list_top_level_windows()

        for kw in keywords:
            kw_lower = kw.lower()
            for win in windows:
                title_lower = win["title"].lower()
                if kw_lower in title_lower:
                    result[kw] = win["hwnd"]
                    break  # use first match for this keyword
        return result

    @staticmethod
    def restore_window(hwnd: int) -> bool:
        """Restore a minimized or maximized window to normal state.

        Returns ``True`` on success, ``False`` on error.
        """
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            return True
        except Exception:  # pragma: no cover - defensive
            return False

    @staticmethod
    def minimize_window(hwnd: int) -> bool:
        """Minimize a window.

        Returns ``True`` on success, ``False`` on error.
        """
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
            return True
        except Exception:  # pragma: no cover - defensive
            return False

    @staticmethod
    def bring_to_front(hwnd: int) -> bool:
        """Bring a window to the foreground and activate it.

        Returns ``True`` on success, ``False`` on error.
        """
        try:
            win32gui.SetForegroundWindow(hwnd)
            return True
        except Exception:  # pragma: no cover - defensive
            return False

    @staticmethod
    def move_resize(hwnd: int, x: int, y: int, w: int, h: int) -> bool:
        """Move and resize a window.

        Args:
            hwnd: window handle
            x: left coordinate
            y: top coordinate
            w: width
            h: height

        Returns ``True`` on success, ``False`` on error.
        """
        try:
            win32gui.MoveWindow(hwnd, x, y, w, h, True)
            return True
        except Exception:  # pragma: no cover - defensive
            return False

    @staticmethod
    def get_monitor_work_areas() -> List[Tuple[int, int, int, int]]:
        """Get work areas (usable screen space) for all monitors.

        Returns a list of tuples (left, top, right, bottom) for each monitor,
        excluding taskbars and other reserved areas.

        Falls back to using SystemParametersInfo if EnumDisplayMonitors is unavailable.
        """
        work_areas: List[Tuple[int, int, int, int]] = []

        try:
            # try using win32api EnumDisplayMonitors
            import win32api
            monitors = win32api.EnumDisplayMonitors()
            for monitor in monitors:
                # monitor is (handle, rect_tuple, rc_monitor)
                # rc_monitor is sometimes available with work area info
                if monitor and len(monitor) >= 3:
                    # extract rect from monitor info
                    rect = monitor[2] if monitor[2] else monitor[1]
                    if rect and len(rect) >= 4:
                        work_areas.append((rect[0], rect[1], rect[2], rect[3]))
        except Exception:
            pass

        # if no monitors found, use SystemParametersInfo fallback
        if not work_areas:
            try:
                user32 = ctypes.windll.user32
                rect = RECT()
                # SPI_GETWORKAREA = 48
                if user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0):
                    work_areas.append((rect.left, rect.top, rect.right, rect.bottom))
            except Exception:  # pragma: no cover - defensive
                pass

        # if still empty, return a default representing the primary monitor
        if not work_areas:
            try:
                user32 = ctypes.windll.user32
                w = user32.GetSystemMetrics(0)  # SM_CXSCREEN
                h = user32.GetSystemMetrics(1)  # SM_CYSCREEN
                work_areas.append((0, 0, w, h))
            except Exception:  # pragma: no cover - defensive
                pass

        return work_areas

    @staticmethod
    def arrange_windows_fixed_grid_720x1280(
        hwnds: List[int],
        work_area: Tuple[int, int, int, int],
        rows: int,
        cols: int,
    ) -> None:
        """Arrange windows in a fixed grid with 720x1280 phone aspect ratio.

        Windows are positioned left-to-right, top-to-bottom in a grid.
        Each window maintains a 720:1280 (width:height) aspect ratio.

        Args:
            hwnds: list of window handles to arrange (limited to rows*cols)
            work_area: (left, top, right, bottom) tuple defining the layout area
            rows: number of rows in the grid
            cols: number of columns in the grid
        """
        left, top, right, bottom = work_area
        work_w = right - left
        work_h = bottom - top

        # cell dimensions
        cell_w = work_w // cols
        cell_h = work_h // rows

        # phone aspect ratio: width/height = 720/1280 = 0.5625
        aspect_ratio = 720.0 / 1280.0

        # compute window size that fits in cell while preserving aspect ratio
        if cell_w / cell_h <= aspect_ratio:
            # cell is narrower relative to aspect ratio
            win_w = cell_w
            win_h = int(win_w / aspect_ratio)
        else:
            # cell is wider relative to aspect ratio
            win_h = cell_h
            win_w = int(win_h * aspect_ratio)

        # arrange windows
        max_windows = rows * cols
        for idx, hwnd in enumerate(hwnds[:max_windows]):
            try:
                # restore window first
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

                # compute grid position
                row = idx // cols
                col = idx % cols
                x = left + col * cell_w
                y = top + row * cell_h

                # use SetWindowPos for precise positioning
                # SWP_NOZORDER = 0x0004, SWP_SHOWWINDOW = 0x0040
                win32gui.SetWindowPos(
                    hwnd,
                    win32con.HWND_TOP,
                    x,
                    y,
                    win_w,
                    win_h,
                    0x0004 | 0x0040,  # SWP_NOZORDER | SWP_SHOWWINDOW
                )
            except Exception:  # pragma: no cover - defensive
                pass

    # ------------------------------------------------------------------
    # Auto-arrange all LDPlayer windows
    # ------------------------------------------------------------------
    @staticmethod
    def find_ldplayer_windows() -> List[Dict[str, Any]]:
        """Return all visible windows whose title contains ``LDPlayer``.

        Each entry has keys ``hwnd``, ``title``, ``pid``.
        Results are sorted by title for deterministic ordering.
        """
        all_wins = WindowManager.list_top_level_windows()
        ld_wins = [w for w in all_wins if "ldplayer" in w["title"].lower()]
        ld_wins.sort(key=lambda w: w["title"])
        return ld_wins


def arrange_ldplayer_windows(
    log_fn: Optional[Callable[[str], None]] = None,
) -> int:
    """Detect all LDPlayer instance windows and tile them on screen.

    Grid is computed automatically::

        cols = ceil(sqrt(n))
        rows = ceil(n / cols)

    Each window is resized to ``(screen_w / cols, screen_h / rows)`` and
    placed at the corresponding grid cell.  Minimised windows are restored
    first.

    Args:
        log_fn: optional callback for log messages (e.g. UI log panel).

    Returns:
        The number of windows that were arranged.
    """
    _log = log_fn or (lambda m: None)

    # 1. Discover LDPlayer windows
    ld_wins = WindowManager.find_ldplayer_windows()
    count = len(ld_wins)
    if count == 0:
        _log("No LDPlayer windows found.")
        return 0
    _log(f"Found {count} LDPlayer window(s)")

    # 2. Compute grid
    cols = math.ceil(math.sqrt(count))
    rows = math.ceil(count / cols)
    _log(f"Grid layout: {rows} row(s) × {cols} col(s)")

    # 3. Get screen work area (primary monitor)
    work_areas = WindowManager.get_monitor_work_areas()
    if not work_areas:
        _log("Could not determine screen work area.")
        return 0
    left, top, right, bottom = work_areas[0]
    screen_w = right - left
    screen_h = bottom - top

    win_w = int(screen_w / cols)
    win_h = int(screen_h / rows)
    _log(f"Screen work area: {screen_w}×{screen_h}  Window size: {win_w}×{win_h}")

    # 4. Arrange each window
    arranged = 0
    for idx, win_info in enumerate(ld_wins):
        hwnd = win_info["hwnd"]
        title = win_info["title"]

        r = idx // cols
        c = idx % cols
        x = left + c * win_w
        y = top + r * win_h

        try:
            # Restore if minimised
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

            win32gui.SetWindowPos(
                hwnd,
                win32con.HWND_TOP,
                x, y, win_w, win_h,
                win32con.SWP_NOZORDER | win32con.SWP_SHOWWINDOW,
            )
            arranged += 1
            _log(f"  [{idx}] '{title}' hwnd={hwnd} → ({x},{y}) {win_w}×{win_h}")
        except Exception as exc:
            _log(f"  [{idx}] '{title}' hwnd={hwnd} → FAILED: {exc}")

    _log(f"Arranged {arranged}/{count} window(s)")
    return arranged
