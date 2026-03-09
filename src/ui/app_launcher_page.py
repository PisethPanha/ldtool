from __future__ import annotations

import time
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
    QGroupBox,
    QSplitter,
)

from src.core.models import AppProfile, AppState
from src.core.adb_manager import ADBManager
from src.core.ldplayer_controller import LDPlayerController
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
        get_config_fn: Callable[[], Dict[str, Any]],
        get_state_fn: Callable[[], AppState],
        get_adb_manager_fn: Callable[[], ADBManager],
    ):
        super().__init__()
        self.log_fn = log_fn
        self.get_config_fn = get_config_fn
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
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        # Compact configuration section
        config_group = QGroupBox("App Launch Configuration")
        config_layout = QVBoxLayout(config_group)
        config_layout.setContentsMargins(10, 10, 10, 10)
        config_layout.setSpacing(6)

        # Profile selector row
        profile_row = QHBoxLayout()
        profile_row.setSpacing(6)
        profile_label = QLabel("Profile:")
        profile_label.setStyleSheet("padding: 10px; border-radius: 10px; background-color: #2a2c3e;")
        profile_row.addWidget(profile_label)
        self.profile_combo = QComboBox()
        self.profile_combo.setFixedHeight(30)
        self.profile_combo.addItem("-- Select Profile --", None)
        for prof in PREDEFINED_PROFILES:
            self.profile_combo.addItem(prof.name, prof)
        self.profile_combo.addItem("Custom", "custom")
        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        profile_row.addWidget(self.profile_combo, 1)
        config_layout.addLayout(profile_row)

        # Custom inputs row
        custom_row = QHBoxLayout()
        custom_row.setSpacing(6)
        package_label = QLabel("Package:")
        package_label.setStyleSheet("padding: 10px; border-radius: 10px; background-color: #2a2c3e;")
        custom_row.addWidget(package_label)
        self.package_input = QLineEdit()
        self.package_input.setFixedHeight(30)
        self.package_input.setPlaceholderText("com.example.app")
        self.package_input.setEnabled(False)
        custom_row.addWidget(self.package_input, 2)
        activity_label = QLabel("Activity:")
        activity_label.setStyleSheet("padding: 10px; border-radius: 10px; background-color: #2a2c3e;")
        custom_row.addWidget(activity_label)
        self.activity_input = QLineEdit()
        self.activity_input.setFixedHeight(30)
        self.activity_input.setPlaceholderText(".MainActivity (optional)")
        self.activity_input.setEnabled(False)
        custom_row.addWidget(self.activity_input, 1)
        config_layout.addLayout(custom_row)

        # Launch button row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.launch_btn = QPushButton("Launch on Selected Instances")
        self.launch_btn.setFixedHeight(32)
        self.launch_btn.clicked.connect(self.launch_on_selected)
        btn_row.addWidget(self.launch_btn)
        btn_row.addStretch()
        config_layout.addLayout(btn_row)

        layout.addWidget(config_group)

        # Results section (expandable)
        results_group = QGroupBox("Launch Results")
        results_layout = QVBoxLayout(results_group)
        results_layout.setContentsMargins(10, 10, 10, 10)
        results_layout.setSpacing(6)
        
        self.results_list = QListWidget()
        self.results_list.setMinimumHeight(100)
        results_layout.addWidget(self.results_list)

        layout.addWidget(results_group, 1)  # Give results area stretch factor

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
        """Background task to launch app on all selected instances.

        Freshly resolves ADB serials for every selected instance using
        dnconsole + adb devices, waits up to 20 s for unresolved running
        instances, then launches the app.  Each instance is processed
        independently so one failure does not block others.
        """
        _log = log_fn or (lambda m: None)
        results: Dict[str, bool] = {}
        WAIT_TIMEOUT = 20

        try:
            state = self.get_state_fn()
            cfg = self.get_config_fn()
            adb = ADBManager(cfg.get("adb_path", ""), _log)
            ctrl = LDPlayerController(cfg.get("dnconsole_path", ""), _log)

            selected = state.get_selected_instances()
            if not selected:
                _log("No instances selected.")
                return results

            _log(f"=== App Launch: {package} on {len(selected)} instance(s) ===")

            # Step 1: refresh instance list from dnconsole
            _log("Refreshing instance list from dnconsole...")
            fresh_instances = ctrl.list_instances()
            running_indexes = {
                int(i["index"]) for i in fresh_instances if i.get("is_running")
            }
            _log(f"Running instance indexes: {sorted(running_indexes)}")

            # Step 2: resolve serials per instance
            resolved: int = 0
            unresolved: int = 0
            launched_ok: int = 0
            launched_fail: int = 0

            for inst in selected:
                label = f"{inst.name} (index {inst.index})"
                expected_serial = adb.serial_for_index(inst.index)
                _log(f"--- Resolving serial for {label} ---")
                _log(f"  Expected serial: {expected_serial}")

                is_running = inst.index in running_indexes
                _log(f"  Running (dnconsole): {is_running}")

                if not is_running:
                    _log(f"  Instance {label} is not running. Skipping.")
                    results[inst.name] = False
                    unresolved += 1
                    continue

                # Try to find serial immediately
                serial = self._wait_for_serial(
                    adb, inst.index, expected_serial, label, _log, WAIT_TIMEOUT
                )

                if not serial:
                    _log(
                        f"  ⚠ Instance {label} stayed unresolved after "
                        f"{WAIT_TIMEOUT}s waiting for ADB attachment."
                    )
                    results[inst.name] = False
                    unresolved += 1
                    continue

                # Serial found — update state
                inst.adb_serial = serial
                resolved += 1
                _log(f"  Final resolved serial: {serial}")

                # Launch
                _log(f"  Launching {package} on {label} ({serial})...")
                ok = adb.launch_app(serial, package, activity)
                results[inst.name] = ok
                if ok:
                    _log(f"  ✓ {label}: launch succeeded")
                    launched_ok += 1
                else:
                    _log(f"  ✗ {label}: launch failed")
                    launched_fail += 1

            # Summary
            _log("=== Launch Summary ===")
            _log(f"  Selected:   {len(selected)}")
            _log(f"  Resolved:   {resolved}")
            _log(f"  Launched OK:{launched_ok}")
            _log(f"  Unresolved: {unresolved}")
            _log(f"  Failed:     {launched_fail}")

        except Exception as exc:  # pragma: no cover - defensive
            _log(f"App launch worker failed: {exc}")

        return results

    @staticmethod
    def _wait_for_serial(
        adb: ADBManager,
        index: int,
        expected_serial: str,
        label: str,
        _log: Callable[[str], None],
        timeout: int,
    ) -> str | None:
        """Poll ADB devices for up to *timeout* seconds until the expected
        serial appears and the device has finished booting."""

        for attempt in range(timeout + 1):
            devices = set(adb.list_devices())
            if expected_serial in devices:
                _log(f"  ADB serial {expected_serial} found (attempt {attempt})")
                # Check boot readiness
                if adb.is_device_ready(expected_serial):
                    _log(f"  Boot completed ✓")
                    return expected_serial
                else:
                    _log(f"  Device attached but still booting (attempt {attempt})...")
            else:
                if attempt > 0:
                    _log(f"  Waiting for ADB attach (attempt {attempt}/{timeout})...")
            if attempt < timeout:
                time.sleep(1)

        # Final check — device may be attached but boot not finished, still usable
        if expected_serial in set(adb.list_devices()):
            _log(f"  ADB serial {expected_serial} attached (boot not confirmed, proceeding anyway)")
            return expected_serial

        return None

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
