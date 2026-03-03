import json
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
    QFileDialog,
    QRadioButton,
    QButtonGroup,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
)

from src.core.task_runner import TaskRunner
from src.core.macro_engine import MacroEngine
from src.core.adb_manager import ADBManager


class MacroRunnerPage(QWidget):
    """Page for selecting and running macros on chosen instances."""

    def __init__(
        self,
        log_fn=None,
        get_state_fn=None,
        get_adb_manager_fn=None,
    ):
        super().__init__()
        self.log_fn = log_fn
        self.get_state_fn = get_state_fn
        self.get_adb_manager_fn = get_adb_manager_fn
        self._is_closing = False

        # state for running
        self.macro_path: str | None = None
        self.stop_event: Event | None = None
        self._row_for_index: Dict[int, int] = {}

        self.task_runner = TaskRunner()
        self.task_runner.on_log.connect(self.log)
        self.task_runner.on_error.connect(self._on_task_error)
        self.task_runner.on_progress.connect(self._on_task_progress)
        self.task_runner.on_done.connect(self._on_task_done)

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # macro file picker
        file_row = QHBoxLayout()
        self.file_label = QLabel("<no file selected>")
        self.file_btn = QPushButton("Browse...")
        self.file_btn.clicked.connect(self._select_macro_file)
        file_row.addWidget(self.file_label)
        file_row.addWidget(self.file_btn)
        layout.addLayout(file_row)

        # option controls
        opt_row = QHBoxLayout()
        self.parallel_radio = QRadioButton("Parallel")
        self.stagger_radio = QRadioButton("Staggered")
        self.parallel_radio.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.parallel_radio)
        group.addButton(self.stagger_radio)
        opt_row.addWidget(self.parallel_radio)
        opt_row.addWidget(self.stagger_radio)

        opt_row.addWidget(QLabel("delay"))
        self.stagger_spin = QSpinBox()
        self.stagger_spin.setRange(0, 60)
        self.stagger_spin.setValue(1)
        opt_row.addWidget(self.stagger_spin)

        opt_row.addWidget(QLabel("repeat"))
        self.repeat_spin = QSpinBox()
        self.repeat_spin.setRange(1, 1000)
        self.repeat_spin.setValue(1)
        opt_row.addWidget(self.repeat_spin)

        opt_row.addWidget(QLabel("pixel jitter"))
        self.pixel_spin = QSpinBox()
        self.pixel_spin.setRange(0, 50)
        opt_row.addWidget(self.pixel_spin)

        opt_row.addWidget(QLabel("delay jitter"))
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 1000)
        opt_row.addWidget(self.delay_spin)

        layout.addLayout(opt_row)

        # run/stop buttons
        btn_row = QHBoxLayout()
        self.run_btn = QPushButton("Run")
        self.run_btn.clicked.connect(self._on_run)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)
        btn_row.addWidget(self.run_btn)
        btn_row.addWidget(self.stop_btn)
        layout.addLayout(btn_row)

        # progress table
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Index", "Name", "Progress", "Status"])
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

        self.setLayout(layout)

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

    # ------------------------------------------------------------------
    # file selection
    # ------------------------------------------------------------------
    def _select_macro_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select Macro File", "", "JSON Files (*.json)")
        if path:
            self.macro_path = path
            self.file_label.setText(path)

    # ------------------------------------------------------------------
    # run control
    # ------------------------------------------------------------------
    def _on_run(self) -> None:
        if self._is_closing:
            return
        if not self.macro_path:
            self.log("Please choose a macro file first.")
            return

        try:
            macro = MacroEngine().load_macro(self.macro_path)
        except Exception as exc:  # pragma: no cover - let validation catch
            self.log(f"failed loading macro: {exc}")
            return

        ok, msg = MacroEngine().validate_macro(macro)
        if not ok:
            self.log(f"macro invalid: {msg}")
            return

        state = self.get_state_fn()
        instances = state.get_selected_instances()
        if not instances:
            self.log("No instances selected for macro run.")
            return

        # prepare UI
        self._populate_progress_table(instances)
        self.stop_event = Event()
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

        # gather options
        parallel = self.parallel_radio.isChecked()
        stagger_delay = self.stagger_spin.value()
        repeat = self.repeat_spin.value()
        pix = self.pixel_spin.value()
        djit = self.delay_spin.value()

        # dispatch worker
        self.task_runner.run(
            self._macro_worker,
            instances,
            macro,
            parallel,
            stagger_delay,
            repeat,
            pix,
            djit,
            self.stop_event,
        )

    def _on_stop(self) -> None:
        if self.stop_event:
            self.stop_event.set()
            self.log("Stopping macro execution...")
        self.stop_btn.setEnabled(False)

    # ------------------------------------------------------------------
    # table helpers
    # ------------------------------------------------------------------
    def _populate_progress_table(self, instances: List[Any]) -> None:
        self.table.setRowCount(0)
        self._row_for_index.clear()
        for inst in instances:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(str(inst.index)))
            self.table.setItem(row, 1, QTableWidgetItem(inst.name))
            self.table.setItem(row, 2, QTableWidgetItem("0%"))
            self.table.setItem(row, 3, QTableWidgetItem(""))
            self._row_for_index[inst.index] = row

    # ------------------------------------------------------------------
    # background worker
    # ------------------------------------------------------------------
    def _macro_worker(
        self,
        instances: List[Any],
        macro: Dict[str, Any],
        parallel: bool,
        stagger_delay: int,
        repeat: int,
        pixel_jitter: int,
        delay_jitter: int,
        stop_event: Event,
        log_fn=None,
        progress_fn=None,
    ) -> Dict[int, Any]:
        results: Dict[int, Any] = {}
        try:
            adb: ADBManager = self.get_adb_manager_fn()

            def run_single(inst):
                engine = MacroEngine(
                    pixel_jitter=pixel_jitter,
                    delay_jitter_ms=delay_jitter,
                    log_fn=log_fn or (lambda m: None),
                )
                final_res = {"success": True, "errors": []}
                for _ in range(repeat):
                    if stop_event.is_set():
                        final_res["success"] = False
                        final_res["errors"].append("stopped")
                        break
                    res = engine.run_macro_on_device(
                        adb,
                        inst.adb_serial,
                        macro,
                        stop_event,
                        progress_fn,
                        inst.index,
                    )
                    if not res.get("success"):
                        final_res["success"] = False
                        final_res["errors"].extend(res.get("errors", []))
                results[inst.index] = final_res

            if parallel:
                threads: List[threading.Thread] = []
                for inst in instances:
                    if stop_event.is_set():
                        break
                    t = threading.Thread(target=run_single, args=(inst,))
                    t.start()
                    threads.append(t)
                for t in threads:
                    t.join()
            else:
                for inst in instances:
                    if stop_event.is_set():
                        break
                    run_single(inst)
                    if stagger_delay:
                        time.sleep(stagger_delay)
        except Exception as exc:  # pragma: no cover - defensive
            log_fn and log_fn(f"Macro worker failed: {exc}")

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
        if percent >= 100:
            self.table.setItem(row, 3, QTableWidgetItem("running"))

    def _on_task_done(self, result: Any) -> None:
        if self._is_closing:
            return
        # worker returned results map
        if isinstance(result, dict):
            for idx, info in result.items():
                row = self._row_for_index.get(idx)
                if row is not None:
                    status = "success" if info.get("success") else "failed"
                    self.table.setItem(row, 3, QTableWidgetItem(status))
            self.log("Macro execution complete.")
        # reset buttons
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
