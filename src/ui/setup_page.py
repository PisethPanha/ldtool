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
    QGroupBox,
    QFormLayout,
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

        # Create main layout with compact margins
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        # Compact configuration card
        config_group = QGroupBox("LDPlayer Configuration")
        config_layout = QFormLayout(config_group)
        config_layout.setContentsMargins(10, 10, 10, 10)
        config_layout.setSpacing(6)
        config_layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        # LDPlayer folder input with browse button
        ld_folder_row = QHBoxLayout()
        ld_folder_row.setSpacing(6)
        self.ld_dir_edit = QLineEdit()
        self.ld_dir_edit.setFixedHeight(30)
        self.ld_dir_edit.setPlaceholderText("Select LDPlayer installation folder")
        browse_btn = QPushButton("Browse...")
        browse_btn.setFixedHeight(30)
        browse_btn.setFixedWidth(90)
        browse_btn.clicked.connect(self._browse_ld_dir)
        ld_folder_row.addWidget(self.ld_dir_edit)
        ld_folder_row.addWidget(browse_btn)
        ld_folder_label = QLabel("LDPlayer Folder:")
        ld_folder_label.setStyleSheet("padding: 10px; border-radius: 10px; background-color: #2a2c3e;")
        config_layout.addRow(ld_folder_label, ld_folder_row)

        # dnconsole executable (read-only display)
        self.dnconsole_edit = QLineEdit()
        self.dnconsole_edit.setReadOnly(True)
        self.dnconsole_edit.setFixedHeight(30)
        self.dnconsole_edit.setPlaceholderText("Auto-detected path will appear here")
        dnconsole_label = QLabel("dnconsole:")
        dnconsole_label.setStyleSheet("padding: 10px; border-radius: 10px; background-color: #2a2c3e;")
        config_layout.addRow(dnconsole_label, self.dnconsole_edit)

        # adb executable (read-only display)
        self.adb_edit = QLineEdit()
        self.adb_edit.setReadOnly(True)
        self.adb_edit.setFixedHeight(30)
        self.adb_edit.setPlaceholderText("Auto-detected path will appear here")
        adb_label = QLabel("adb:")
        adb_label.setStyleSheet("padding: 10px; border-radius: 10px; background-color: #2a2c3e;")
        config_layout.addRow(adb_label, self.adb_edit)

        # Test button
        test_btn = QPushButton("Test Configuration")
        test_btn.setFixedHeight(32)
        test_btn.setFixedWidth(150)
        test_btn.clicked.connect(self._test_and_save)
        test_btn_layout = QHBoxLayout()
        test_btn_layout.addWidget(test_btn)
        test_btn_layout.addStretch()
        config_layout.addRow("", test_btn_layout)

        main_layout.addWidget(config_group)
        main_layout.addStretch()

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
