from __future__ import annotations

from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Any, Callable
import time
import uuid

from PySide6.QtCore import Qt, QDateTime, QThread
from PySide6.QtWidgets import (
	QWidget,
	QVBoxLayout,
	QHBoxLayout,
	QLabel,
	QPushButton,
	QLineEdit,
	QFileDialog,
	QListWidget,
	QListWidgetItem,
	QRadioButton,
	QButtonGroup,
	QDateTimeEdit,
	QSpinBox,
	QTableWidget,
	QTableWidgetItem,
	QPlainTextEdit,
	QMessageBox,
)

from src.core.media_manager import (
	caption_from_filename,
	move_to_failed,
	move_to_posted,
	scan_media,
)
from src.core.caption_mapper import CaptionMapper
from src.core.reel_jobs import ReelJob, ReelJobQueue
from src.core.reel_poster import ReelPoster, ADBKeyboardRequest
from src.core.task_runner import TaskRunner
from src.ui.multi_reel_poster_worker import MultiReelPosterWorker
from src.ui.process_queue_manager import ProcessQueueManager


class ReelsPosterPage(QWidget):
	"""UI for building and running reel posting jobs."""

	COL_MEDIA = 0
	COL_INSTANCE = 1
	COL_STATUS = 2
	COL_ATTEMPTS = 3
	COL_ERROR = 4

	def __init__(
		self,
		log_fn: Callable[[str], None],
		get_config_fn: Callable[[], dict[str, Any]],
		get_state_fn: Callable[[], Any],
		get_adb_manager_fn: Callable[[], Any],
		adbkeyboard_install_bus: Any = None,
	):
		super().__init__()
		self.log_fn = log_fn
		self.get_config_fn = get_config_fn
		self.get_state_fn = get_state_fn
		self.get_adb_manager_fn = get_adb_manager_fn
		self.adbkeyboard_install_bus = adbkeyboard_install_bus

		self._is_closing = False
		self.stop_event: Event = Event()
		self._media_paths: list[str] = []
		self._job_row_map: dict[str, int] = {}
		self._caption_mapper = CaptionMapper("./caption_mapping.json")

		self.task_runner = TaskRunner()
		self.task_runner.on_log.connect(self._on_worker_log)
		self.task_runner.on_error.connect(self._on_worker_error)
		self.task_runner.on_done.connect(self._on_worker_done)
		
		# Multi-media worker thread support - now per-process (parallel execution)
		self._workers_by_pid: dict[str, MultiReelPosterWorker] = {}  # process_id -> worker
		self._threads_by_pid: dict[str, QThread] = {}  # process_id -> thread
		self._running_serials: set[str] = set()  # Track which instance serials are active

		# Process queue manager
		self.queue_manager = ProcessQueueManager()
		self.queue_manager.process_added.connect(self._on_queue_process_added)
		self.queue_manager.status_changed.connect(self._on_queue_status_changed)
		self.queue_manager.process_started.connect(self._on_queue_process_started)
		self.queue_manager.process_completed.connect(self._on_queue_process_completed)
		self.queue_manager.process_failed.connect(self._on_queue_process_failed)
		self.queue_manager.log_message.connect(self._on_queue_log_message)

		self._build_ui()

	def _create_adbkeyboard_request(self, serial: str) -> ADBKeyboardRequest:
		"""Create and emit an ADBKeyboard installation request.
		
		Called from ReelPoster worker thread.
		Creates the request and emits signal to UI thread for handling.
		Returns the request object which the worker thread waits on.
		"""
		request = ADBKeyboardRequest(serial)
		
		if self.adbkeyboard_install_bus:
			self.log_fn(f"[{serial}] Emitting ADBKeyboard install request signal to UI thread...")
			# Emit signal - will be handled by MainWindow in UI thread via Qt.QueuedConnection
			self.adbkeyboard_install_bus.install_requested.emit(request)
		else:
			self.log_fn(f"[{serial}] ✗ No ADBKeyboard install bus available")
			# Set failure immediately if no bus
			request.set_result(False, "No install bus configured")
		
		return request

	@staticmethod
	def _is_adbkeyboard_setup_error(error: str | None) -> bool:
		"""Return True when failure is caused by ADBKeyboard setup/install issues."""
		if not error:
			return False
		err = str(error).lower()
		return (
			"adbkeyboard required" in err
			or "adbkeyboard installation failed" in err
			or "failed to show adbkeyboard dialog" in err
		)

	def _build_ui(self) -> None:
		layout = QVBoxLayout(self)

		# Media folder picker
		folder_row = QHBoxLayout()
		folder_row.addWidget(QLabel("Media folder:"))
		self.folder_input = QLineEdit()
		self.browse_btn = QPushButton("Browse")
		self.browse_btn.clicked.connect(self._pick_folder)
		self.scan_btn = QPushButton("Scan")
		self.scan_btn.clicked.connect(self._scan_media)
		folder_row.addWidget(self.folder_input)
		folder_row.addWidget(self.browse_btn)
		folder_row.addWidget(self.scan_btn)
		layout.addLayout(folder_row)

		# Media list + caption preview
		media_row = QHBoxLayout()
		self.media_list = QListWidget()
		self.media_list.currentItemChanged.connect(self._update_caption_preview)
		media_row.addWidget(self.media_list, 2)

		preview_col = QVBoxLayout()
		preview_col.addWidget(QLabel("Caption preview:"))
		self.caption_preview = QPlainTextEdit()
		self.caption_preview.setReadOnly(True)
		preview_col.addWidget(self.caption_preview)
		media_row.addLayout(preview_col, 1)
		layout.addLayout(media_row)

		# Page loading row
		page_row = QHBoxLayout()
		page_row.addWidget(QLabel("Target page:"))
		self.page_input = QLineEdit()
		self.page_input.setPlaceholderText("Enter Facebook page name")
		page_row.addWidget(self.page_input)
		layout.addLayout(page_row)

		# Mode / schedule / retry / concurrency
		opts_row = QHBoxLayout()
		self.post_now_radio = QRadioButton("Post now")
		self.schedule_radio = QRadioButton("Schedule")
		self.post_now_radio.setChecked(True)
		mode_group = QButtonGroup(self)
		mode_group.addButton(self.post_now_radio)
		mode_group.addButton(self.schedule_radio)
		self.post_now_radio.toggled.connect(self._on_mode_changed)
		opts_row.addWidget(self.post_now_radio)
		opts_row.addWidget(self.schedule_radio)

		self.schedule_dt = QDateTimeEdit(QDateTime.currentDateTime())
		self.schedule_dt.setCalendarPopup(True)
		self.schedule_dt.setEnabled(False)
		opts_row.addWidget(self.schedule_dt)

		opts_row.addWidget(QLabel("Retry count:"))
		self.retry_spin = QSpinBox()
		self.retry_spin.setRange(1, 10)
		self.retry_spin.setValue(2)
		opts_row.addWidget(self.retry_spin)

		opts_row.addWidget(QLabel("Concurrency:"))
		self.concurrent_spin = QSpinBox()
		self.concurrent_spin.setRange(1, 32)
		self.concurrent_spin.setValue(2)
		opts_row.addWidget(self.concurrent_spin)
		layout.addLayout(opts_row)

		# Actions
		act_row = QHBoxLayout()
		self.start_btn = QPushButton("Start")
		self.start_btn.clicked.connect(self._start)
		self.stop_btn = QPushButton("Stop")
		self.stop_btn.clicked.connect(self._stop)
		self.test_btn = QPushButton("Test")
		self.test_btn.clicked.connect(self._test)
		self.stop_btn.setEnabled(False)
		act_row.addWidget(self.start_btn)
		act_row.addWidget(self.stop_btn)
		act_row.addWidget(self.test_btn)
		layout.addLayout(act_row)

		# Status table
		self.table = QTableWidget(0, 5)
		self.table.setHorizontalHeaderLabels(["Media", "Instance", "Status", "Attempts", "Error"])
		self.table.horizontalHeader().setStretchLastSection(True)
		layout.addWidget(self.table)

		# Process queue table
		queue_label_row = QHBoxLayout()
		queue_label_row.addWidget(QLabel("Process Queue:"))
		self.clear_completed_btn = QPushButton("Clear Completed")
		self.clear_completed_btn.clicked.connect(self._clear_completed_processes)
		queue_label_row.addWidget(self.clear_completed_btn)
		queue_label_row.addStretch()
		layout.addLayout(queue_label_row)
		
		self.queue_table = QTableWidget(0, 6)
		self.queue_table.setHorizontalHeaderLabels(["Instance", "Page", "Media Count", "Mode", "Schedule Time", "Status"])
		self.queue_table.horizontalHeader().setStretchLastSection(True)
		layout.addWidget(self.queue_table)

		# Per instance log panel
		layout.addWidget(QLabel("Per-instance log"))
		self.instance_log = QPlainTextEdit()
		self.instance_log.setReadOnly(True)
		layout.addWidget(self.instance_log)

	def closeEvent(self, event) -> None:  # type: ignore[override]
		self._is_closing = True
		self.stop_event.set()
		
		# Stop queue manager timer
		if hasattr(self, 'queue_manager'):
			self.queue_manager.scheduler_timer.stop()
		
		# Clean up all active workers and threads
		for process_id, worker in list(self._workers_by_pid.items()):
			if worker is not None:
				worker.cancel()
		
		for process_id, thread in list(self._threads_by_pid.items()):
			if thread is not None and thread.isRunning():
				thread.quit()
				thread.wait()
		
		super().closeEvent(event)

	# ------------------------------------------------------------------
	# UI interactions
	# ------------------------------------------------------------------
	def _pick_folder(self) -> None:
		folder = QFileDialog.getExistingDirectory(self, "Select media folder")
		if folder:
			self.folder_input.setText(folder)
			self._scan_media()

	def _scan_media(self) -> None:
		folder = self.folder_input.text().strip()
		self.media_list.clear()
		self._media_paths = scan_media(folder)

		# Clear previous caption mappings and store new ones
		self._caption_mapper.clear()
		batch_entries: list[tuple[str, str, int]] = []
		
		for label, path in enumerate(self._media_paths, start=1):
			item = QListWidgetItem(Path(path).name)
			item.setData(Qt.UserRole, path)
			item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
			item.setCheckState(Qt.Checked)
			self.media_list.addItem(item)
			
			# Extract caption and store mapping
			caption = caption_from_filename(path)
			batch_entries.append((path, caption, label))
		
		self._caption_mapper.add_batch(batch_entries)
		self._log(f"Loaded {len(self._media_paths)} media file(s) with caption mappings.")

	def _update_caption_preview(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
		del previous
		if current is None:
			self.caption_preview.setPlainText("")
			return
		path = current.data(Qt.UserRole)
		self.caption_preview.setPlainText(caption_from_filename(path))

	def _on_mode_changed(self) -> None:
		self.schedule_dt.setEnabled(self.schedule_radio.isChecked())

	def _show_reject_dialog(self, reason: str) -> None:
		"""Show a robust rejection dialog and fall back to log if UI dialog fails."""
		message = (reason or "This process cannot be started due to queue validation rules.").strip()
		try:
			box = QMessageBox(self)
			box.setIcon(QMessageBox.Warning)
			box.setWindowTitle("Process Rejected")
			box.setText("Unable to create process.")
			box.setInformativeText(message)
			box.exec()
		except Exception as exc:
			self._log(f"Failed to show reject dialog: {exc}")
			self._log(f"Reject reason: {message}")

	def _start(self) -> None:
		payload = self._build_run_payload(test_mode=False)
		if payload is None:
			return
		
		# Use queue manager for multi-media posts
		jobs = payload["jobs"]
		instances = payload["instances"]
		
		if len(jobs) > 1:
			# Multi-media: add to queue with validation
			selected_instance = instances[0]
			instance_serial = getattr(selected_instance, "adb_serial", "")
			instance_name = getattr(selected_instance, "name", instance_serial or "Unknown")
			post_mode = jobs[0].post_mode if jobs else "NOW"
			scheduled_at = jobs[0].scheduled_at if jobs else None
			
			# Validate against queue rules
			try:
				is_valid, error_msg = self.queue_manager.validate_new_process(
					instance_serial,
					instance_name,
					instances,
					post_mode,
					scheduled_at,
				)
			except Exception as exc:
				self._show_reject_dialog(f"Validation failed: {exc}")
				return
			if not is_valid:
				self._show_reject_dialog(error_msg)
				return
			
			# Add to queue
			page_name = jobs[0].target_page if jobs else ""
			self.queue_manager.add_process(
				selected_instance,
				instance_serial,
				instance_name,
				page_name,
				jobs,
				post_mode,
				scheduled_at,
			)
			
			# Clear inputs (Rule 1)
			self._clear_inputs()
			
			self._log(f"Process added to queue: {len(jobs)} media for {len(instances)} instance(s)")
		else:
			# Single media: use existing direct flow
			self._start_worker(payload)

	def _test(self) -> None:
		payload = self._build_run_payload(test_mode=True)
		if payload is None:
			return
		self._start_worker(payload)

	def _stop(self) -> None:
		# Stop all active multi-media workers
		for process_id, worker in self._workers_by_pid.items():
			if worker is not None:
				worker.cancel()
		
		# Stop the parallel worker (if any)
		self.stop_event.set()
		self.stop_btn.setEnabled(False)
		self._log("Stop requested.")

	def _start_worker(self, payload: dict[str, Any]) -> None:
		"""Start a direct (non-queued) worker for single-media posting."""
		# This method is only called for single-media direct posting
		# Multi-media posts use the queue manager and parallel workers
		
		jobs = payload["jobs"]
		if len(jobs) != 1:
			self._log("Error: _start_worker called with non-single-media payload. Use queue instead.")
			return
		
		self.stop_event.clear()
		self.start_btn.setEnabled(False)
		self.test_btn.setEnabled(False)
		self.stop_btn.setEnabled(True)
		self.instance_log.clear()

		# Prepare table rows for jobs (main thread)
		self._job_row_map.clear()
		self.table.setRowCount(0)
		for row_idx, job in enumerate(payload["jobs"]):
			self.table.insertRow(row_idx)
			self.table.setItem(row_idx, self.COL_MEDIA, QTableWidgetItem(Path(job.media_path).name))
			self.table.setItem(row_idx, self.COL_INSTANCE, QTableWidgetItem(""))
			self.table.setItem(row_idx, self.COL_STATUS, QTableWidgetItem("QUEUED"))
			self.table.setItem(row_idx, self.COL_ATTEMPTS, QTableWidgetItem("0"))
			self.table.setItem(row_idx, self.COL_ERROR, QTableWidgetItem(""))
			self._job_row_map[job.id] = row_idx

		# Single media: use existing parallel flow
		self.task_runner.run(self._do_run_jobs, payload)

	def _start_multi_media_worker(self, process_id: str, instance_serial: str, jobs: list[ReelJob], instances: list[Any]) -> None:
		"""Start sequential multi-media posting worker for a queue process.
		
		Each process gets its own worker and thread, enabling parallel execution
		for different instances.
		"""
		# Create worker with process tracking
		worker = MultiReelPosterWorker(
			process_id=process_id,
			instance_serial=instance_serial,
			jobs=jobs,
			instances=instances,
			adb_manager=self.get_adb_manager_fn(),
			get_adbkeyboard_request_fn=self._create_adbkeyboard_request,
			log_fn=self.log_fn,
		)
		
		# Create thread
		thread = QThread()
		worker.moveToThread(thread)
		
		# Connect signals (include process_id in finished signal)
		thread.started.connect(worker.run)
		worker.log_message.connect(self._on_multi_worker_log)
		worker.progress.connect(self._on_multi_worker_progress)
		worker.finished.connect(self._on_multi_worker_finished)
		
		# Auto-cleanup: when thread finishes, delete worker and thread
		thread.finished.connect(worker.deleteLater)
		thread.finished.connect(thread.deleteLater)
		
		# Store references
		self._workers_by_pid[process_id] = worker
		self._threads_by_pid[process_id] = thread
		self._running_serials.add(instance_serial)
		
		# Start thread
		thread.start()
	
	def _on_multi_worker_log(self, message: str) -> None:
		"""Handle log message from multi-media worker."""
		if self._is_closing:
			return
		self._log(message)
		self.instance_log.appendPlainText(message)
	
	def _on_multi_worker_progress(self, idx: int, total: int, media_name: str, status: str) -> None:
		"""Handle progress update from multi-media worker."""
		if self._is_closing:
			return
		# Update table row for this media
		for row_idx in range(self.table.rowCount()):
			item = self.table.item(row_idx, self.COL_MEDIA)
			if item and item.text() == media_name:
				self.table.setItem(row_idx, self.COL_STATUS, QTableWidgetItem(status))
				break
	
	def _on_multi_worker_finished(self, process_id: str, instance_serial: str, result: dict[str, Any]) -> None:
		"""Handle completion of multi-media worker.
		
		Args:
			process_id: Queue process ID
			instance_serial: Target ADB serial
			result: Results dictionary from worker
		"""
		if self._is_closing:
			return
		
		# Update table with final results (for backward compatibility with single-media table)
		total = result.get("total", 0)
		success = result.get("success", 0)
		fail = result.get("fail", 0)
		cancelled = result.get("cancelled", False)
		results_list = result.get("results", [])
		
		for post_result in results_list:
			media_name = Path(post_result.media_path).name
			status = "SUCCESS" if post_result.success else "FAILED"
			error = post_result.error or ""
			
			# Find row for this media
			for row_idx in range(self.table.rowCount()):
				item = self.table.item(row_idx, self.COL_MEDIA)
				if item and item.text() == media_name:
					self.table.setItem(row_idx, self.COL_STATUS, QTableWidgetItem(status))
					self.table.setItem(row_idx, self.COL_ERROR, QTableWidgetItem(error))
					break
		
		# Log summary
		if cancelled:
			self._log(f"Process {process_id[:8]} cancelled by user.")
		else:
			self._log(f"Process {process_id[:8]} finished: success={success} fail={fail}")
		
		# Notify queue manager
		if cancelled:
			self.queue_manager.mark_process_failed(process_id, "Cancelled by user")
		else:
			self.queue_manager.mark_process_complete(process_id, success, fail)
		
		# Cleanup worker/thread for this process
		self._running_serials.discard(instance_serial)
		if process_id in self._workers_by_pid:
			del self._workers_by_pid[process_id]
		if process_id in self._threads_by_pid:
			thread = self._threads_by_pid[process_id]
			thread.quit()
			thread.wait()
			del self._threads_by_pid[process_id]
		
		# Re-enable buttons if no processes are running
		if not self._running_serials:
			self.start_btn.setEnabled(True)
			self.test_btn.setEnabled(True)
			self.stop_btn.setEnabled(False)

	# ------------------------------------------------------------------
	# Input clearing and queue management
	# ------------------------------------------------------------------
	def _clear_inputs(self) -> None:
		"""Clear inputs after successful process creation (Rule 1)."""
		self.folder_input.clear()
		self.media_list.clear()
		self._media_paths.clear()
		# Keep page input for convenience
	
	def _clear_completed_processes(self) -> None:
		"""Remove completed/failed processes from queue table."""
		rows_to_remove = []
		for row in range(self.queue_table.rowCount()):
			status_item = self.queue_table.item(row, 5)  # Status column
			if status_item and status_item.text() in ("Completed", "Failed"):
				rows_to_remove.append(row)
		
		# Remove in reverse order to avoid index shifting
		for row in reversed(rows_to_remove):
			self.queue_table.removeRow(row)
	
	# ------------------------------------------------------------------
	# Queue manager signal handlers
	# ------------------------------------------------------------------
	def _on_queue_process_added(self, process_info: Any) -> None:
		"""Handle process added to queue."""
		if self._is_closing:
			return
		
		# Add row to queue table
		row = self.queue_table.rowCount()
		self.queue_table.insertRow(row)
		
		instance_names = process_info.instance_name
		page = process_info.jobs[0].target_page if process_info.jobs else ""
		mode = "Now" if process_info.scheduled_at is None else "Scheduled"
		schedule_time = ""
		if process_info.scheduled_at:
			schedule_time = process_info.scheduled_at.strftime("%Y-%m-%d %H:%M")
		
		self.queue_table.setItem(row, 0, QTableWidgetItem(instance_names))
		self.queue_table.setItem(row, 1, QTableWidgetItem(page))
		self.queue_table.setItem(row, 2, QTableWidgetItem(str(len(process_info.jobs))))
		self.queue_table.setItem(row, 3, QTableWidgetItem(mode))
		self.queue_table.setItem(row, 4, QTableWidgetItem(schedule_time))
		self.queue_table.setItem(row, 5, QTableWidgetItem(process_info.status))
		
		# Store process_id in row
		self.queue_table.item(row, 0).setData(Qt.UserRole, process_info.process_id)
	
	def _on_queue_status_changed(self, process_id: str, new_status: str) -> None:
		"""Handle process status change."""
		if self._is_closing:
			return
		
		# Find row with this process_id
		for row in range(self.queue_table.rowCount()):
			item = self.queue_table.item(row, 0)
			if item and item.data(Qt.UserRole) == process_id:
				self.queue_table.setItem(row, 5, QTableWidgetItem(new_status))
				break
	
	def _on_queue_process_started(self, process_id: str) -> None:
		"""Handle process started from queue.
		
		Extracts process info and creates a worker for parallel execution.
		The queue manager guarantees the instance serial is free at this point.
		"""
		if self._is_closing:
			return
		
		process_info = self.queue_manager.processes.get(process_id)
		if not process_info:
			return
		
		# Start the worker for this process
		self._start_multi_media_worker(
			process_id,
			process_info.instance_serial,
			process_info.jobs,
			[process_info.instance]
		)
		
		# Keep Start enabled so user can queue more processes
		self.stop_btn.setEnabled(True)
		
	def _on_queue_process_completed(self, process_id: str, success_count: int, fail_count: int) -> None:
		"""Handle process completion from queue."""
		if self._is_closing:
			return
		self._log(f"Queue process {process_id[:8]} completed: {success_count} success, {fail_count} failed")
	
	def _on_queue_process_failed(self, process_id: str, error: str) -> None:
		"""Handle process failure from queue."""
		if self._is_closing:
			return
		self._log(f"Queue process {process_id[:8]} failed: {error}")
	
	def _on_queue_log_message(self, message: str) -> None:
		"""Handle log message from queue manager."""
		if self._is_closing:
			return
		self._log(message)

	def _build_run_payload(self, test_mode: bool) -> dict[str, Any] | None:
		selected_media: list[str] = []
		for i in range(self.media_list.count()):
			item = self.media_list.item(i)
			if item.checkState() == Qt.Checked:
				selected_media.append(item.data(Qt.UserRole))

		if not selected_media:
			self._log("No media selected.")
			return None

		selected_instances = [inst for inst in self.get_state_fn().get_selected_instances() if inst.adb_serial]
		if not selected_instances:
			self._log("No selected instance with ADB serial.")
			return None

		page = self.page_input.text().strip()
		if not page:
			self._log("Please enter target page name.")
			return None

		post_mode = "NOW" if self.post_now_radio.isChecked() else "SCHEDULED"
		scheduled_at: datetime | None = None
		if post_mode == "SCHEDULED":
			scheduled_at = self.schedule_dt.dateTime().toPython()

		if test_mode:
			selected_media = selected_media[:1]
			selected_instances = selected_instances[:1]

		jobs: list[ReelJob] = []
		for media_path in selected_media:
			# Get caption and label from mapper
			caption = self._caption_mapper.get_caption(media_path)
			label = self._caption_mapper.get_label(media_path)
			
			# Fallback to filename if not in mapper
			if caption is None:
				caption = caption_from_filename(media_path)
			if label is None:
				label = 0
			
			jobs.append(
				ReelJob(
					id=str(uuid.uuid4()),
					media_path=media_path,
					caption=caption,
					target_page=page,
					post_mode=post_mode,
					scheduled_at=scheduled_at,
					status="PENDING",
					attempts=0,
					max_attempts=self.retry_spin.value(),
					last_error=None,
					label=label,
				)
			)
		
		# Sort jobs by label to ensure ordered posting
		jobs.sort(key=lambda j: j.label)

		return {
			"jobs": jobs,
			"instances": selected_instances,
			"max_workers": self.concurrent_spin.value(),
			"test_mode": test_mode,
		}

	# ------------------------------------------------------------------
	# Worker logic
	# ------------------------------------------------------------------
	def _do_run_jobs(self, payload: dict[str, Any], log_fn=None, progress_fn=None) -> dict[str, Any]:
		del progress_fn
		adb = self.get_adb_manager_fn()
		# Use skip_push_media=True to use File Manager flow (no PC path validation)
		# fallback_push_if_missing=False: if media not found on emulator, skip (don't fail or push from PC)
		poster = ReelPoster(
			adb,
			log_fn or (lambda m: None),
			skip_push_media=True,
			fallback_push_if_missing=False,
			get_adbkeyboard_request_fn=self._create_adbkeyboard_request,
		)
		queue = ReelJobQueue(persist_path=None)

		jobs: list[ReelJob] = payload["jobs"]
		instances = payload["instances"]
		max_workers = max(1, int(payload["max_workers"]))

		queue.add_jobs(jobs)
		pending_count = len(jobs)
		in_flight: dict[Future, tuple[ReelJob, Any, str]] = {}
		available_instances = deque(instances)

		rows: dict[str, dict[str, str]] = {
			job.id: {
				"instance": "",
				"status": "QUEUED",
				"attempts": str(job.attempts),
				"error": "",
			}
			for job in jobs
		}

		def run_one(job: ReelJob, instance: Any, device_media_path: str) -> tuple[bool, str | None]:
			return poster.run(instance.adb_serial, job, device_media_path)

		with ThreadPoolExecutor(max_workers=max_workers) as executor:
			while (pending_count > 0 or in_flight) and not self.stop_event.is_set():
				# fill worker slots
				while pending_count > 0 and len(in_flight) < max_workers and not self.stop_event.is_set():
					ready = queue.pop_next_ready(datetime.now())
					if ready is None:
						time.sleep(0.4)
						continue

					if not available_instances:
						time.sleep(0.2)
						continue

					instance = available_instances[0]
					available_instances.rotate(-1)

					ready.status = "RUNNING"
					ready.attempts += 1
					rows[ready.id]["instance"] = getattr(instance, "name", instance.adb_serial)
					rows[ready.id]["status"] = "RUNNING"
					rows[ready.id]["attempts"] = str(ready.attempts)

					# Keep original media in place - do NOT move to processing
					original_media_path = ready.media_path
					
					log_fn and log_fn(
						f"[{instance.adb_serial}] Running {Path(original_media_path).name} "
						f"(original: {original_media_path})"
					)

					# For skip_push_media=True flow, device_media_path is just a placeholder
					# The actual file should already be on emulator in /sdcard/shared/Pictures/
					device_media_path = f"/sdcard/shared/Pictures/{Path(original_media_path).name}"

					fut = executor.submit(run_one, ready, instance, device_media_path)
					in_flight[fut] = (ready, instance, original_media_path)
					pending_count -= 1

				if not in_flight:
					continue

				done, _ = wait(set(in_flight.keys()), timeout=0.5, return_when=FIRST_COMPLETED)
				for fut in done:
					job, instance, original_media_path = in_flight.pop(fut)
					try:
						success, error = fut.result()
					except Exception as exc:  # pragma: no cover - defensive
						success, error = False, str(exc)

					if success:
						rows[job.id]["status"] = "SUCCESS"
						rows[job.id]["error"] = ""
						try:
							# Move ORIGINAL file to posted/ (not a processing copy)
							moved_path = move_to_posted(original_media_path)
							log_fn and log_fn(
								f"[{instance.adb_serial}] ✓ {Path(job.media_path).name}: SUCCESS "
								f"(moved to posted/)"
							)
						except Exception as exc:  # pragma: no cover - defensive
							rows[job.id]["status"] = "SUCCESS (move failed)"
							rows[job.id]["error"] = f"move posted failed: {exc}"
							log_fn and log_fn(
								f"[{instance.adb_serial}] ⚠ {Path(job.media_path).name}: SUCCESS "
								f"but move to posted/ failed: {exc}"
							)
					else:
						rows[job.id]["status"] = "FAILED"
						rows[job.id]["error"] = error or "unknown error"
						if self._is_adbkeyboard_setup_error(error):
							log_fn and log_fn(
								f"[{instance.adb_serial}] ✗ {Path(job.media_path).name}: FAILED - {error} "
								f"(kept in place: ADBKeyboard setup issue)"
							)
						else:
							try:
								# Move ORIGINAL file to failed/ (not a processing copy)
								move_to_failed(original_media_path)
								log_fn and log_fn(
									f"[{instance.adb_serial}] ✗ {Path(job.media_path).name}: FAILED - {error} "
									f"(moved to failed/)"
								)
							except Exception as exc:  # pragma: no cover - defensive
								rows[job.id]["error"] += f" | move failed error: {exc}"
								log_fn and log_fn(
									f"[{instance.adb_serial}] ✗ {Path(job.media_path).name}: FAILED - {error} "
									f"(move to failed/ also failed: {exc})"
								)

		if self.stop_event.is_set():
			log_fn and log_fn("Reels posting stopped by user.")

		return {
			"type": "run",
			"rows": rows,
			"stopped": self.stop_event.is_set(),
		}

	# ------------------------------------------------------------------
	# Signal handlers (main thread)
	# ------------------------------------------------------------------
	def _on_worker_log(self, message: str) -> None:
		if self._is_closing:
			return
		self._log(message)
		self.instance_log.appendPlainText(message)

	def _on_worker_error(self, message: str) -> None:
		if self._is_closing:
			return
		self._log(f"Worker error: {message}")
		self.instance_log.appendPlainText(f"Worker error: {message}")

	def _on_worker_done(self, result: Any) -> None:
		if self._is_closing:
			return

		if isinstance(result, dict) and result.get("type") == "run":
			rows = result.get("rows", {})
			for job_id, state in rows.items():
				row = self._job_row_map.get(job_id)
				if row is None:
					continue
				self.table.setItem(row, self.COL_INSTANCE, QTableWidgetItem(state.get("instance", "")))
				self.table.setItem(row, self.COL_STATUS, QTableWidgetItem(state.get("status", "")))
				self.table.setItem(row, self.COL_ATTEMPTS, QTableWidgetItem(state.get("attempts", "0")))
				self.table.setItem(row, self.COL_ERROR, QTableWidgetItem(state.get("error", "")))

			if result.get("stopped"):
				self._log("Run stopped.")
			else:
				self._log("Run finished.")

		self.start_btn.setEnabled(True)
		self.test_btn.setEnabled(True)
		self.stop_btn.setEnabled(False)

	# ------------------------------------------------------------------
	# Logging
	# ------------------------------------------------------------------
	def _log(self, msg: str) -> None:
		if callable(self.log_fn):
			self.log_fn(msg)
