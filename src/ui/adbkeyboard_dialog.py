"""PySide6 dialog for ADBKeyboard installation."""

from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QFileDialog,
    QTextEdit,
    QProgressBar,
)


class ADBKeyboardInstallWorker(QThread):
    """Worker thread for non-blocking ADBKeyboard installation."""

    progress = Signal(str)  # Log messages
    finished = Signal(bool, str)  # (success, error_message)

    def __init__(
        self,
        adb: Any,
        serial: str,
        apk_path: str,
        log_fn: Callable[[str], None],
    ):
        super().__init__()
        self.adb = adb
        self.serial = serial
        self.apk_path = apk_path
        self.log_fn = log_fn

    def run(self) -> None:
        """Execute installation in background thread."""
        try:
            self.progress.emit(f"Installing ADBKeyboard.apk to {self.serial}...")
            self.log_fn(f"[ADBKeyboardInstall] Installing from: {self.apk_path}")

            # Install APK with replace flag
            result = self.adb.shell(
                self.serial,
                f'install -r "{self.apk_path}"'
            )
            self.log_fn(f"[ADBKeyboardInstall] install output: {result}")

            if result is None or "Success" not in str(result):
                error_msg = str(result) if result else "Unknown error"
                self.progress.emit(f"❌ Installation failed: {error_msg}")
                self.finished.emit(False, f"Installation failed: {error_msg}")
                return

            self.progress.emit("✓ APK installed successfully, enabling IME...")
            self.log_fn("[ADBKeyboardInstall] APK installed, enabling IME")

            # Enable IME
            try:
                self.adb.shell(self.serial, "ime enable com.android.adbkeyboard/.AdbIME")
                self.progress.emit("✓ ADBKeyboard IME enabled")
                self.log_fn("[ADBKeyboardInstall] IME enabled")
            except Exception as e:
                self.log_fn(f"[ADBKeyboardInstall] Failed to enable IME: {e}")
                # Continue anyway, IME may still work

            # Set as current IME
            try:
                self.adb.shell(self.serial, "ime set com.android.adbkeyboard/.AdbIME")
                self.progress.emit("✓ ADBKeyboard set as active IME")
                self.log_fn("[ADBKeyboardInstall] IME set as active")
            except Exception as e:
                self.log_fn(f"[ADBKeyboardInstall] Failed to set IME as active: {e}")
                # Continue anyway

            self.finished.emit(True, "")

        except Exception as e:
            error_msg = str(e)
            self.progress.emit(f"❌ Installation error: {error_msg}")
            self.log_fn(f"[ADBKeyboardInstall] Exception: {error_msg}")
            self.finished.emit(False, error_msg)


class ADBKeyboardInstallDialog(QDialog):
    """Dialog for installing ADBKeyboard to an emulator."""

    def __init__(
        self,
        parent: Optional[Any] = None,
        adb: Optional[Any] = None,
        serial: str = "",
        log_fn: Optional[Callable[[str], None]] = None,
    ):
        super().__init__(parent)
        self.adb = adb
        self.serial = serial
        self.log_fn = log_fn or (lambda x: None)
        self.install_worker: Optional[ADBKeyboardInstallWorker] = None

        self.setWindowTitle("ADBKeyboard Installation Required")
        self.setModal(True)
        self.resize(600, 400)

        self._build_ui()

    def _build_ui(self) -> None:
        """Build dialog UI."""
        layout = QVBoxLayout(self)

        # Title / message
        title = QLabel("ADBKeyboard Installation Required")
        title_font = title.font()
        title_font.setPointSize(12)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        # Explanation
        msg = QLabel(
            f"The ADBKeyboard (ADB IME) is required for reliable caption input on emulator {self.serial}.\n\n"
            "Please select ADBKeyboard.apk from your local PC and install it."
        )
        msg.setWordWrap(True)
        layout.addWidget(msg)

        # APK file picker
        picker_row = QHBoxLayout()
        picker_row.addWidget(QLabel("APK file:"))
        self.apk_path_input = QLineEdit()
        self.apk_path_input.setPlaceholderText("Select ADBKeyboard.apk...")
        self.apk_path_input.setReadOnly(True)
        picker_row.addWidget(self.apk_path_input)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_apk)
        picker_row.addWidget(browse_btn)
        layout.addLayout(picker_row)

        # Progress / log
        layout.addWidget(QLabel("Installation log:"))
        self.log_display = QTextEdit()
        self.log_display.setReadOnly(True)
        self.log_display.setMaximumHeight(200)
        layout.addWidget(self.log_display)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximum(0)  # Indeterminate
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # Buttons
        button_row = QHBoxLayout()
        button_row.addStretch()

        self.install_btn = QPushButton("Install")
        self.install_btn.clicked.connect(self._install)
        self.install_btn.setEnabled(False)
        button_row.addWidget(self.install_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_row.addWidget(cancel_btn)

        layout.addLayout(button_row)

    def _browse_apk(self) -> None:
        """Open file picker for ADBKeyboard.apk."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select ADBKeyboard.apk",
            str(Path.home()),
            "APK files (*.apk);;All files (*)",
        )
        if file_path:
            self.apk_path_input.setText(file_path)
            self.install_btn.setEnabled(True)
            self.log_display.append(f"Selected: {file_path}")

    def _install(self) -> None:
        """Start ADBKeyboard installation."""
        apk_path = self.apk_path_input.text().strip()
        if not apk_path:
            self.log_display.append("❌ No APK file selected")
            return

        apk_file = Path(apk_path)
        if not apk_file.exists() or not apk_file.is_file():
            self.log_display.append(f"❌ File does not exist: {apk_path}")
            return

        # Disable UI during installation
        self.install_btn.setEnabled(False)
        self.apk_path_input.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.log_display.append(f"Starting installation from: {apk_path}")

        # Start installation worker
        self.install_worker = ADBKeyboardInstallWorker(
            self.adb,
            self.serial,
            apk_path,
            self.log_fn,
        )
        self.install_worker.progress.connect(self._on_progress)
        self.install_worker.finished.connect(self._on_install_finished)
        self.install_worker.start()

    def _on_progress(self, message: str) -> None:
        """Update progress display."""
        self.log_display.append(message)

    def _on_install_finished(self, success: bool, error: str) -> None:
        """Handle installation completion."""
        self.progress_bar.setVisible(False)

        if success:
            self.log_display.append("\n✓ Installation completed successfully!")
            self.log_display.append("You can now proceed with posting reels.")
            self.accept()  # Close dialog with success
        else:
            self.log_display.append(f"\n❌ Installation failed: {error}")
            self.install_btn.setEnabled(True)
            self.apk_path_input.setEnabled(True)

    def get_result(self) -> bool:
        """Return True if installation succeeded, False if cancelled."""
        return self.result() == QDialog.Accepted
