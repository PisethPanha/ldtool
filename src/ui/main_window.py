from PySide6.QtCore import Qt, QDateTime, QObject, Signal, QTimer
from PySide6.QtWidgets import (
    QMainWindow,
    QTabWidget,
    QDockWidget,
    QLabel,
)

from src.core.config import load_config
from src.core.models import AppState
from src.core.adb_manager import ADBManager
from .setup_page import SetupPage
from .instances_page import InstancesPage
from .app_launcher_page import AppLauncherPage
from .macro_runner_page import MacroRunnerPage
from .reels_poster_page import ReelsPosterPage
from .enhanced_log_panel import EnhancedLogPanel


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
        self.resize(1100, 650)
        self.setMinimumSize(900, 950)

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
        self._create_status_bar()

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
            self.get_config,
            self.get_app_state,
            self.get_adb_manager,
        )
        self.macros_tab = MacroRunnerPage(
            self.log_bus.message.emit,
            self.get_app_state,
            self.get_adb_manager,
            self.get_config,
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
        """Create a dockable, enhanced log widget for logging.

        This is called before any tabs are instantiated so that ``self.log``
        can safely be used during page initialization.
        """

        self.log_widget = EnhancedLogPanel()

        dock = QDockWidget("System Log", self)
        dock.setWidget(self.log_widget)
        dock.setAllowedAreas(Qt.BottomDockWidgetArea)
        dock.setMaximumHeight(150)
        dock.setMinimumHeight(100)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)

    def _create_status_bar(self) -> None:
        """Create a persistent status bar with runtime summary labels."""
        bar = self.statusBar()
        self.instances_status = QLabel("Instances: 0 running")
        self.queue_status = QLabel("Queue: 0 tasks")
        self.ldplayer_status = QLabel("LDPlayer: detected")
        self.adb_status = QLabel("ADB: connected")

        bar.addPermanentWidget(self.instances_status)
        bar.addPermanentWidget(self.queue_status)
        bar.addPermanentWidget(self.ldplayer_status)
        bar.addPermanentWidget(self.adb_status)

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(2500)
        self._status_timer.timeout.connect(self._refresh_status_bar)
        self._status_timer.start()
        self._refresh_status_bar()

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

    def _refresh_status_bar(self) -> None:
        """Refresh live status bar indicators without changing app behaviour."""
        try:
            selected_count = len(self.state.get_selected_instances())
        except Exception:
            selected_count = 0

        queue_count = 0
        try:
            queue_count = len(self.reels_poster_tab.queue_manager.processes)
        except Exception:
            queue_count = 0

        self.instances_status.setText(f"Instances: {selected_count} selected")
        self.queue_status.setText(f"Queue: {queue_count} tasks")

        try:
            adb = self.get_adb_manager()
            self.adb_status.setText("ADB: connected" if adb.list_devices() else "ADB: no devices")
        except Exception:
            self.adb_status.setText("ADB: unavailable")

        cfg = self.get_config()
        has_dnconsole = bool(cfg.get("dnconsole_path", "").strip())
        self.ldplayer_status.setText("LDPlayer: detected" if has_dnconsole else "LDPlayer: not configured")

    def _append_log(self, message: str) -> None:
        """Append ``message`` to the enhanced log panel with colour coding."""
        self.log_widget.append_message(message)

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
