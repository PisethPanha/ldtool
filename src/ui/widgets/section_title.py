from __future__ import annotations

from PySide6.QtWidgets import QLabel


class SectionTitle(QLabel):
    """A small reusable section header label used across dashboard panels."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("sectionTitle")
