from __future__ import annotations

from typing import Any, Callable, Dict, List
from src.core.models import LDInstance
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QPushButton,
    QCheckBox,
    QSpinBox,
    QLabel,
    QComboBox,
    QRadioButton,
    QButtonGroup,
    QGroupBox,
)

from src.core.ldplayer_controller import LDPlayerController
from src.core.adb_manager import ADBManager
from src.core.task_runner import TaskRunner
from src.core.window_manager import WindowManager


class InstancesPage(QWidget):
    """Page used for scanning and controlling emulator instances."""

    COL_SELECT = 0
    COL_INDEX = 1
    COL_NAME = 2
    COL_STATUS = 3
    COL_SERIAL = 4

    def __init__(
        self,
        log_fn: Callable[[str], None],
        get_config_fn: Callable[[], Dict[str, str]],
        get_app_state_fn: Callable[[], Any],
    ):
        super().__init__()
        self.log_fn = log_fn
        self.get_config_fn = get_config_fn
        self.get_app_state_fn = get_app_state_fn
        # capture current state for faster access
        self.state = get_app_state_fn()
        # guard used to suppress recursive signal handling when we
        # update the table programmatically
        self._updating_table = False

        self.task_runner = TaskRunner()
        self.task_runner.on_log.connect(self.log)
        self.task_runner.on_progress.connect(self._on_task_progress)
        self.task_runner.on_done.connect(self._on_task_done)

        self._index_to_row: Dict[int, int] = {}
        self.instances: List[Dict[str, Any]] = []

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        # Compact toolbar row
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(6)
        self.scan_btn = QPushButton("Scan")
        self.scan_btn.setFixedHeight(30)
        self.scan_btn.clicked.connect(self.scan)
        self.start_btn = QPushButton("Start Selected")
        self.start_btn.setFixedHeight(30)
        self.start_btn.clicked.connect(self.start_selected)
        self.stop_btn = QPushButton("Stop Selected")
        self.stop_btn.setFixedHeight(30)
        self.stop_btn.clicked.connect(self.stop_selected)
        self.reconnect_btn = QPushButton("Reconnect ADB")
        self.reconnect_btn.setFixedHeight(30)
        self.reconnect_btn.clicked.connect(self.reconnect_selected)
        self.refresh_btn = QPushButton("Refresh ADB")
        self.refresh_btn.setFixedHeight(30)
        self.refresh_btn.clicked.connect(self._refresh_serials)
        
        self.stagger_checkbox = QCheckBox("Staggered")
        self.delay_spin = QSpinBox()
        self.delay_spin.setFixedHeight(28)
        self.delay_spin.setFixedWidth(60)
        self.delay_spin.setRange(0, 60)
        self.delay_spin.setValue(1)
        
        ctrl_row.addWidget(self.scan_btn)
        ctrl_row.addWidget(self.start_btn)
        ctrl_row.addWidget(self.stop_btn)
        ctrl_row.addWidget(self.reconnect_btn)
        ctrl_row.addWidget(self.refresh_btn)
        ctrl_row.addWidget(QLabel("│"))
        ctrl_row.addWidget(self.stagger_checkbox)
        delay_label = QLabel("delay:")
        delay_label.setStyleSheet("padding: 10px; border-radius: 10px; background-color: #2a2c3e;")
        ctrl_row.addWidget(delay_label)
        ctrl_row.addWidget(self.delay_spin)
        ctrl_row.addWidget(QLabel("s"))
        ctrl_row.addStretch()

        layout.addLayout(ctrl_row)

        # Compact selection buttons row
        sel_row = QHBoxLayout()
        sel_row.setSpacing(6)
        sel_all_btn = QPushButton("Select All")
        sel_all_btn.setFixedHeight(28)
        sel_all_btn.clicked.connect(self._select_all)
        sel_running_btn = QPushButton("Running/Booting")
        sel_running_btn.setFixedHeight(28)
        sel_running_btn.clicked.connect(self._select_running_booting)
        sel_offline_btn = QPushButton("Offline")
        sel_offline_btn.setFixedHeight(28)
        sel_offline_btn.clicked.connect(self._select_offline)
        unsel_btn = QPushButton("Unselect All")
        unsel_btn.setFixedHeight(28)
        unsel_btn.clicked.connect(self._unselect_all)
        sel_row.addWidget(sel_all_btn)
        sel_row.addWidget(sel_running_btn)
        sel_row.addWidget(sel_offline_btn)
        sel_row.addWidget(unsel_btn)
        sel_row.addStretch()
        layout.addLayout(sel_row)

        # Instances table (main content - should expand)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Select", "Index", "Name", "Status", "ADB Serial"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setMinimumHeight(120)
        # Remove maximum height to allow it to expand
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table, 1)  # Give table stretch factor

        # Compact Window Management section
        win_group = QGroupBox("Window Management")
        win_layout = QVBoxLayout(win_group)
        win_layout.setContentsMargins(8, 8, 8, 8)
        win_layout.setSpacing(6)

        # First row: monitor and layout mode
        win_ctrl_row1 = QHBoxLayout()
        win_ctrl_row1.setSpacing(6)
        monitor_label = QLabel("Monitor:")
        monitor_label.setStyleSheet("padding: 10px; border-radius: 10px; background-color: #2a2c3e;")
        win_ctrl_row1.addWidget(monitor_label)
        self.monitor_combo = QComboBox()
        self.monitor_combo.setFixedHeight(28)
        self._populate_monitor_dropdown()
        win_ctrl_row1.addWidget(self.monitor_combo)
        
        win_ctrl_row1.addWidget(QLabel("│ Layout:"))
        self.layout_auto_radio = QRadioButton("Auto")
        self.layout_cols_radio = QRadioButton("Fixed Cols")
        self.layout_rows_radio = QRadioButton("Fixed Rows")
        self.layout_auto_radio.setChecked(True)
        layout_group = QButtonGroup(self)
        layout_group.addButton(self.layout_auto_radio)
        layout_group.addButton(self.layout_cols_radio)
        layout_group.addButton(self.layout_rows_radio)
        self.layout_auto_radio.toggled.connect(self._on_layout_mode_changed)
        self.layout_cols_radio.toggled.connect(self._on_layout_mode_changed)
        self.layout_rows_radio.toggled.connect(self._on_layout_mode_changed)
        win_ctrl_row1.addWidget(self.layout_auto_radio)
        win_ctrl_row1.addWidget(self.layout_cols_radio)
        win_ctrl_row1.addWidget(self.layout_rows_radio)
        win_ctrl_row1.addStretch()
        win_layout.addLayout(win_ctrl_row1)

        # Second row: dimensions and padding
        win_ctrl_row2 = QHBoxLayout()
        win_ctrl_row2.setSpacing(6)
        cols_label = QLabel("Cols:")
        cols_label.setStyleSheet("padding: 10px; border-radius: 10px; background-color: #2a2c3e;")
        win_ctrl_row2.addWidget(cols_label)
        self.cols_spin = QSpinBox()
        self.cols_spin.setFixedHeight(28)
        self.cols_spin.setFixedWidth(60)
        self.cols_spin.setRange(1, 10)
        self.cols_spin.setValue(2)
        self.cols_spin.setEnabled(False)
        win_ctrl_row2.addWidget(self.cols_spin)
        
        rows_label = QLabel("Rows:")
        rows_label.setStyleSheet("padding: 10px; border-radius: 10px; background-color: #2a2c3e;")
        win_ctrl_row2.addWidget(rows_label)
        self.rows_spin = QSpinBox()
        self.rows_spin.setFixedHeight(28)
        self.rows_spin.setFixedWidth(60)
        self.rows_spin.setRange(1, 10)
        self.rows_spin.setValue(2)
        self.rows_spin.setEnabled(False)
        win_ctrl_row2.addWidget(self.rows_spin)
        
        padding_label = QLabel("Padding:")
        padding_label.setStyleSheet("padding: 10px; border-radius: 10px; background-color: #2a2c3e;")
        win_ctrl_row2.addWidget(padding_label)
        self.padding_spin = QSpinBox()
        self.padding_spin.setFixedHeight(28)
        self.padding_spin.setFixedWidth(60)
        self.padding_spin.setRange(0, 100)
        self.padding_spin.setValue(10)
        win_ctrl_row2.addWidget(self.padding_spin)
        win_ctrl_row2.addWidget(QLabel("px"))
        
        # Window action buttons in same row
        win_ctrl_row2.addWidget(QLabel("│"))
        self.arrange_btn = QPushButton("Arrange Selected")
        self.arrange_btn.setFixedHeight(28)
        self.arrange_btn.clicked.connect(self.arrange_selected_windows)
        self.restore_btn = QPushButton("Restore Selected")
        self.restore_btn.setFixedHeight(28)
        self.restore_btn.clicked.connect(self.restore_selected_windows)
        self.minimize_btn = QPushButton("Minimize Selected")
        self.minimize_btn.setFixedHeight(28)
        self.minimize_btn.clicked.connect(self.minimize_selected_windows)
        win_ctrl_row2.addWidget(self.arrange_btn)
        win_ctrl_row2.addWidget(self.restore_btn)
        win_ctrl_row2.addWidget(self.minimize_btn)
        win_ctrl_row2.addStretch()
        win_layout.addLayout(win_ctrl_row2)

        layout.addWidget(win_group)

    def log(self, msg: str) -> None:
        if callable(self.log_fn):
            self.log_fn(msg)

    # ------------------------------------------------------------------
    # UI actions
    # ------------------------------------------------------------------
    def scan(self) -> None:
        self.log("Starting instance scan + ADB resolution...")
        self.task_runner.run(self._do_scan)

    def start_selected(self) -> None:
        indices = self._selected_indices()
        if not indices:
            self.log("No instances selected for start.")
            return
        stagger = self.stagger_checkbox.isChecked()
        delay = self.delay_spin.value()
        self.log(f"Starting {len(indices)} instance(s) with{' ' if stagger else ' no '}stagger.")
        self.task_runner.run(self._do_start, indices, stagger, delay)

    def stop_selected(self) -> None:
        indices = self._selected_indices()
        if not indices:
            self.log("No instances selected for stop.")
            return
        self.log(f"Stopping {len(indices)} instance(s)...")
        self.task_runner.run(self._do_stop, indices)

    def reconnect_selected(self) -> None:
        self.log("Listing ADB devices and reconnecting all...")
        self.task_runner.run(self._do_reconnect)

    def _refresh_serials(self) -> None:
        """Re-scan instances and resolve ADB serials for all running ones."""
        self.log("Refreshing instances and ADB serials...")
        self.task_runner.run(self._do_scan)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _selected_indices(self) -> List[int]:
        out: List[int] = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, self.COL_SELECT)
            if item and item.checkState() == Qt.Checked:
                idx_item = self.table.item(row, self.COL_INDEX)
                if idx_item:
                    try:
                        out.append(int(idx_item.text()))
                    except ValueError:
                        pass
        return out

    def _selected_serials(self) -> List[str]:
        out: List[str] = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, self.COL_SELECT)
            if item and item.checkState() == Qt.Checked:
                serial_item = self.table.item(row, self.COL_SERIAL)
                if serial_item:
                    out.append(serial_item.text())
        return out

    def _on_item_changed(self, item) -> None:
        """Handle when a table item changes.

        We only care about the checkbox column; when it toggles we update the
        shared ``AppState`` selection.  A guard flag prevents recursion when
        the table is being updated programmatically.
        """
        if self._updating_table:
            return

        # only respond to the select/checkbox column
        if item.column() != self.COL_SELECT:
            return

        # determine instance index for this row
        row = item.row()
        idx = None
        idx_item = self.table.item(row, self.COL_INDEX)
        if idx_item:
            try:
                idx = int(idx_item.text())
            except ValueError:
                idx = None
        if idx is None:
            # fall back to lookup mapping
            for real_idx, r in self._index_to_row.items():
                if r == row:
                    idx = real_idx
                    break
        if idx is None:
            return

        selected = item.checkState() == Qt.Checked
        # update shared state
        self.get_app_state_fn().set_selected(idx, selected)

    # ------------------------------------------------------------------
    # Bulk selection helpers
    # ------------------------------------------------------------------
    def _set_all_check_states(self, predicate=None) -> None:
        """Check rows matching predicate (or all if None), uncheck the rest."""
        self._updating_table = True
        try:
            for row in range(self.table.rowCount()):
                sel_item = self.table.item(row, self.COL_SELECT)
                if not sel_item:
                    continue
                if predicate is None:
                    checked = True
                else:
                    status_item = self.table.item(row, self.COL_STATUS)
                    status_text = status_item.text() if status_item else ""
                    checked = predicate(status_text)
                sel_item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
                # sync shared state
                idx_item = self.table.item(row, self.COL_INDEX)
                if idx_item:
                    try:
                        self.get_app_state_fn().set_selected(int(idx_item.text()), checked)
                    except ValueError:
                        pass
        finally:
            self._updating_table = False

    def _select_all(self) -> None:
        self._set_all_check_states(predicate=None)

    def _select_running_booting(self) -> None:
        self._set_all_check_states(
            predicate=lambda s: "Running" in s or "Booting" in s or "Starting" in s
        )

    def _select_offline(self) -> None:
        self._set_all_check_states(predicate=lambda s: "Offline" in s)

    def _unselect_all(self) -> None:
        self._set_all_check_states(predicate=lambda _: False)

    @staticmethod
    def _make_status_item(status: str) -> QTableWidgetItem:
        """Create a colour-coded status QTableWidgetItem."""
        _COLORS = {
            "running": ("#4CAF50", "● Running"),
            "booting": ("#FF9800", "● Booting"),
            "starting": ("#FF9800", "● Starting"),
            "stopped": ("#9E9E9E", "● Offline"),
        }
        color, label = _COLORS.get(status, ("#9E9E9E", f"● {status.capitalize()}"))
        item = QTableWidgetItem(label)
        item.setForeground(QColor(color))
        item.setFlags(Qt.ItemIsEnabled)
        return item

    def _on_task_progress(self, instance_id: int, percent: int) -> None:
        row = self._index_to_row.get(instance_id)
        if row is None:
            return
        if percent >= 100:
            status = "running"
        elif percent >= 50:
            status = "booting"
        else:
            status = "starting"
        self.table.setItem(row, self.COL_STATUS, self._make_status_item(status))
        # update state running flag
        inst = next((i for i in self.state.instances if i.index == instance_id), None)
        if inst:
            inst.is_running = (status == "running")

    def _on_task_done(self, result: Any) -> None:
        """Update UI/state when background task completes."""
        if isinstance(result, dict) and "instances" in result and "serials" in result:
            # scan+resolve result
            self._populate_table(result["instances"], result["serials"])
        elif isinstance(result, list):
            # legacy scan result (shouldn't happen but handle gracefully)
            self._populate_table(result)
        elif isinstance(result, dict):
            # start/stop/reconnect may return dicts
            for idx, val in result.items():
                if isinstance(val, str):
                    row = self._index_to_row.get(idx)
                    if row is not None:
                        self.table.setItem(row, self.COL_SERIAL, QTableWidgetItem(val))
                    # update state instance serial
                    inst = next((i for i in self.state.instances if i.index == idx), None)
                    if inst is not None:
                        inst.adb_serial = val
        # else ignore

    def _populate_table(self, instances: List[Dict[str, Any]], serials: Dict[int, str] | None = None) -> None:
        if serials is None:
            serials = {}
        self.instances = instances
        # update shared state
        ld_instances = []
        for inst in instances:
            idx = int(inst.get("index", -1))
            existing = self.state.instances_by_index.get(idx)
            if isinstance(existing, LDInstance):
                # Update running state from fresh scan
                existing.is_running = bool(inst.get("is_running", False))
                # Update serial from fresh resolve
                if idx in serials:
                    existing.adb_serial = serials[idx]
                elif not inst.get("is_running"):
                    existing.adb_serial = None
                ld_instances.append(existing)
            else:
                ld_instances.append(
                    LDInstance(
                        index=idx,
                        name=str(inst.get("name", "")),
                        is_running=bool(inst.get("is_running", False)),
                        adb_serial=serials.get(idx),
                    )
                )
        self.state.set_instances(ld_instances)

        # programmatic updates should not trigger itemChanged handler
        self._updating_table = True
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self._index_to_row.clear()
        for inst in instances:
            row = self.table.rowCount()
            self.table.insertRow(row)
            sel_item = QTableWidgetItem()
            sel_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            sel_item.setCheckState(Qt.Checked if inst.get("index") in self.state.selected_indexes else Qt.Unchecked)
            self.table.setItem(row, self.COL_SELECT, sel_item)

            idx_item = QTableWidgetItem(str(inst.get("index", "")))
            idx_item.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(row, self.COL_INDEX, idx_item)

            name_item = QTableWidgetItem(str(inst.get("name", "")))
            name_item.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(row, self.COL_NAME, name_item)

            status_item = QTableWidgetItem(
                "● Running" if inst.get("is_running", False) else "● Offline"
            )
            status_item.setForeground(
                QColor("#4CAF50") if inst.get("is_running", False) else QColor("#9E9E9E")
            )
            status_item.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(row, self.COL_STATUS, status_item)

            self.table.setItem(row, self.COL_SERIAL, QTableWidgetItem(
                serials.get(idx, "") if idx >= 0 else ""
            ))

            try:
                idx = int(inst.get("index", -1))
            except ValueError:
                idx = -1
            if idx >= 0:
                self._index_to_row[idx] = row
        self.table.blockSignals(False)
        self._updating_table = False

    # ------------------------------------------------------------------
    # window management
    # ------------------------------------------------------------------
    def _populate_monitor_dropdown(self) -> None:
        """Populate the monitor dropdown with available monitors."""
        self.monitor_combo.clear()
        self.monitor_combo.addItem("Primary", 0)  # index 0 = primary
        work_areas = WindowManager.get_monitor_work_areas()
        for idx, (left, top, right, bottom) in enumerate(work_areas[1:], start=1):
            self.monitor_combo.addItem(f"Monitor {idx+1}", idx)

    def _on_layout_mode_changed(self) -> None:
        """Update spinbox enabled states based on layout mode."""
        if self.layout_cols_radio.isChecked():
            self.cols_spin.setEnabled(True)
            self.rows_spin.setEnabled(False)
        elif self.layout_rows_radio.isChecked():
            self.cols_spin.setEnabled(False)
            self.rows_spin.setEnabled(True)
        else:  # Auto
            self.cols_spin.setEnabled(False)
            self.rows_spin.setEnabled(False)

    def arrange_selected_windows(self) -> None:
        """Arrange selected instance windows on the chosen monitor."""
        # get selected instances
        state = self.get_app_state_fn()
        instances = state.get_selected_instances()
        if not instances:
            self.log("No instances selected for window arrangement.")
            return

        monitor_idx = self.monitor_combo.currentData() or 0
        layout_mode = "auto"
        if self.layout_cols_radio.isChecked():
            layout_mode = "cols"
        elif self.layout_rows_radio.isChecked():
            layout_mode = "rows"

        cols = self.cols_spin.value()
        rows = self.rows_spin.value()
        padding = self.padding_spin.value()

        self.log(f"Arranging {len(instances)} window(s) on monitor {monitor_idx+1}...")
        self.task_runner.run(
            self._arrange_windows_worker,
            instances,
            monitor_idx,
            layout_mode,
            cols,
            rows,
            padding,
        )

    def restore_selected_windows(self) -> None:
        """Restore selected instance windows."""
        state = self.get_app_state_fn()
        instances = state.get_selected_instances()
        if not instances:
            self.log("No instances selected for window restore.")
            return

        self.log(f"Restoring {len(instances)} window(s)...")
        self.task_runner.run(self._restore_windows_worker, instances)

    def minimize_selected_windows(self) -> None:
        """Minimize selected instance windows."""
        state = self.get_app_state_fn()
        instances = state.get_selected_instances()
        if not instances:
            self.log("No instances selected for window minimize.")
            return

        self.log(f"Minimizing {len(instances)} window(s)...")
        self.task_runner.run(self._minimize_windows_worker, instances)

    # ------------------------------------------------------------------
    # window management workers
    # ------------------------------------------------------------------
    def _arrange_windows_worker(
        self,
        instances: List[Any],
        monitor_idx: int,
        layout_mode: str,
        cols: int,
        rows: int,
        padding: int,
        log_fn=None,
        progress_fn=None,
    ) -> Dict[str, bool]:
        """Background worker to arrange windows in a grid."""
        results: Dict[str, bool] = {}
        work_areas = WindowManager.get_monitor_work_areas()
        if monitor_idx >= len(work_areas):
            monitor_idx = 0

        left, top, right, bottom = work_areas[monitor_idx]
        work_width = right - left - padding * 2
        work_height = bottom - top - padding * 2

        # compute grid dimensions
        num_instances = len(instances)
        if layout_mode == "cols":
            grid_cols = cols
            grid_rows = (num_instances + cols - 1) // cols
        elif layout_mode == "rows":
            grid_rows = rows
            grid_cols = (num_instances + rows - 1) // rows
        else:  # auto
            import math
            grid_cols = max(1, int(math.ceil(math.sqrt(num_instances))))
            grid_rows = (num_instances + grid_cols - 1) // grid_cols

        win_width = work_width // grid_cols
        win_height = work_height // grid_rows

        # find and arrange each window
        windows_by_title = WindowManager.find_windows_by_title_keywords(
            [inst.name for inst in instances]
        )

        for pos, inst in enumerate(instances):
            hwnd = windows_by_title.get(inst.name)
            if not hwnd:
                msg = f"Window for '{inst.name}' not found"
                log_fn and log_fn(msg)
                results[inst.name] = False
                continue

            # compute grid position
            row = pos // grid_cols
            col = pos % grid_cols
            x = left + padding + col * win_width
            y = top + padding + row * win_height

            ok = WindowManager.move_resize(hwnd, x, y, win_width, win_height)
            if ok:
                log_fn and log_fn(f"Arranged '{inst.name}' at ({row},{col})")
                results[inst.name] = True
            else:
                log_fn and log_fn(f"Failed to arrange '{inst.name}'")
                results[inst.name] = False

            progress_fn and progress_fn(inst.index, int((pos + 1) * 100 / num_instances))

        return results

    def _restore_windows_worker(
        self,
        instances: List[Any],
        log_fn=None,
        progress_fn=None,
    ) -> Dict[str, bool]:
        """Background worker to restore windows."""
        results: Dict[str, bool] = {}
        windows_by_title = WindowManager.find_windows_by_title_keywords(
            [inst.name for inst in instances]
        )

        for pos, inst in enumerate(instances):
            hwnd = windows_by_title.get(inst.name)
            if not hwnd:
                log_fn and log_fn(f"Window for '{inst.name}' not found")
                results[inst.name] = False
                continue

            ok = WindowManager.restore_window(hwnd)
            if ok:
                log_fn and log_fn(f"Restored '{inst.name}'")
                results[inst.name] = True
            else:
                log_fn and log_fn(f"Failed to restore '{inst.name}'")
                results[inst.name] = False

            progress_fn and progress_fn(inst.index, int((pos + 1) * 100 / len(instances)))

        return results

    def _minimize_windows_worker(
        self,
        instances: List[Any],
        log_fn=None,
        progress_fn=None,
    ) -> Dict[str, bool]:
        """Background worker to minimize windows."""
        results: Dict[str, bool] = {}
        windows_by_title = WindowManager.find_windows_by_title_keywords(
            [inst.name for inst in instances]
        )

        for pos, inst in enumerate(instances):
            hwnd = windows_by_title.get(inst.name)
            if not hwnd:
                log_fn and log_fn(f"Window for '{inst.name}' not found")
                results[inst.name] = False
                continue

            ok = WindowManager.minimize_window(hwnd)
            if ok:
                log_fn and log_fn(f"Minimized '{inst.name}'")
                results[inst.name] = True
            else:
                log_fn and log_fn(f"Failed to minimize '{inst.name}'")
                results[inst.name] = False

            progress_fn and progress_fn(inst.index, int((pos + 1) * 100 / len(instances)))

        return results

    # ------------------------------------------------------------------
    # background task implementations
    # ------------------------------------------------------------------
    def _do_scan(self, log_fn: Callable[[str], None] | None = None, progress_fn=None) -> Dict[str, Any]:
        _log = log_fn or (lambda m: None)
        cfg = self.get_config_fn()
        ctrl = LDPlayerController(cfg.get("dnconsole_path", ""), _log)
        adb = ADBManager(cfg.get("adb_path", ""), _log)

        instances = ctrl.list_instances()
        resolved = adb.resolve_instance_serials(instances, wait_timeout=15)
        return {"instances": instances, "serials": resolved}

    def _do_start(
        self,
        indices: List[int],
        stagger: bool,
        delay: int,
        log_fn: Callable[[str], None] | None = None,
        progress_fn=None,
    ) -> Dict[int, str]:
        cfg = self.get_config_fn()
        ctrl = LDPlayerController(cfg.get("dnconsole_path", ""), log_fn or (lambda m: None))
        adb = ADBManager(cfg.get("adb_path", ""), log_fn or (lambda m: None))
        serials: Dict[int, str] = {}
        import time
        for idx in indices:
            log_fn and log_fn(f"starting instance {idx}")
            expected_serial = adb.serial_for_index(idx)
            progress_fn and progress_fn(idx, 10)

            # Check if already running with expected serial
            devices = set(adb.list_devices())
            if expected_serial in devices:
                log_fn and log_fn(f"instance {idx} already has ADB device {expected_serial}")
                serials[idx] = expected_serial
                progress_fn and progress_fn(idx, 100)
                if stagger and delay > 0:
                    time.sleep(delay)
                continue

            # Start the instance via dnconsole
            ok = ctrl.start_instance(idx)
            if not ok:
                log_fn and log_fn(f"failed to start {idx}")
                progress_fn and progress_fn(idx, 0)
                continue

            progress_fn and progress_fn(idx, 20)
            log_fn and log_fn(f"instance {idx} started, waiting for ADB device {expected_serial}...")

            # Wait for the expected serial to appear
            serial = None
            for attempt in range(30):
                time.sleep(1)
                if expected_serial in set(adb.list_devices()):
                    serial = expected_serial
                    break
            if not serial:
                # Fallback: try detecting any new device
                serial = expected_serial if expected_serial in set(adb.list_devices()) else None
            if not serial:
                log_fn and log_fn(
                    f"Error: ADB device {expected_serial} did not appear for instance {idx}. "
                    "Check ADB debugging is enabled in LDPlayer settings."
                )
                progress_fn and progress_fn(idx, 0)
                continue

            progress_fn and progress_fn(idx, 50)
            log_fn and log_fn(f"detected ADB device: {serial}")
            serials[idx] = serial

            # Wait for device to be fully ready
            log_fn and log_fn(f"waiting for {serial} to finish booting...")
            for attempt in range(30):
                if adb.is_device_ready(serial):
                    log_fn and log_fn(f"{serial} is ready")
                    break
                time.sleep(1)

            progress_fn and progress_fn(idx, 100)
            log_fn and log_fn(f"instance {idx} started successfully, serial {serial}")
            if stagger and delay > 0:
                time.sleep(delay)
        return serials

    def _do_stop(
        self,
        indices: List[int],
        log_fn: Callable[[str], None] | None = None,
        progress_fn=None,
    ) -> Dict[int, str]:
        cfg = self.get_config_fn()
        ctrl = LDPlayerController(cfg.get("dnconsole_path", ""), log_fn or (lambda m: None))
        results: Dict[int, str] = {}
        for idx in indices:
            ok = ctrl.stop_instance(idx)
            results[idx] = "stopped" if ok else "failed"
            progress_fn and progress_fn(idx, 0)
        return results

    def _do_reconnect(
        self,
        log_fn: Callable[[str], None] | None = None,
        progress_fn=None,
    ) -> Dict[str, str]:
        _log = log_fn or (lambda m: None)
        cfg = self.get_config_fn()
        adb = ADBManager(cfg.get("adb_path", ""), _log)

        # Step 1: list all currently visible devices
        devices = adb.list_devices()
        _log(f"Found {len(devices)} device(s): {devices}")

        if not devices:
            _log("No devices found. Nothing to reconnect.")
            return {}

        # Step 2: reconnect each device
        results: Dict[str, str] = {}
        for serial in devices:
            _log(f"Reconnecting {serial}...")
            ok = adb.connect_host(serial)
            results[serial] = "connected" if ok else "failed"
            _log(f"{serial}: {'connected' if ok else 'failed'}")

        _log(f"Reconnect complete. {sum(1 for v in results.values() if v == 'connected')}/{len(results)} succeeded.")
        return results