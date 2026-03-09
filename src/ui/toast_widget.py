"""Lightweight toast notification overlay."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel

from src.ui.styles import current_theme_name


class ToastWidget(QLabel):
	"""A brief floating notification that hides itself after a timeout."""

	def __init__(self, parent=None):
		super().__init__(parent)
		self.setAlignment(Qt.AlignCenter)
		self.setWordWrap(True)
		self.hide()
		self._timer = QTimer(self)
		self._timer.setSingleShot(True)
		self._timer.timeout.connect(self.hide)

	def show_message(self, text: str, duration_ms: int = 3000) -> None:
		"""Display *text* for *duration_ms* milliseconds."""
		if current_theme_name() == "dark":
			self.setStyleSheet(
				"background-color: rgba(220, 220, 220, 230);"
				"color: #1e1e1e;"
				"padding: 10px 24px;"
				"border-radius: 8px;"
				"font-size: 13px;"
			)
		else:
			self.setStyleSheet(
				"background-color: rgba(50, 50, 50, 220);"
				"color: white;"
				"padding: 10px 24px;"
				"border-radius: 8px;"
				"font-size: 13px;"
			)
		self.setText(text)
		self.adjustSize()
		if self.parent():
			pw = self.parent().width()
			self.move((pw - self.width()) // 2, 12)
		self.show()
		self.raise_()
		self._timer.start(duration_ms)

	def show_error(self, text: str, duration_ms: int = 3500) -> None:
		"""Compatibility wrapper for error toasts."""
		self.show_message(text, duration_ms)

	def show_success(self, text: str, duration_ms: int = 2500) -> None:
		"""Compatibility wrapper for success toasts."""
		self.show_message(text, duration_ms)
