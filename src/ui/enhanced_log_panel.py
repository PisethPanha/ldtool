"""Enhanced log panel with colored messages and toolbar controls."""
from __future__ import annotations

from PySide6.QtCore import QDateTime
from PySide6.QtGui import QTextCursor, QIcon
from PySide6.QtWidgets import (
	QWidget,
	QVBoxLayout,
	QHBoxLayout,
	QPushButton,
	QCheckBox,
	QLabel,
	QTextEdit,
	QApplication,
)

from src.ui.widgets import icon_path


class EnhancedLogPanel(QWidget):
	"""Rich log panel with color-coded messages, clear / copy / auto-scroll."""

	def __init__(self, parent=None):
		super().__init__(parent)
		self._auto_scroll = True
		self._build_ui()

	def _build_ui(self) -> None:
		layout = QVBoxLayout(self)
		layout.setContentsMargins(0, 0, 0, 0)
		layout.setSpacing(2)

		# Toolbar
		toolbar = QHBoxLayout()
		toolbar.setContentsMargins(4, 2, 4, 0)

		clear_btn = QPushButton("Clear Log")
		clear_btn.setObjectName("secondaryButton")
		clear_btn.setIcon(QIcon(icon_path("trash.svg")))
		clear_btn.clicked.connect(self.clear)

		copy_btn = QPushButton("Copy Log")
		copy_btn.setObjectName("secondaryButton")
		copy_btn.clicked.connect(self._copy_log)

		self._auto_scroll_cb = QCheckBox("Auto-scroll")
		self._auto_scroll_cb.setChecked(True)
		self._auto_scroll_cb.toggled.connect(self._on_auto_scroll_toggled)

		self._auto_scroll_label = QLabel("Auto-scroll: ON")
		self._auto_scroll_label.setObjectName("subtleLabel")

		toolbar.addWidget(clear_btn)
		toolbar.addWidget(copy_btn)
		toolbar.addWidget(self._auto_scroll_cb)
		toolbar.addWidget(self._auto_scroll_label)
		toolbar.addStretch()
		layout.addLayout(toolbar)

		# Log text area (rich text)
		self._text_edit = QTextEdit()
		self._text_edit.setReadOnly(True)
		self._text_edit.document().setDefaultStyleSheet(
			"body { font-family: 'Cascadia Mono', Consolas, 'Courier New', monospace; font-size: 12px; }"
		)
		layout.addWidget(self._text_edit)

	# -- public API ----------------------------------------------------------

	def append_message(self, message: str) -> None:
		"""Append *message* with a timestamp and automatic color detection."""
		ts = QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
		color = _detect_color(message, "#b3b8cf")
		safe = _escape_html(message)
		html = (
			f'<span style="color:#8f97bd;">[{ts}]</span> '
			f'<span style="color:{color};">{safe}</span>'
		)
		self._text_edit.append(html)
		if self._auto_scroll:
			self._text_edit.moveCursor(QTextCursor.MoveOperation.End)

	def clear(self) -> None:
		self._text_edit.clear()

	def toPlainText(self) -> str:  # noqa: N802 – matches Qt naming
		return self._text_edit.toPlainText()

	# -- private -------------------------------------------------------------

	def _copy_log(self) -> None:
		QApplication.clipboard().setText(self._text_edit.toPlainText())

	def _on_auto_scroll_toggled(self, checked: bool) -> None:
		self._auto_scroll = checked
		self._auto_scroll_label.setText("Auto-scroll: ON" if checked else "Auto-scroll: OFF")


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _detect_color(msg: str, default: str = "#333333") -> str:
	"""Return an HTML hex colour string based on message content."""
	upper = msg.upper()
	if "\u2713" in msg or "SUCCESS" in upper:
		return "#6bd16b"
	if "\u2717" in msg or "FAILED" in upper or "ERROR" in upper:
		return "#ff6b6b"
	if "\u26A0" in msg or "WARNING" in upper:
		return "#f2b45e"
	if "INFO" in upper:
		return "#b3b8cf"
	if "[Queue]" in msg:
		return "#78a8ff"
	return default


def _escape_html(text: str) -> str:
	return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
