"""Centralized UI styles, theme management, and color constants for LDTool."""
from __future__ import annotations

from dataclasses import dataclass


# Status badge colors for queue processes (shared across themes)
STATUS_COLORS = {
	"Running": "#2196F3",    # Blue
	"Waiting": "#FF9800",    # Orange
	"Completed": "#4CAF50",  # Green
	"Failed": "#F44336",     # Red
}

# Instance status indicator colors
INSTANCE_STATUS_COLORS = {
	"Running": "#4CAF50",    # Green
	"Idle": "#FF9800",       # Orange
	"Offline": "#9E9E9E",    # Grey
}


@dataclass(frozen=True)
class ThemeColors:
	"""Token set that varies between light and dark mode."""
	# Window / base
	window_bg: str
	window_fg: str
	# Panels & cards
	panel_bg: str
	panel_border: str
	# Inputs
	input_bg: str
	input_border: str
	input_fg: str
	# Tables
	table_grid: str
	table_alt_bg: str
	# Group boxes
	group_title: str
	# Progress bar
	progress_bg: str
	# Scrollbar
	scrollbar_bg: str
	scrollbar_handle: str
	# Log panel
	log_default_text: str
	log_timestamp: str
	# Tab bar
	tab_bg: str
	tab_selected_bg: str
	tab_selected_fg: str
	tab_hover_bg: str
	# Dock title
	dock_title_bg: str


LIGHT = ThemeColors(
	window_bg="#f5f5f5",
	window_fg="#333333",
	panel_bg="#ffffff",
	panel_border="#c0c0c0",
	input_bg="#ffffff",
	input_border="#c0c0c0",
	input_fg="#333333",
	table_grid="#e0e0e0",
	table_alt_bg="#fafafa",
	group_title="#333333",
	progress_bg="#f0f0f0",
	scrollbar_bg="#f0f0f0",
	scrollbar_handle="#c0c0c0",
	log_default_text="#333333",
	log_timestamp="#888888",
	tab_bg="#e8e8e8",
	tab_selected_bg="#ffffff",
	tab_selected_fg="#333333",
	tab_hover_bg="#d8d8d8",
	dock_title_bg="#e0e0e0",
)

DARK = ThemeColors(
	window_bg="#1e1e1e",
	window_fg="#d4d4d4",
	panel_bg="#252526",
	panel_border="#3c3c3c",
	input_bg="#2d2d2d",
	input_border="#3c3c3c",
	input_fg="#cccccc",
	table_grid="#3c3c3c",
	table_alt_bg="#2a2a2a",
	group_title="#cccccc",
	progress_bg="#2d2d2d",
	scrollbar_bg="#1e1e1e",
	scrollbar_handle="#555555",
	log_default_text="#d4d4d4",
	log_timestamp="#888888",
	tab_bg="#2d2d2d",
	tab_selected_bg="#1e1e1e",
	tab_selected_fg="#ffffff",
	tab_hover_bg="#383838",
	dock_title_bg="#333333",
)

_THEMES: dict[str, ThemeColors] = {"light": LIGHT, "dark": DARK}

# ---------------------------------------------------------------------------
# Active theme state
# ---------------------------------------------------------------------------
_current_theme_name: str = "light"


def current_theme() -> ThemeColors:
	"""Return the currently active theme colour set."""
	return _THEMES[_current_theme_name]


def current_theme_name() -> str:
	return _current_theme_name


def set_theme(name: str) -> None:
	"""Set theme by name ('light' or 'dark')."""
	global _current_theme_name
	if name not in _THEMES:
		return
	_current_theme_name = name


