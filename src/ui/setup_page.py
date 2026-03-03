from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import (
    QWidget,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QFileDialog,
)

from src.core.config import load_config, save_config
from src.core.ldplayer_scanner import (
    find_dnconsole,
    find_adb,
    validate_paths,
)


class SetupPage(QWidget):
    """Configuration page displayed in the "Setup" tab.

    The caller must provide a ``log_fn`` callback that accepts a single
    string.  Messages about detection and validation are forwarded to this
    function so they appear in the application's central log panel.
    """

    def __init__(self, log_fn: Callable[[str], None], parent=None):
        super().__init__(parent)
        self._log = log_fn

        # input widgets
        self.ld_dir_edit = QLineEdit()
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_ld_dir)

        self.dnconsole_edit = QLineEdit()
        self.dnconsole_edit.setReadOnly(True)
        self.adb_edit = QLineEdit()
        self.adb_edit.setReadOnly(True)

        test_btn = QPushButton("Test")
        test_btn.clicked.connect(self._test_and_save)

        # layout
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("LDPlayer folder:"))
        row.addWidget(self.ld_dir_edit)
        row.addWidget(browse_btn)
        layout.addLayout(row)

        layout.addWidget(QLabel("dnconsole executable:"))
        layout.addWidget(self.dnconsole_edit)
        layout.addWidget(QLabel("adb executable:"))
        layout.addWidget(self.adb_edit)
        layout.addWidget(test_btn)

        # populate from existing configuration
        cfg = load_config()
        self.ld_dir_edit.setText(cfg.get("ldplayer_dir", ""))
        self._update_paths()

    def _browse_ld_dir(self) -> None:
        """Show a directory chooser and update path fields on selection."""

        path = QFileDialog.getExistingDirectory(self, "Select LDPlayer Directory")
        if path:
            self.ld_dir_edit.setText(path)
            self._update_paths()

    def _update_paths(self) -> None:
        """Detect dnconsole and adb based on the current LD directory."""

        ld_dir = self.ld_dir_edit.text()
        dn = find_dnconsole(ld_dir) or ""
        ad = find_adb(ld_dir) or ""

        self.dnconsole_edit.setText(dn)
        self.adb_edit.setText(ad)

        self._log(f"Detected dnconsole: {dn or '<none>'}")
        self._log(f"Detected adb: {ad or '<none>'}")

    def _test_and_save(self) -> None:
        """Validate the current paths and save configuration if valid."""

        dn = self.dnconsole_edit.text()
        ad = self.adb_edit.text()
        ok, reason = validate_paths(dn, ad)
        if ok:
            self._log("Path validation succeeded, saving configuration.")
            save_config(
                {
                    "ldplayer_dir": self.ld_dir_edit.text(),
                    "dnconsole_path": dn,
                    "adb_path": ad,
                }
            )
            self._log("Configuration written to disk.")
        else:
            self._log(f"Path validation failed: {reason}")
