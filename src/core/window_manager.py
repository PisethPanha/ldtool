"""Windows window management utilities using pywin32.

Provides utilities for listing, finding, and manipulating windows across
one or more monitors on Windows systems.
"""

from typing import Dict, List, Tuple, Callable, Any
import ctypes
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
