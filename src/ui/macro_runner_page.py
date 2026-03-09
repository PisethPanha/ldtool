import threading
import time
from threading import Event
from typing import Any, Dict, List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QGroupBox,
)

from src.core.task_runner import TaskRunner
from src.core.ldplayer_macro_runner import LDPlayerMacroRunner
from src.core.desktop_macro_runner import (
    DesktopClickCalibration,
    DesktopMacroRunner,
)

# Mode constants
MODE_CLI = "CLI playback"
MODE_DESKTOP = "Desktop click fallback"


class MacroRunnerPage(QWidget):
    """Page for running LDPlayer .record macros on selected instances."""

    def __init__(
        self,
        log_fn=None,
        get_state_fn=None,
        get_adb_manager_fn=None,
        get_config_fn=None,
    ):
        super().__init__()
        self.log_fn = log_fn
        self.get_state_fn = get_state_fn
        self.get_adb_manager_fn = get_adb_manager_fn
        self.get_config_fn = get_config_fn
        self._is_closing = False

        # running state
        self.stop_event: Event | None = None
        self._row_for_index: Dict[int, int] = {}
        self._pause_events: Dict[int, Event] = {}
        self._macro_supported: bool | None = None  # None = not checked yet

        self.task_runner = TaskRunner()
        self.task_runner.on_log.connect(self.log)
        self.task_runner.on_error.connect(self._on_task_error)
        self.task_runner.on_progress.connect(self._on_task_progress)
        self.task_runner.on_done.connect(self._on_task_done)

        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        # Compact configuration section
        config_group = QGroupBox("Macro Configuration")
        config_layout = QVBoxLayout(config_group)
        config_layout.setContentsMargins(10, 10, 10, 10)
        config_layout.setSpacing(6)

        # Mode selector row
        mode_row = QHBoxLayout()
        mode_row.setSpacing(6)
        mode_label = QLabel("Mode:")
        mode_label.setStyleSheet("padding: 10px; border-radius: 10px; background-color: #2a2c3e;")
        mode_row.addWidget(mode_label)
        self.mode_combo = QComboBox()
        self.mode_combo.setFixedHeight(30)
        self.mode_combo.addItems([MODE_CLI, MODE_DESKTOP])
        self.mode_combo.setToolTip(
            "CLI playback uses dnconsole operaterecord.\n"
            "Desktop click fallback clicks LDPlayer's macro UI directly."
        )
        mode_row.addWidget(self.mode_combo, 1)
        mode_row.addStretch(2)
        config_layout.addLayout(mode_row)

        # Macro selector row
        file_row = QHBoxLayout()
        file_row.setSpacing(6)
        macro_label = QLabel("Macro:")
        macro_label.setStyleSheet("padding: 10px; border-radius: 10px; background-color: #2a2c3e;")
        file_row.addWidget(macro_label)
        self.macro_combo = QComboBox()
        self.macro_combo.setFixedHeight(30)
        self.macro_combo.setMinimumWidth(200)
        file_row.addWidget(self.macro_combo, 1)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setFixedHeight(30)
        self.refresh_btn.setFixedWidth(80)
        self.refresh_btn.setToolTip("Reload .record list from LDPlayer")
        self.refresh_btn.clicked.connect(self._refresh_records)
        file_row.addWidget(self.refresh_btn)
        config_layout.addLayout(file_row)

        # Options row
        opt_row = QHBoxLayout()
        opt_row.setSpacing(6)
        loops_label = QLabel("Loops:")
        loops_label.setStyleSheet("padding: 10px; border-radius: 10px; background-color: #2a2c3e;")
        opt_row.addWidget(loops_label)
        self.loop_spin = QSpinBox()
        self.loop_spin.setFixedHeight(30)
        self.loop_spin.setFixedWidth(80)
        self.loop_spin.setRange(1, 10000)
        self.loop_spin.setValue(1)
        opt_row.addWidget(self.loop_spin)
        
        delay_label = QLabel("Delay between loops:")
        delay_label.setStyleSheet("padding: 10px; border-radius: 10px; background-color: #2a2c3e;")
        opt_row.addWidget(delay_label)
        self.delay_spin = QSpinBox()
        self.delay_spin.setFixedHeight(30)
        self.delay_spin.setFixedWidth(80)
        self.delay_spin.setRange(0, 300)
        self.delay_spin.setValue(2)
        opt_row.addWidget(self.delay_spin)
        opt_row.addWidget(QLabel("sec"))
        opt_row.addStretch()
        config_layout.addLayout(opt_row)

        # Run / stop buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.run_btn = QPushButton("Run on Selected Instances")
        self.run_btn.setFixedHeight(32)
        self.run_btn.clicked.connect(self._on_run)
        self.stop_btn = QPushButton("Stop All")
        self.stop_btn.setFixedHeight(32)
        self.stop_btn.setFixedWidth(100)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)
        btn_row.addWidget(self.run_btn)
        btn_row.addWidget(self.stop_btn)
        btn_row.addStretch()
        config_layout.addLayout(btn_row)

        layout.addWidget(config_group)

        # Progress table section
        progress_group = QGroupBox("Execution Progress")
        progress_layout = QVBoxLayout(progress_group)
        progress_layout.setContentsMargins(10, 10, 10, 10)
        progress_layout.setSpacing(6)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Index", "Name", "Progress", "Status", "Action"])
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setMinimumHeight(100)
        progress_layout.addWidget(self.table)

        layout.addWidget(progress_group, 1)  # Give progress area stretch factor

        self.setLayout(layout)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def log(self, msg: str) -> None:
        if callable(self.log_fn):
            self.log_fn(msg)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._is_closing = True
        if self.stop_event:
            self.stop_event.set()
        super().closeEvent(event)

    def _on_task_error(self, message: str) -> None:
        if self._is_closing:
            return
        self.log(f"Macro task error: {message}")

    def _get_runner(self) -> LDPlayerMacroRunner | None:
        cfg = self.get_config_fn() if callable(self.get_config_fn) else {}
        dn = cfg.get("dnconsole_path", "")
        if not dn:
            self.log("dnconsole path not configured")
            return None
        return LDPlayerMacroRunner(dn, self.log)

    # ------------------------------------------------------------------
    # record list
    # ------------------------------------------------------------------
    def _current_mode(self) -> str:
        return self.mode_combo.currentText()

    def _refresh_records(self) -> None:
        """Reload .record list from the first running instance."""
        runner = self._get_runner()
        if runner is None:
            return

        # Verify macro CLI support
        supported, reason = runner.verify_macro_support()
        self._macro_supported = supported
        if not supported:
            self.log(reason)
            # Auto-switch to desktop click mode
            self.mode_combo.setCurrentText(MODE_DESKTOP)
            self.log("Auto-switched to Desktop click fallback mode.")

        # Always keep Run enabled — desktop fallback can still work
        self.run_btn.setEnabled(True)
        self.run_btn.setToolTip("")

        state = self.get_state_fn()
        instances = state.get_selected_instances()
        # pick any running instance to query records
        idx = 0
        for inst in (instances or []):
            if inst.is_running:
                idx = inst.index
                break

        records = runner.list_records(idx)
        self.macro_combo.clear()
        if not records:
            self.log("No .record files found on instance")
            return
        for rec in records:
            fname = rec.get("file", "")
            if fname:
                self.macro_combo.addItem(fname, fname)
        self.log(f"Loaded {len(records)} record(s)")

    # ------------------------------------------------------------------
    # run / stop
    # ------------------------------------------------------------------
    def _on_run(self) -> None:
        if self._is_closing:
            return

        mode = self._current_mode()

        if mode == MODE_CLI and self._macro_supported is False:
            self.log(
                "CLI playback is not available. Switching to Desktop click fallback."
            )
            self.mode_combo.setCurrentText(MODE_DESKTOP)
            mode = MODE_DESKTOP

        record_file = self.macro_combo.currentData() or self.macro_combo.currentText()
        if not record_file or record_file == "— unsupported —":
            self.log("Please select a .record macro first (click Refresh).")
            return

        state = self.get_state_fn()
        instances = state.get_selected_instances()
        if not instances:
            self.log("No instances selected for macro run.")
            return

        # validate: only running instances
        running = [i for i in instances if i.is_running]
        if not running:
            self.log("None of the selected instances are running.")
            return

        # prepare UI
        self._populate_progress_table(running)
        self.stop_event = Event()
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

        loops = self.loop_spin.value()
        delay = self.delay_spin.value()

        self.log(f"[{mode}] Starting macro '{record_file}' — {loops} loop(s), {delay}s delay")

        if mode == MODE_DESKTOP:
            cfg = self.get_config_fn() if callable(self.get_config_fn) else {}
            cal = DesktopClickCalibration.from_config(cfg)
            desktop_runner = DesktopMacroRunner(cal, self.log)
            self.task_runner.run(
                self._macro_worker,
                desktop_runner,  # same run_macro_loop interface
                running,
                record_file,
                loops,
                delay,
                self.stop_event,
            )
        else:
            runner = self._get_runner()
            if runner is None:
                self.run_btn.setEnabled(True)
                self.stop_btn.setEnabled(False)
                return
            self.task_runner.run(
                self._macro_worker,
                runner,
                running,
                record_file,
                loops,
                delay,
                self.stop_event,
            )

    def _on_stop(self) -> None:
        if self.stop_event:
            self.stop_event.set()
            self.log("Stopping macro execution…")
        self.stop_btn.setEnabled(False)
        # For CLI mode, also tell each running instance to stop playback
        if self._current_mode() == MODE_CLI:
            runner = self._get_runner()
            if runner:
                for idx in self._row_for_index:
                    row = self._row_for_index[idx]
                    name_item = self.table.item(row, 1)
                    label = name_item.text() if name_item else str(idx)
                    runner.stop_playback(idx, label=label)

    # ------------------------------------------------------------------
    # table helpers
    # ------------------------------------------------------------------
    def _populate_progress_table(self, instances: List[Any]) -> None:
        self.table.setRowCount(0)
        self._row_for_index.clear()
        self._pause_events.clear()
        for inst in instances:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(str(inst.index)))
            self.table.setItem(row, 1, QTableWidgetItem(inst.name))
            self.table.setItem(row, 2, QTableWidgetItem("0%"))
            self.table.setItem(row, 3, QTableWidgetItem("pending"))
            # per-instance pause event (set = running)
            evt = Event()
            evt.set()
            self._pause_events[inst.index] = evt
            btn = QPushButton("Pause")
            btn.setFixedWidth(60)
            btn.clicked.connect(lambda checked=False, idx=inst.index: self._toggle_pause(idx))
            self.table.setCellWidget(row, 4, btn)
            self._row_for_index[inst.index] = row

    # ------------------------------------------------------------------
    # per-instance pause toggle
    # ------------------------------------------------------------------
    def _toggle_pause(self, instance_index: int) -> None:
        evt = self._pause_events.get(instance_index)
        if evt is None:
            return
        row = self._row_for_index.get(instance_index)
        btn = self.table.cellWidget(row, 4) if row is not None else None
        if evt.is_set():
            evt.clear()
            self.log(f"Paused macro on instance {instance_index}")
            if btn:
                btn.setText("Resume")
            if row is not None:
                self.table.setItem(row, 3, QTableWidgetItem("paused"))
        else:
            evt.set()
            self.log(f"Resumed macro on instance {instance_index}")
            if btn:
                btn.setText("Pause")
            if row is not None:
                self.table.setItem(row, 3, QTableWidgetItem("running"))

    # ------------------------------------------------------------------
    # background worker
    # ------------------------------------------------------------------
    def _macro_worker(
        self,
        runner: Any,  # LDPlayerMacroRunner or DesktopMacroRunner
        instances: List[Any],
        record_file: str,
        loops: int,
        delay: int,
        stop_event: Event,
        log_fn=None,
        progress_fn=None,
    ) -> Dict[int, Any]:
        results: Dict[int, Any] = {}

        def run_single(inst):
            pause_evt = self._pause_events.get(inst.index)
            results[inst.index] = runner.run_macro_loop(
                index=inst.index,
                instance_name=inst.name,
                filename=record_file,
                loops=loops,
                delay_between_loops=delay,
                stop_event=stop_event,
                pause_event=pause_evt,
                log_fn=log_fn,
                progress_fn=progress_fn,
            )

        # always parallel — each instance independently
        threads: List[threading.Thread] = []
        for inst in instances:
            if stop_event.is_set():
                break
            t = threading.Thread(target=run_single, args=(inst,))
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

        return results

    # ------------------------------------------------------------------
    # signal handlers
    # ------------------------------------------------------------------
    def _on_task_progress(self, instance_id: int, percent: int) -> None:
        if self._is_closing:
            return
        row = self._row_for_index.get(instance_id)
        if row is None:
            return
        self.table.setItem(row, 2, QTableWidgetItem(f"{percent}%"))
        evt = self._pause_events.get(instance_id)
        paused = evt is not None and not evt.is_set()
        self.table.setItem(row, 3, QTableWidgetItem("paused" if paused else "running"))

    def _on_task_done(self, result: Any) -> None:
        if self._is_closing:
            return
        if isinstance(result, dict):
            for idx, info in result.items():
                row = self._row_for_index.get(idx)
                if row is not None:
                    status = info.get("status", "failed")
                    self.table.setItem(row, 3, QTableWidgetItem(status))
                    btn = self.table.cellWidget(row, 4)
                    if btn:
                        btn.setEnabled(False)
            self.log("Macro execution complete.")
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
