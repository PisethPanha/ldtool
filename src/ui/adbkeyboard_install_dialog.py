"""ADBKeyboard installation dialog with proper threading."""

from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt, QThread, Signal, QObject
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
    QMessageBox,
)
import subprocess


class ADBInstallWorker(QThread):
    """Worker thread for ADB install operation to avoid blocking UI."""

    progress = Signal(str)  # Log messages
    finished = Signal(bool, str)  # (success, error_message)

    def __init__(self, adb, serial: str, apk_path: str, log_fn: Callable[[str], None]):
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

            # Method 1: Try using adb manager if it has install method
            if hasattr(self.adb, 'install'):
                try:
                    result = self.adb.install(self.serial, self.apk_path)
                    if result and "Success" in str(result):
                        self._post_install_setup()
                        return
                except Exception as e:
                    self.log_fn(f"[ADBKeyboardInstall] ADB manager install failed: {e}")

            # Method 2: Direct subprocess call
            if hasattr(self.adb, '_adb_path'):
                adb_path = self.adb._adb_path
            elif hasattr(self.adb, 'adb_path'):
                adb_path = self.adb.adb_path
            else:
                adb_path = "adb"

            cmd = [adb_path, "-s", self.serial, "install", "-r", self.apk_path]
            self.log_fn(f"[ADBKeyboardInstall] Running: {' '.join(cmd)}")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
            )
            
            output = result.stdout + result.stderr
            self.log_fn(f"[ADBKeyboardInstall] Install output: {output[:300]}")

            if result.returncode != 0 or "Success" not in output:
                error_msg = f"Installation failed: {output[:500]}"
                self.progress.emit(f"❌ {error_msg}")
                self.finished.emit(False, error_msg)
                return

            self.progress.emit("✓ APK installed successfully")
            self._post_install_setup()

        except subprocess.TimeoutExpired:
            error_msg = "Installation timed out after 60 seconds"
            self.progress.emit(f"❌ {error_msg}")
            self.log_fn(f"[ADBKeyboardInstall] {error_msg}")
            self.finished.emit(False, error_msg)
        except Exception as e:
            error_msg = f"Installation error: {e}"
            self.progress.emit(f"❌ {error_msg}")
            self.log_fn(f"[ADBKeyboardInstall] Exception: {error_msg}")
            self.finished.emit(False, error_msg)

    def _post_install_setup(self) -> None:
        """Enable and set ADBKeyboard IME after installation."""
        try:
            self.progress.emit("Enabling ADBKeyboard IME...")
            
            # Enable IME
            self.adb.shell(self.serial, "ime enable com.android.adbkeyboard/.AdbIME")
            self.progress.emit("✓ ADBKeyboard IME enabled")
            self.log_fn("[ADBKeyboardInstall] IME enabled")
            
            # Set as current IME
            self.adb.shell(self.serial, "ime set com.android.adbkeyboard/.AdbIME")
            self.progress.emit("✓ ADBKeyboard set as active IME")
            self.log_fn("[ADBKeyboardInstall] IME set as active")
            
            self.progress.emit("\n✓ Installation completed successfully!")
            self.finished.emit(True, "")
            
        except Exception as e:
            error_msg = f"Failed to enable IME: {e}"
            self.progress.emit(f"⚠ {error_msg}")
            self.log_fn(f"[ADBKeyboardInstall] IME setup failed: {e}")
            # Still report success since APK is installed
            self.finished.emit(True, "")


class ADBKeyboardInstallDialog(QDialog):
    """Dialog for installing ADBKeyboard to an emulator.
    
    This dialog runs in the main UI thread and uses a worker thread
    for the actual ADB installation to avoid blocking the UI.
    """

    def __init__(
        self,
        parent: Optional[Any],
        adb: Any,
        serial: str,
        log_fn: Callable[[str], None],
    ):
        super().__init__(parent)
        self.adb = adb
        self.serial = serial
        self.log_fn = log_fn
        self.install_worker: Optional[ADBInstallWorker] = None
        self.install_success = False

        self.setWindowTitle("ADBKeyboard Installation Required")
        self.setModal(True)
        self.resize(650, 450)

        self._build_ui()
        self.log_fn(f"[{serial}] ADBKeyboard installation dialog created in UI thread")

    def _build_ui(self) -> None:
        """Build dialog UI."""
        layout = QVBoxLayout(self)

        # Title
        title = QLabel("ADBKeyboard Installation Required")
        title_font = title.font()
        title_font.setPointSize(12)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        # Explanation
        msg = QLabel(
            f"The ADBKeyboard (ADB IME) is required for reliable text input on emulator <b>{self.serial}</b>.\n\n"
            "Please select <b>ADBKeyboard.apk</b> from your local PC to install it."
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

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        button_row.addWidget(self.cancel_btn)

        layout.addLayout(button_row)

    def _browse_apk(self) -> None:
        """Open file picker for ADBKeyboard.apk."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select ADBKeyboard.apk",
            str(Path.cwd()),
            "APK files (*.apk);;All files (*)",
        )
        if file_path:
            self.apk_path_input.setText(file_path)
            self.install_btn.setEnabled(True)
            self.log_display.append(f"Selected: {file_path}")
            self.log_fn(f"[{self.serial}] User selected APK: {file_path}")

    def _install(self) -> None:
        """Start ADBKeyboard installation."""
        apk_path = self.apk_path_input.text().strip()
        if not apk_path:
            self.log_display.append("❌ No APK file selected")
            return

        apk_file = Path(apk_path)
        if not apk_file.exists() or not apk_file.is_file():
            self.log_display.append(f"❌ File does not exist: {apk_path}")
            QMessageBox.warning(self, "File Not Found", f"File does not exist:\n{apk_path}")
            return

        # Disable UI during installation
        self.install_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.log_display.append(f"\nStarting installation from: {apk_path}")
        self.log_fn(f"[{self.serial}] Starting ADB install of {apk_path}")

        # Start installation worker
        self.install_worker = ADBInstallWorker(
            self.adb,
            self.serial,
            apk_path,
            self.log_fn,
        )
        self.install_worker.progress.connect(self._on_progress)
        self.install_worker.finished.connect(self._on_install_finished)
        self.install_worker.start()

    def _on_progress(self, message: str) -> None:
        """Update progress display (called from worker thread via signal)."""
        self.log_display.append(message)
        self.log_display.verticalScrollBar().setValue(
            self.log_display.verticalScrollBar().maximum()
        )

    def _on_install_finished(self, success: bool, error: str) -> None:
        """Handle installation completion (called from worker thread via signal)."""
        self.progress_bar.setVisible(False)
        self.cancel_btn.setEnabled(True)

        if success:
            self.log_display.append("\n✅ Installation completed successfully!")
            self.log_fn(f"[{self.serial}] ADBKeyboard installation succeeded")
            self.install_success = True
            
            # Auto-close after successful install
            QMessageBox.information(
                self,
                "Installation Complete",
                "ADBKeyboard has been successfully installed!\n\nYou can now proceed with posting reels.",
            )
            self.accept()
        else:
            self.log_display.append(f"\n❌ Installation failed: {error}")
            self.log_fn(f"[{self.serial}] ADBKeyboard installation failed: {error}")
            self.install_btn.setEnabled(True)
            QMessageBox.critical(
                self,
                "Installation Failed",
                f"Failed to install ADBKeyboard:\n\n{error}\n\nPlease check the log and try again.",
            )

    def get_result(self) -> bool:
        """Return True if installation succeeded, False if cancelled."""
        return self.result() == QDialog.Accepted and self.install_success