def build_stylesheet(t: ThemeColors | None = None) -> str:
	"""Generate the full application QSS for the given (or current) theme."""
	if t is None:
		t = current_theme()
	return f"""
/* ── Base ─────────────────────────────────────────── */
QMainWindow, QWidget {{
	background-color: {t.window_bg};
	color: {t.window_fg};
}}

/* ── Inputs ───────────────────────────────────────── */
QLineEdit, QSpinBox, QDateTimeEdit, QComboBox {{
	background-color: {t.input_bg};
	color: {t.input_fg};
	border: 1px solid {t.input_border};
	border-radius: 3px;
	padding: 3px 6px;
}}
QLineEdit:focus, QSpinBox:focus, QDateTimeEdit:focus, QComboBox:focus {{
	border-color: #2196F3;
}}
QComboBox::drop-down {{
	border: none;
	padding-right: 6px;
}}
QComboBox QAbstractItemView {{
	background-color: {t.panel_bg};
	color: {t.window_fg};
	selection-background-color: #2196F3;
	selection-color: #ffffff;
}}

/* ── Buttons ──────────────────────────────────────── */
QPushButton {{
	background-color: {t.panel_bg};
	color: {t.window_fg};
	border: 1px solid {t.panel_border};
	border-radius: 4px;
	padding: 4px 14px;
}}
QPushButton:hover {{
	background-color: {t.tab_hover_bg};
}}
QPushButton:pressed {{
	background-color: {t.panel_border};
}}
QPushButton:disabled {{
	color: {t.scrollbar_handle};
}}

/* ── Group boxes ──────────────────────────────────── */
QGroupBox {{
	font-weight: bold;
	border: 1px solid {t.panel_border};
	border-radius: 6px;
	margin-top: 14px;
	padding: 12px 8px 8px 8px;
	background-color: {t.panel_bg};
}}
QGroupBox::title {{
	subcontrol-origin: margin;
	left: 12px;
	padding: 0 6px;
	color: {t.group_title};
}}

/* ── Tables ───────────────────────────────────────── */
QTableWidget {{
	gridline-color: {t.table_grid};
	alternate-background-color: {t.table_alt_bg};
	background-color: {t.panel_bg};
	color: {t.window_fg};
}}
QTableWidget::item {{
	padding: 3px 6px;
}}
QHeaderView::section {{
	background-color: {t.tab_bg};
	color: {t.window_fg};
	border: 1px solid {t.table_grid};
	padding: 4px 6px;
}}

/* ── Progress bar ─────────────────────────────────── */
QProgressBar {{
	border: 1px solid {t.panel_border};
	border-radius: 4px;
	text-align: center;
	background-color: {t.progress_bg};
	color: {t.window_fg};
	min-height: 18px;
	max-height: 18px;
}}
QProgressBar::chunk {{
	background-color: #2196F3;
	border-radius: 3px;
}}

/* ── Tab bar ──────────────────────────────────────── */
QTabWidget::pane {{
	border: 1px solid {t.panel_border};
	background-color: {t.window_bg};
}}
QTabBar::tab {{
	background-color: {t.tab_bg};
	color: {t.window_fg};
	padding: 6px 16px;
	border: 1px solid {t.panel_border};
	border-bottom: none;
	border-top-left-radius: 4px;
	border-top-right-radius: 4px;
	margin-right: 2px;
}}
QTabBar::tab:selected {{
	background-color: {t.tab_selected_bg};
	color: {t.tab_selected_fg};
}}
QTabBar::tab:hover:!selected {{
	background-color: {t.tab_hover_bg};
}}

/* ── Dock widget ──────────────────────────────────── */
QDockWidget {{
	color: {t.window_fg};
}}
QDockWidget::title {{
	background-color: {t.dock_title_bg};
	padding: 4px;
}}

/* ── Lists ────────────────────────────────────────── */
QListWidget {{
	background-color: {t.panel_bg};
	color: {t.window_fg};
	border: 1px solid {t.panel_border};
}}
QListWidget::item:selected {{
	background-color: #2196F3;
	color: #ffffff;
}}

/* ── Text areas ───────────────────────────────────── */
QPlainTextEdit, QTextEdit {{
	background-color: {t.panel_bg};
	color: {t.window_fg};
	border: 1px solid {t.panel_border};
}}

/* ── Scrollbars ───────────────────────────────────── */
QScrollBar:vertical {{
	background: {t.scrollbar_bg};
	width: 10px;
	margin: 0;
}}
QScrollBar::handle:vertical {{
	background: {t.scrollbar_handle};
	min-height: 30px;
	border-radius: 5px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
	height: 0;
}}
QScrollBar:horizontal {{
	background: {t.scrollbar_bg};
	height: 10px;
	margin: 0;
}}
QScrollBar::handle:horizontal {{
	background: {t.scrollbar_handle};
	min-width: 30px;
	border-radius: 5px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
	width: 0;
}}

/* ── Checkbox / Radio ─────────────────────────────── */
QCheckBox, QRadioButton {{
	color: {t.window_fg};
}}

/* ── Labels ───────────────────────────────────────── */
QLabel {{
	color: {t.window_fg};
}}

/* ── Context menu ─────────────────────────────────── */
QMenu {{
	background-color: {t.panel_bg};
	color: {t.window_fg};
	border: 1px solid {t.panel_border};
}}
QMenu::item:selected {{
	background-color: #2196F3;
	color: #ffffff;
}}

/* ── Tooltip ──────────────────────────────────────── */
QToolTip {{
	background-color: {t.panel_bg};
	color: {t.window_fg};
	border: 1px solid {t.panel_border};
	padding: 4px;
}}
"""


# Keep backward-compatible name (used before theme system existed)
APP_STYLESHEET = build_stylesheet(LIGHT)

