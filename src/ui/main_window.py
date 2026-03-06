from PySide6.QtCore import Qt, QDateTime, QObject, Signal
from PySide6.QtWidgets import (
    QMainWindow,
    QTabWidget,
    QPlainTextEdit,
    QDockWidget,
)

from src.core.config import load_config
from src.core.models import AppState
from src.core.adb_manager import ADBManager
from .setup_page import SetupPage
from .instances_page import InstancesPage
from .app_launcher_page import AppLauncherPage
from .macro_runner_page import MacroRunnerPage
from .reels_poster_page import ReelsPosterPage


class LogBus(QObject):
    message = Signal(str)


class AdbKeyboardInstallBus(QObject):
    """Signal bus for ADBKeyboard installation requests.
    
    Worker threads emit install_requested signal.
    UI thread handles the signal by showing the dialog.
    """
    install_requested = Signal(object)  # Signal(ADBKeyboardRequest)


class MainWindow(QMainWindow):
    """Main application window.

    Creates a tabbed interface containing the various pages and a dockable
    log panel at the bottom.  The ``log`` method is provided to append
    timestamped messages to the panel.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("LDTool")
        self.resize(800, 600)

        # Initialize app state and managers
        self.state = AppState.instance()
        self._adb_manager = None

        # ensure log widget exists before any page may call log()
        self._create_log_panel()
        self.log_bus = LogBus(self)
        self.log_bus.message.connect(self._append_log)
        
        # ADBKeyboard install bus for thread-safe dialog handling
        self.adbkeyboard_install_bus = AdbKeyboardInstallBus(self)
        self.adbkeyboard_install_bus.install_requested.connect(
            self._handle_adbkeyboard_install_request,
            Qt.QueuedConnection  # Ensure UI thread execution
        )
        
        self._create_tabs()

    # ------------------------------------------------------------------
    # UI construction helpers
    # ------------------------------------------------------------------
    def _create_tabs(self) -> None:
        """Build the central tab widget and add each page."""

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.setup_tab = SetupPage(self.log_bus.message.emit)
        self.instances_tab = InstancesPage(self.log_bus.message.emit, self.get_config, self.get_app_state)
        self.app_launcher_tab = AppLauncherPage(
            self.log_bus.message.emit,
            self.get_app_state,
            self.get_adb_manager,
        )
        self.macros_tab = MacroRunnerPage(
            self.log_bus.message.emit,
            self.get_app_state,
            self.get_adb_manager,
        )
        self.reels_poster_tab = ReelsPosterPage(
            self.log_bus.message.emit,
            self.get_config,
            self.get_app_state,
            self.get_adb_manager,
            self.adbkeyboard_install_bus,
        )

        self.tabs.addTab(self.setup_tab, "Setup")
        self.tabs.addTab(self.instances_tab, "Instances")
        self.tabs.addTab(self.app_launcher_tab, "App Launcher")
        self.tabs.addTab(self.macros_tab, "Macros")
        self.tabs.addTab(self.reels_poster_tab, "Reels Poster")

    def _create_log_panel(self) -> None:
        """Create a dockable, read‑only text widget for logging.

        This is called before any tabs are instantiated so that ``self.log``
        can safely be used during page initialization.
        """

        self.log_widget = QPlainTextEdit()
        self.log_widget.setReadOnly(True)

        dock = QDockWidget("Log", self)
        dock.setWidget(self.log_widget)
        dock.setAllowedAreas(Qt.BottomDockWidgetArea)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_config(self) -> dict:
        """Load and return the current configuration dictionary."""
        return load_config()

    def get_app_state(self) -> AppState:
        """Return the singleton app state."""
        return self.state

    def get_adb_manager(self) -> ADBManager:
        """Get or create an ADB manager using the current config."""
        if self._adb_manager is None:
            cfg = self.get_config()
            self._adb_manager = ADBManager(cfg.get("adb_path", ""), self.log_bus.message.emit)
        return self._adb_manager

    def _append_log(self, message: str) -> None:
        """Append ``message`` to the log panel with a timestamp.

        The timestamp is formatted as ``YYYY-MM-DD HH:MM:SS``.  This method
        can be called from anywhere in the application provided a reference
        to the main window is available.
        """

        timestamp = QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
        self.log_widget.appendPlainText(f"[{timestamp}] {message}")

    def log(self, message: str) -> None:
        self.log_bus.message.emit(message)

    # ------------------------------------------------------------------
    # ADBKeyboard Installation Handler (UI Thread)
    # ------------------------------------------------------------------
    def _handle_adbkeyboard_install_request(self, request) -> None:
        """Handle ADBKeyboard installation request in the UI thread.
        
        This method is connected to adbkeyboard_install_bus.install_requested signal
        with Qt.QueuedConnection, ensuring it always runs in the main UI thread
        even when the signal is emitted from a worker thread.
        
        Args:
            request: ADBKeyboardRequest object from the worker thread
        """
        serial = request.serial
        self.log(f"[{serial}] ===== ADBKeyboard Install Request Handler (UI Thread) =====")
        self.log(f"[{serial}] Showing ADBKeyboard installation dialog...")
        
        try:
            from .adbkeyboard_install_dialog import ADBKeyboardInstallDialog
            
            # Create and show dialog in UI thread (safe)
            dialog = ADBKeyboardInstallDialog(
                parent=self,  # Main window as parent
                adb=self.get_adb_manager(),
                serial=serial,
                log_fn=self.log,
            )
            
            # Show modal dialog (blocks UI thread until user completes/cancels)
            success = dialog.exec() == dialog.Accepted and dialog.install_success
            
            # Set result to unblock the worker thread
            if success:
                self.log(f"[{serial}] ✓ User completed ADBKeyboard installation successfully")
                request.set_result(True, None)
            else:
                self.log(f"[{serial}] ✗ User cancelled or installation failed")
                request.set_result(False, "Installation cancelled or failed")
                
        except Exception as e:
            error_msg = f"Failed to show ADBKeyboard dialog: {e}"
            self.log(f"[{serial}] ✗ {error_msg}")
            request.set_result(False, error_msg)
