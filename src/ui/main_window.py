from PySide6.QtCore import Qt, QDateTime
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
        self._create_tabs()

    # ------------------------------------------------------------------
    # UI construction helpers
    # ------------------------------------------------------------------
    def _create_tabs(self) -> None:
        """Build the central tab widget and add each page."""

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.setup_tab = SetupPage(self.log)
        self.instances_tab = InstancesPage(self.log, self.get_config, self.get_app_state)
        self.app_launcher_tab = AppLauncherPage(self.log, self.get_app_state, self.get_adb_manager)
        self.macros_tab = MacroRunnerPage(self.log, self.get_app_state, self.get_adb_manager)

        self.tabs.addTab(self.setup_tab, "Setup")
        self.tabs.addTab(self.instances_tab, "Instances")
        self.tabs.addTab(self.app_launcher_tab, "App Launcher")
        self.tabs.addTab(self.macros_tab, "Macros")

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
            self._adb_manager = ADBManager(cfg.get("adb_path", ""), self.log)
        return self._adb_manager

    def log(self, message: str) -> None:
        """Append ``message`` to the log panel with a timestamp.

        The timestamp is formatted as ``YYYY-MM-DD HH:MM:SS``.  This method
        can be called from anywhere in the application provided a reference
        to the main window is available.
        """

        timestamp = QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
        self.log_widget.appendPlainText(f"[{timestamp}] {message}")
