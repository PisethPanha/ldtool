from __future__ import annotations

from typing import Any, Callable, Dict

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QLineEdit,
    QPushButton,
    QListWidget,
    QListWidgetItem,
)

from src.core.models import AppProfile, AppState
from src.core.adb_manager import ADBManager
from src.core.task_runner import TaskRunner

# Predefined app profiles
PREDEFINED_PROFILES = [
    AppProfile(name="Facebook", package="com.facebook.katana"),
    AppProfile(name="Surfshark", package="com.surfshark.vpnclient.android"),
]


class AppLauncherPage(QWidget):
    """Page for launching apps on selected instances."""

    def __init__(
        self,
        log_fn: Callable[[str], None],
        get_state_fn: Callable[[], AppState],
        get_adb_manager_fn: Callable[[], ADBManager],
    ):
        super().__init__()
        self.log_fn = log_fn
        self.get_state_fn = get_state_fn
        self.get_adb_manager_fn = get_adb_manager_fn
        self._is_closing = False

        self.task_runner = TaskRunner()
        self.task_runner.on_log.connect(self.log)
        self.task_runner.on_error.connect(self._on_task_error)
        self.task_runner.on_done.connect(self._on_task_done)

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Profile selector row
        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("Profile:"))
        self.profile_combo = QComboBox()
        self.profile_combo.addItem("-- Select Profile --", None)
        for prof in PREDEFINED_PROFILES:
            self.profile_combo.addItem(prof.name, prof)
        self.profile_combo.addItem("Custom", "custom")
        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        profile_row.addWidget(self.profile_combo)
        layout.addLayout(profile_row)

        # Custom inputs (hidden by default)
        custom_row = QHBoxLayout()
        custom_row.addWidget(QLabel("Package:"))
        self.package_input = QLineEdit()
        self.package_input.setPlaceholderText("com.example.app")
        self.package_input.setEnabled(False)
        custom_row.addWidget(self.package_input)
        custom_row.addWidget(QLabel("Activity:"))
        self.activity_input = QLineEdit()
        self.activity_input.setPlaceholderText(".MainActivity (optional)")
        self.activity_input.setEnabled(False)
        custom_row.addWidget(self.activity_input)
        layout.addLayout(custom_row)

        # Launch button
        btn_row = QHBoxLayout()
        self.launch_btn = QPushButton("Launch on Selected")
        self.launch_btn.clicked.connect(self.launch_on_selected)
        btn_row.addWidget(self.launch_btn)
        layout.addLayout(btn_row)

        # Results list
        layout.addWidget(QLabel("Results:"))
        self.results_list = QListWidget()
        layout.addWidget(self.results_list)

    def log(self, msg: str) -> None:
        if callable(self.log_fn):
            self.log_fn(msg)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._is_closing = True
        super().closeEvent(event)

    def _on_task_error(self, message: str) -> None:
        if self._is_closing:
            return
        self.log(f"Launch task error: {message}")

    def _on_profile_changed(self) -> None:
        """Update UI when profile selection changes."""
        data = self.profile_combo.currentData()
        is_custom = data == "custom"
        self.package_input.setEnabled(is_custom)
        self.activity_input.setEnabled(is_custom)

    def launch_on_selected(self) -> None:
        """Launch the selected profile on all selected instances."""
        if self._is_closing:
            return
        state = self.get_state_fn()
        selected = state.get_selected_instances()
        if not selected:
            self.log("Select instances in Instances tab first")
            return

        # Get profile info
        data = self.profile_combo.currentData()
        if data is None or (data != "custom" and not data):
            self.log("Please select a profile.")
            return

        if data == "custom":
            package = self.package_input.text().strip()
            activity = self.activity_input.text().strip() or None
            if not package:
                self.log("Custom package name is required.")
                return
        else:
            package = data.package
            activity = data.activity

        self.results_list.clear()
        self.log(f"Launching {package} on {len(selected)} instance(s)...")
        self.task_runner.run(self._do_launch, package, activity)

    def _do_launch(
        self,
        package: str,
        activity: str | None,
        log_fn: Callable[[str], None] | None = None,
        progress_fn=None,
    ) -> Dict[str, bool]:
        """Background task to launch app on all selected instances."""
        results: Dict[str, bool] = {}
        try:
            state = self.get_state_fn()
            adb = self.get_adb_manager_fn()

            for inst in state.get_selected_instances():
                if not inst.adb_serial:
                    log_fn and log_fn(f"Instance {inst.name} has no ADB serial, skipping.")
                    results[inst.name] = False
                    continue

                log_fn and log_fn(f"Launching {package} on {inst.name}...")
                ok = adb.launch_app(inst.adb_serial, package, activity)
                results[inst.name] = ok
        except Exception as exc:  # pragma: no cover - defensive
            log_fn and log_fn(f"App launch worker failed: {exc}")

        return results

    def _on_task_done(self, result: Any) -> None:
        """Update results list when background task completes."""
        if self._is_closing:
            return
        if isinstance(result, dict):
            for instance_name, success in result.items():
                status = "✓ Success" if success else "✗ Failed"
                item = QListWidgetItem(f"{instance_name}: {status}")
                if success:
                    item.setForeground(Qt.green)
                else:
                    item.setForeground(Qt.red)
                self.results_list.addItem(item)
            self.log(f"Launch complete: {sum(result.values())}/{len(result)} succeeded.")
