from __future__ import annotations

from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from threading import Event, Thread
from typing import Any, Callable
import time
import uuid

from PySide6.QtCore import Qt, QDateTime, QThread, QTimer
from PySide6.QtGui import QColor, QIcon
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
	QGroupBox,
	QProgressBar,
	QHeaderView,
	QMenu,
	QSplitter,
	QFrame,
	QCheckBox,
	QComboBox,
)

from src.core.media_manager import (
	caption_from_filename,
	move_to_failed,
	move_to_posted,
	scan_media,
)
from src.core.caption_mapper import CaptionMapper
from src.core.ai_caption_service import AICaptionService
from src.core.config import save_config
from src.core.reel_jobs import ReelJob, ReelJobQueue
from src.core.reel_poster import ReelPoster, ADBKeyboardRequest
from src.core.task_runner import TaskRunner
from src.core.process_queue_manager import ProcessQueueManager, ProcessSnapshot
from src.ui.multi_reel_poster_worker import MultiReelPosterWorker
from src.ui.styles import STATUS_COLORS
from src.ui.toast_widget import ToastWidget
from src.ui.widgets import SectionTitle, icon_path


class ReelsPosterPage(QWidget):
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
		self._caption_mapper = CaptionMapper("./caption_mapping.json")

		self.task_runner = TaskRunner()
		self.task_runner.on_log.connect(self._on_worker_log)
		self.task_runner.on_error.connect(self._on_worker_error)
		self.task_runner.on_done.connect(self._on_worker_done)

		# Multi-media worker thread support - now per-process (parallel execution)
		self._workers_by_pid: dict[str, MultiReelPosterWorker] = {}  # process_id -> worker
		self._threads_by_pid: dict[str, QThread] = {}  # process_id -> thread
		self._running_serials: set[str] = set()  # Track which instance serials are active
		self._queue_progress_bars: dict[str, QProgressBar] = {}  # process_id -> progress bar

		# Process status tracking (since ProcessSnapshot doesn't have status)
		self._process_status: dict[str, str] = {}  # process_id -> "queued" | "running" | "completed" | "failed"

		# Process queue manager (core business logic)
		self.queue_manager = ProcessQueueManager(log_fn=self.log_fn)
		self.log_fn("[INIT] ProcessQueueManager created")

		# Register Qt signal callbacks for UI updates
		self.queue_manager.process_queued.connect(self._on_queue_process_queued)
		self.queue_manager.process_started.connect(self._on_queue_process_started)
		self.queue_manager.process_completed.connect(self._on_queue_process_completed)
		self.queue_manager.process_failed.connect(self._on_queue_process_failed)
		self.queue_manager.status_changed.connect(self._on_queue_status_changed)
		self.log_fn("[INIT] ✓ All queue_manager signals connected (UI thread safe)")

		# Scheduler timer for scheduled jobs (periodic queue check)
		self._queue_timer = QTimer(self)
		self._queue_timer.timeout.connect(self._check_queue)
		self._queue_timer.start(5000)  # check every 5 seconds

		# Saved pages feature
		self.saved_pages: list[str] = []
		self._build_ui()
		self._load_saved_pages()
		self._load_ai_settings()

	def _check_queue(self):
		if self._is_closing:
			return
		self.queue_manager.try_start_next()

	# ------------------------------------------------------------------
	# UI for building and running reel posting jobs."""

	# Queue table column indices
	COL_Q_INSTANCE = 0
	COL_Q_PAGE = 1
	COL_Q_MEDIA = 2
	COL_Q_MODE = 3
	COL_Q_SCHEDULE = 4
	COL_Q_STATUS = 5
	COL_Q_PROGRESS = 6
	COL_Q_RESULT = 7

	def _load_saved_pages(self):
		cfg = self.get_config_fn()
		self.saved_pages = cfg.get("saved_pages", [])
		if not isinstance(self.saved_pages, list):
			self.saved_pages = []
		self._refresh_saved_pages_dropdown()

	def _save_current_page(self):
		page = self.page_input.text().strip()
		if not page:
			self.log_fn("Page name is empty, not saved.")
			return
		# Avoid duplicates (case-insensitive)
		if any(page.lower() == p.lower() for p in self.saved_pages):
			self.log_fn(f"Page name already saved: {page}")
			return
		self.saved_pages.append(page)
		cfg = self.get_config_fn()
		cfg["saved_pages"] = self.saved_pages
		save_config(cfg)
		self.log_fn(f"Saved page name: {page}")
		self._refresh_saved_pages_dropdown(select_page=page)

	def _refresh_saved_pages_dropdown(self, select_page: str = None):
		self.saved_pages_dropdown.blockSignals(True)
		self.saved_pages_dropdown.clear()
		self.saved_pages_dropdown.addItem("Select saved page...")
		for page in self.saved_pages:
			self.saved_pages_dropdown.addItem(page)
		self.saved_pages_dropdown.blockSignals(False)
		if select_page:
			idx = self.saved_pages_dropdown.findText(select_page, Qt.MatchFixedString)
			if idx != -1:
				self.saved_pages_dropdown.setCurrentIndex(idx)

	def _on_saved_page_selected(self, idx):
		if idx <= 0:
			return
		page = self.saved_pages_dropdown.currentText()
		if page:
			self.page_input.setText(page)
			self.log_fn(f"Selected saved page: {page}")

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
		layout.setSpacing(10)
		layout.setContentsMargins(10, 10, 10, 10)

		layout.addWidget(SectionTitle("Top Toolbar"))

		top_controls = QGroupBox("Run Controls")
		top_controls_layout = QVBoxLayout(top_controls)
		top_controls_layout.setSpacing(8)
		top_controls_layout.setContentsMargins(10, 10, 10, 10)

		# Page input row with Save button and dropdown
		page_row = QHBoxLayout()
		page_row.setSpacing(8)
		page_label = QLabel("Page")
		page_label.setStyleSheet("padding: 10px; border-radius: 10px; background-color: #2a2c3e;")
		page_row.addWidget(page_label)
		self.page_input = QLineEdit()
		self.page_input.setPlaceholderText("Select target page")
		self.page_input.setMinimumWidth(220)
		self.page_input.textChanged.connect(self._validate_start_button)
		page_row.addWidget(self.page_input)

		self.save_page_btn = QPushButton("Save")
		self.save_page_btn.setFixedWidth(60)
		self.save_page_btn.setToolTip("Save page name")
		self.save_page_btn.clicked.connect(self._save_current_page)
		page_row.addWidget(self.save_page_btn)

		self.saved_pages_dropdown = QComboBox()
		self.saved_pages_dropdown.setMinimumWidth(180)
		self.saved_pages_dropdown.setMaximumWidth(220)
		self.saved_pages_dropdown.currentIndexChanged.connect(self._on_saved_page_selected)
		page_row.addWidget(self.saved_pages_dropdown)

		page_row.addStretch()
		top_controls_layout.addLayout(page_row)

		# Other controls in second row
		top_bar = QHBoxLayout()
		top_bar.setSpacing(8)

		mode_label = QLabel("Mode")
		mode_label.setStyleSheet("padding: 10px; border-radius: 10px; background-color: #2a2c3e;")
		top_bar.addWidget(mode_label)
		self.post_now_radio = QRadioButton("Post now")
		self.schedule_radio = QRadioButton("Schedule")
		self.post_now_radio.setChecked(True)
		mode_group = QButtonGroup(self)
		mode_group.addButton(self.post_now_radio)
		mode_group.addButton(self.schedule_radio)
		self.post_now_radio.toggled.connect(self._on_mode_changed)
		top_bar.addWidget(self.post_now_radio)
		top_bar.addWidget(self.schedule_radio)

		self.schedule_dt = QDateTimeEdit(QDateTime.currentDateTime())
		self.schedule_dt.setCalendarPopup(True)
		self.schedule_dt.setEnabled(False)
		self.schedule_dt.dateTimeChanged.connect(self._update_schedule_warning)
		schedule_label = QLabel("Schedule")
		schedule_label.setStyleSheet("padding: 10px; border-radius: 10px; background-color: #2a2c3e;")
		top_bar.addWidget(schedule_label)
		top_bar.addWidget(self.schedule_dt)

		retry_label = QLabel("Retry")
		retry_label.setStyleSheet("padding: 10px; border-radius: 10px; background-color: #2a2c3e;")
		top_bar.addWidget(retry_label)
		self.retry_spin = QSpinBox()
		self.retry_spin.setRange(1, 10)
		self.retry_spin.setValue(2)
		top_bar.addWidget(self.retry_spin)

		concurrency_label = QLabel("Concurrency")
		concurrency_label.setStyleSheet("padding: 10px; border-radius: 10px; background-color: #2a2c3e;")
		top_bar.addWidget(concurrency_label)
		self.concurrent_spin = QSpinBox()
		self.concurrent_spin.setRange(1, 32)
		self.concurrent_spin.setValue(2)
		top_bar.addWidget(self.concurrent_spin)

		top_bar.addStretch()

		self.start_btn = QPushButton("Start")
		self.start_btn.setIcon(QIcon(icon_path("play.svg")))
		self.start_btn.clicked.connect(self._start)

		self.stop_btn = QPushButton("Stop")
		self.stop_btn.setObjectName("dangerButton")
		self.stop_btn.setIcon(QIcon(icon_path("stop.svg")))
		self.stop_btn.clicked.connect(self._stop)
		self.stop_btn.setEnabled(False)

		self.test_btn = QPushButton("Test")
		self.test_btn.setObjectName("secondaryButton")
		self.test_btn.clicked.connect(self._test)

		top_bar.addWidget(self.start_btn)
		top_bar.addWidget(self.stop_btn)
		top_bar.addWidget(self.test_btn)

		top_controls_layout.addLayout(top_bar)

		self.schedule_warning = QLabel()
		self.schedule_warning.setObjectName("subtleLabel")
		self.schedule_warning.hide()
		top_controls_layout.addWidget(self.schedule_warning)

		layout.addWidget(top_controls)

		# AI Captioning section
		ai_controls = QGroupBox("AI Captioning")
		ai_controls_layout = QVBoxLayout(ai_controls)
		ai_controls_layout.setSpacing(8)
		ai_controls_layout.setContentsMargins(10, 10, 10, 10)

		# Row 0: Smart Title by Bot checkbox
		ai_row0 = QHBoxLayout()
		ai_row0.setSpacing(8)
		self.smart_title_checkbox = QCheckBox("Smart Title by Bot")
		self.smart_title_checkbox.setToolTip("Parse filename locally to extract title and hashtags")
		self.smart_title_checkbox.stateChanged.connect(self._on_smart_title_changed)
		ai_row0.addWidget(self.smart_title_checkbox)
		ai_row0.addStretch()
		ai_controls_layout.addLayout(ai_row0)

		# Row 1: AI retitle checkbox and language selector
		ai_row1 = QHBoxLayout()
		ai_row1.setSpacing(8)
		self.ai_retitle_checkbox = QCheckBox("Retitle by AI")
		self.ai_retitle_checkbox.stateChanged.connect(self._on_ai_enabled_changed)
		ai_row1.addWidget(self.ai_retitle_checkbox)
		
		ai_lang_label = QLabel("Language:")
		ai_lang_label.setStyleSheet("padding: 10px; border-radius: 10px; background-color: #2a2c3e;")
		ai_row1.addWidget(ai_lang_label)
		self.ai_language_combo = QComboBox()
		self.ai_language_combo.setFixedHeight(30)
		self.ai_language_combo.addItems([
			"English",
			"Khmer",
			"French",
			"Spanish",
			"German",
			"Chinese",
			"Japanese",
			"Korean",
			"Thai",
			"Vietnamese",
		])
		self.ai_language_combo.setEnabled(False)
		self.ai_language_combo.currentTextChanged.connect(lambda: self._save_ai_settings())
		ai_row1.addWidget(self.ai_language_combo)
		ai_row1.addStretch()
		ai_controls_layout.addLayout(ai_row1)

		# Row 2: Gemini API key input and test button
		ai_row2 = QHBoxLayout()
		ai_row2.setSpacing(8)
		api_key_label = QLabel("Gemini API Key:")
		api_key_label.setStyleSheet("padding: 10px; border-radius: 10px; background-color: #2a2c3e;")
		ai_row2.addWidget(api_key_label)
		self.gemini_api_key_edit = QLineEdit()
		self.gemini_api_key_edit.setFixedHeight(30)
		self.gemini_api_key_edit.setEchoMode(QLineEdit.Password)
		self.gemini_api_key_edit.setPlaceholderText("Paste Gemini API key here")
		self.gemini_api_key_edit.setEnabled(False)
		self.gemini_api_key_edit.textChanged.connect(self._on_api_key_changed)
		ai_row2.addWidget(self.gemini_api_key_edit, 1)
		self.gemini_test_button = QPushButton("Test API")
		self.gemini_test_button.setFixedHeight(30)
		self.gemini_test_button.setFixedWidth(80)
		self.gemini_test_button.setEnabled(False)
		self.gemini_test_button.clicked.connect(self._test_gemini_api)
		ai_row2.addWidget(self.gemini_test_button)
		ai_controls_layout.addLayout(ai_row2)

		# Additional Hashtag controls (moved below Gemini API key input)
		additional_hashtag_row = QHBoxLayout()
		additional_hashtag_row.setSpacing(8)
		self.additional_hashtag_checkbox = QCheckBox("Additional hashtag")
		self.additional_hashtag_input = QLineEdit()
		self.additional_hashtag_input.setPlaceholderText("#viral #reels #funny")
		self.additional_hashtag_input.setEnabled(False)
		self.additional_hashtag_checkbox.toggled.connect(
			lambda checked: self.additional_hashtag_input.setEnabled(checked)
		)
		additional_hashtag_row.addWidget(self.additional_hashtag_checkbox)
		additional_hashtag_row.addWidget(self.additional_hashtag_input, 1)
		ai_controls_layout.addLayout(additional_hashtag_row)

		

		layout.addWidget(ai_controls)

		# Subfolder navigation controls
		subfolder_controls = QGroupBox("Subfolder Navigation")
		subfolder_layout = QHBoxLayout(subfolder_controls)
		subfolder_layout.setSpacing(8)
		subfolder_layout.setContentsMargins(10, 10, 10, 10)

		self.use_subfolder_checkbox = QCheckBox("Have sub folder?")
		self.use_subfolder_checkbox.setToolTip("Navigate into a subfolder inside Pictures before selecting media")
		self.use_subfolder_checkbox.stateChanged.connect(self._on_subfolder_toggled)
		subfolder_layout.addWidget(self.use_subfolder_checkbox)

		subfolder_name_label = QLabel("Sub folder name:")
		subfolder_name_label.setStyleSheet("padding: 10px; border-radius: 10px; background-color: #2a2c3e;")
		subfolder_layout.addWidget(subfolder_name_label)
		self.subfolder_name_input = QLineEdit()
		self.subfolder_name_input.setPlaceholderText("e.g. MindReg")
		self.subfolder_name_input.setEnabled(False)
		self.subfolder_name_input.setMinimumWidth(180)
		subfolder_layout.addWidget(self.subfolder_name_input)
		subfolder_layout.addStretch()

		layout.addWidget(subfolder_controls)

		splitter = QSplitter(Qt.Horizontal)

		left_widget = QWidget()
		left_col = QVBoxLayout(left_widget)
		left_col.setContentsMargins(0, 0, 0, 0)
		left_col.setSpacing(10)

		left_col.addWidget(SectionTitle("Media Selection"))
		media_group = QGroupBox("Media Panel")
		media_layout = QVBoxLayout(media_group)
		media_layout.setSpacing(10)
		media_layout.setContentsMargins(10, 10, 10, 10)

		folder_row = QHBoxLayout()
		folder_row.setSpacing(8)
		self.folder_input = QLineEdit()
		self.folder_input.setPlaceholderText("Choose folder containing reels")

		self.browse_btn = QPushButton("Browse")
		self.browse_btn.setObjectName("iconButton")
		self.browse_btn.setIcon(QIcon(icon_path("folder.svg")))
		self.browse_btn.clicked.connect(self._pick_folder)

		self.scan_btn = QPushButton("Scan")
		self.scan_btn.setObjectName("iconButton")
		self.scan_btn.setIcon(QIcon(icon_path("scan.svg")))
		self.scan_btn.clicked.connect(self._scan_media)

		folder_row.addWidget(self.folder_input)
		folder_row.addWidget(self.browse_btn)
		folder_row.addWidget(self.scan_btn)
		media_layout.addLayout(folder_row)

		sel_row = QHBoxLayout()
		sel_row.setSpacing(8)
		sel_all_btn = QPushButton("All")
		sel_all_btn.setObjectName("secondaryButton")
		sel_all_btn.clicked.connect(self._select_all_media)
		unsel_btn = QPushButton("None")
		unsel_btn.setObjectName("secondaryButton")
		unsel_btn.clicked.connect(self._unselect_all_media)
		invert_btn = QPushButton("Invert")
		invert_btn.setObjectName("secondaryButton")
		invert_btn.clicked.connect(self._invert_media_selection)
		self.media_counter_label = QLabel("0 / 0")
		self.media_counter_label.setObjectName("subtleLabel")
		sel_row.addWidget(sel_all_btn)
		sel_row.addWidget(unsel_btn)
		sel_row.addWidget(invert_btn)
		sel_row.addStretch()
		sel_row.addWidget(self.media_counter_label)
		media_layout.addLayout(sel_row)

		self.media_list = QListWidget()
		self.media_list.itemClicked.connect(lambda _: self._update_media_counter())
		media_layout.addWidget(self.media_list, 1)

		self.media_empty_label = QLabel("No media loaded\nBrowse a folder to begin")
		self.media_empty_label.setObjectName("emptyStateLabel")
		self.media_empty_label.setAlignment(Qt.AlignCenter)
		media_layout.addWidget(self.media_empty_label)

		left_col.addWidget(media_group, 4)

		splitter.addWidget(left_widget)

		right_widget = QWidget()
		right_col = QVBoxLayout(right_widget)
		right_col.setContentsMargins(0, 0, 0, 0)
		right_col.setSpacing(10)

		right_col.addWidget(SectionTitle("Process Queue"))
		queue_group = QGroupBox("Queue Dashboard")
		queue_layout = QVBoxLayout(queue_group)
		queue_layout.setSpacing(8)
		queue_layout.setContentsMargins(10, 10, 10, 10)

		queue_toolbar = QHBoxLayout()
		queue_toolbar.addStretch()
		self.clear_completed_btn = QPushButton("Clear Completed")
		self.clear_completed_btn.setObjectName("secondaryButton")
		self.clear_completed_btn.setIcon(QIcon(icon_path("trash.svg")))
		self.clear_completed_btn.clicked.connect(self._clear_completed_processes)
		queue_toolbar.addWidget(self.clear_completed_btn)
		queue_layout.addLayout(queue_toolbar)

		self.queue_table = QTableWidget(0, 8)
		self.queue_table.setHorizontalHeaderLabels([
			"Instance", "Page", "Media", "Mode",
			"Schedule", "Status", "Progress", "Result",
		])
		header = self.queue_table.horizontalHeader()
		header.setStretchLastSection(True)
		header.setSectionResizeMode(self.COL_Q_STATUS, QHeaderView.ResizeMode.ResizeToContents)
		header.setSectionResizeMode(self.COL_Q_PROGRESS, QHeaderView.ResizeMode.Stretch)
		self.queue_table.setAlternatingRowColors(True)
		self.queue_table.verticalHeader().setDefaultSectionSize(24)
		self.queue_table.setContextMenuPolicy(Qt.CustomContextMenu)
		self.queue_table.customContextMenuRequested.connect(self._on_queue_context_menu)
		queue_layout.addWidget(self.queue_table, 1)

		self.queue_empty_label = QLabel("No processes in queue\nCreate a process to get started")
		self.queue_empty_label.setObjectName("emptyStateLabel")
		self.queue_empty_label.setAlignment(Qt.AlignCenter)
		queue_layout.addWidget(self.queue_empty_label)

		right_col.addWidget(queue_group, 1)

		splitter.addWidget(right_widget)
		splitter.setSizes([520, 580])
		layout.addWidget(splitter, 1)

		self._toast = ToastWidget(self)

		self._validate_start_button()
		self._update_empty_states()

	def _load_ai_settings(self) -> None:
		"""Load AI settings from config and update UI."""
		try:
			config = self.get_config_fn()
			use_smart_title = config.get("use_smart_title", "false").lower() == "true"
			use_ai = config.get("use_ai_retitle", "false").lower() == "true"
			api_key = config.get("gemini_api_key", "")
			language = config.get("ai_target_language", "English")
			
			# Debug logging
			has_key = bool(api_key and api_key.strip())
			self.log_fn(f"Loading AI settings from config:")
			self.log_fn(f"  - use_smart_title: {use_smart_title}")
			self.log_fn(f"  - use_ai_retitle: {use_ai}")
			self.log_fn(f"  - gemini_api_key present: {'yes' if has_key else 'no'}")
			self.log_fn(f"  - ai_target_language: {language}")
			
			self.smart_title_checkbox.setChecked(use_smart_title)
			self.ai_retitle_checkbox.setChecked(use_ai)
			self.gemini_api_key_edit.setText(api_key)
			
			idx = self.ai_language_combo.findText(language)
			if idx >= 0:
				self.ai_language_combo.setCurrentIndex(idx)
		except Exception as e:
			self.log_fn(f"Error loading AI settings: {e}")

	def _save_ai_settings(self) -> None:
		"""Save AI settings to config."""
		try:
			config = self.get_config_fn()
			config["use_smart_title"] = "true" if self.smart_title_checkbox.isChecked() else "false"
			config["use_ai_retitle"] = "true" if self.ai_retitle_checkbox.isChecked() else "false"
			config["gemini_api_key"] = self.gemini_api_key_edit.text()
			config["ai_target_language"] = self.ai_language_combo.currentText()
			save_config(config)
			
			# Debug logging
			has_key = bool(config["gemini_api_key"].strip())
			self.log_fn(f"AI settings saved to config:")
			self.log_fn(f"  - use_smart_title: {config['use_smart_title']}")
			self.log_fn(f"  - use_ai_retitle: {config['use_ai_retitle']}")
			self.log_fn(f"  - gemini_api_key present: {'yes' if has_key else 'no'}")
			self.log_fn(f"  - ai_target_language: {config['ai_target_language']}")
		except Exception as e:
			self.log_fn(f"Error saving AI settings: {e}")

	def _on_smart_title_changed(self, state: int) -> None:
		"""Handle Smart Title checkbox state change - mutually exclusive with AI."""
		if self.smart_title_checkbox.isChecked():
			# Uncheck AI Retitle if Smart Title is checked
			self.ai_retitle_checkbox.setChecked(False)
		self._save_ai_settings()

	def _on_ai_enabled_changed(self, state: int) -> None:
		"""Handle AI retitle checkbox state change - mutually exclusive with Smart Title."""
		checked = self.ai_retitle_checkbox.isChecked()
		if checked:
			# Uncheck Smart Title if AI Retitle is checked
			self.smart_title_checkbox.setChecked(False)
		self.ai_language_combo.setEnabled(checked)
		self.gemini_api_key_edit.setEnabled(checked)
		self.gemini_test_button.setEnabled(checked and len(self.gemini_api_key_edit.text()) > 0)
		self._save_ai_settings()

	def _on_api_key_changed(self, text: str) -> None:
		"""Handle API key text changes - enable/disable test button."""
		has_key = len(text.strip()) > 0
		is_enabled = self.ai_retitle_checkbox.isChecked()
		self.gemini_test_button.setEnabled(is_enabled and has_key)
		self._save_ai_settings()

	def _test_gemini_api(self) -> None:
		"""Test Gemini API connection in a worker thread."""
		api_key = self.gemini_api_key_edit.text().strip()
		if not api_key:
			self._toast.show_error("Please enter a Gemini API key")
			return
		
		self.gemini_test_button.setEnabled(False)
		self.gemini_test_button.setText("Testing...")
		
		def test_worker():
			try:
				service = AICaptionService(api_key, self.log_fn)
				success, error = service.test_connection()
				
				# Update UI in main thread using Qt signals
				if not self._is_closing:
					self.gemini_test_button.setText("Test API")
					self.gemini_test_button.setEnabled(True)
					
					if success:
						self._toast.show_success("Gemini API is working!")
						self.log_fn("✓ Gemini API connection test PASSED")
					else:
						self._toast.show_error(f"API test failed: {error}")
						self.log_fn(f"✗ Gemini API connection test FAILED: {error}")
			except Exception as e:
				if not self._is_closing:
					self.gemini_test_button.setText("Test API")
					self.gemini_test_button.setEnabled(True)
					self._toast.show_error(f"Test error: {str(e)}")
					self.log_fn(f"✗ Gemini API test exception: {str(e)}")
		
		# Run test in background thread
		thread = Thread(target=test_worker, daemon=True)
		thread.start()

	def closeEvent(self, event) -> None:  # type: ignore[override]
		self._is_closing = True
		self.stop_event.set()
		# Stop periodic queue timer
		if hasattr(self, '_queue_timer'):
			self._queue_timer.stop()
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
		self._update_media_counter()

	def _on_mode_changed(self) -> None:
		self.schedule_dt.setEnabled(self.schedule_radio.isChecked())
		self._update_schedule_warning()

	# ------------------------------------------------------------------
	# Media selection helpers
	# ------------------------------------------------------------------
	def _select_all_media(self) -> None:
		for i in range(self.media_list.count()):
			self.media_list.item(i).setCheckState(Qt.Checked)
		self._update_media_counter()

	def _unselect_all_media(self) -> None:
		for i in range(self.media_list.count()):
			self.media_list.item(i).setCheckState(Qt.Unchecked)
		self._update_media_counter()

	def _invert_media_selection(self) -> None:
		for i in range(self.media_list.count()):
			item = self.media_list.item(i)
			item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)
		self._update_media_counter()

	def _update_media_counter(self) -> None:
		total = self.media_list.count()
		selected = sum(
			1 for i in range(total) if self.media_list.item(i).checkState() == Qt.Checked
		)
		self.media_counter_label.setText(f"{selected} / {total}")
		self._validate_start_button()
		self._update_empty_states()

	def _on_subfolder_toggled(self, state: int) -> None:
		"""Enable/disable subfolder name input based on checkbox."""
		enabled = state == Qt.Checked.value
		self.subfolder_name_input.setEnabled(enabled)
		if not enabled:
			self.subfolder_name_input.clear()

	def _validate_start_button(self) -> None:
		"""Enable/disable Start based on required fields; set tooltip."""
		reasons: list[str] = []
		has_media = any(
			self.media_list.item(i).checkState() == Qt.Checked
			for i in range(self.media_list.count())
		)
		if not has_media:
			reasons.append("Select at least one media file")
		if not self.page_input.text().strip():
			reasons.append("Enter a target page name")

		enabled = not reasons
		self.start_btn.setEnabled(enabled)
		self.test_btn.setEnabled(enabled)
		tooltip = "\n".join(reasons) if reasons else ""
		self.start_btn.setToolTip(tooltip)
		self.test_btn.setToolTip(tooltip)

	def _update_schedule_warning(self) -> None:
		if not self.schedule_radio.isChecked():
			self.schedule_warning.hide()
			return
		scheduled = self.schedule_dt.dateTime().toPython()
		now = datetime.now()
		if scheduled < now:
			self.schedule_warning.setText("\u26A0 Scheduled time is in the past.")
			self.schedule_warning.show()
		elif (scheduled - now).total_seconds() < 25 * 60:
			self.schedule_warning.setText(
				"\u26A0 Scheduled time is for future"
			)
			self.schedule_warning.show()
		else:
			self.schedule_warning.hide()

	def _update_empty_states(self) -> None:
		self.media_empty_label.setVisible(self.media_list.count() == 0)
		self.queue_empty_label.setVisible(self.queue_table.rowCount() == 0)

	def _resolve_selected_serials(self) -> None:
		"""Freshly resolve ADB serials for all selected instances."""
		from src.core.adb_manager import ADBManager
		from src.core.ldplayer_controller import LDPlayerController

		cfg = self.get_config_fn()
		ctrl = LDPlayerController(cfg.get("dnconsole_path", ""), self._log)
		adb = ADBManager(cfg.get("adb_path", ""), self._log)

		# Re-scan instances to get current running state
		instances = ctrl.list_instances()
		# Build a quick index->running lookup
		running_map = {int(i["index"]): i for i in instances if i.get("is_running")}

		state = self.get_state_fn()
		for inst in state.get_selected_instances():
			expected = adb.serial_for_index(inst.index)
			devices = set(adb.list_devices())
			if expected in devices:
				if inst.adb_serial != expected:
					self._log(f"Resolved instance {inst.index} ({inst.name}) → {expected}")
				inst.adb_serial = expected
			elif inst.index in running_map:
				# Running but no serial — clear stale serial
				self._log(
					f"Instance {inst.index} ({inst.name}) is running but "
					f"{expected} not in ADB devices. Serial cleared."
				)
				inst.adb_serial = None
			else:
				# Not running — clear serial
				if inst.adb_serial:
					self._log(f"Instance {inst.index} ({inst.name}) is not running. Serial cleared.")
				inst.adb_serial = None

	# ------------------------------------------------------------------
	# Queue context menu (right-click actions)
	# ------------------------------------------------------------------
	def _on_queue_context_menu(self, pos) -> None:
		row = self.queue_table.rowAt(pos.y())
		if row < 0:
			return
		item = self.queue_table.item(row, self.COL_Q_INSTANCE)
		if not item:
			return
		process_id = item.data(Qt.UserRole)
		process = self.queue_manager.get_process(process_id)
		if not process:
			return

		menu = QMenu(self)
		if process.status == "Waiting":
			act = menu.addAction("Cancel")
			act.triggered.connect(lambda: self.queue_manager.cancel_process(process_id))
		if process.status == "Running":
			act = menu.addAction("Cancel Running")
			act.triggered.connect(lambda: self._cancel_running_process(process_id))
		if process.status in ("Completed", "Failed"):
			act = menu.addAction("Remove")
			act.triggered.connect(lambda: self._remove_queue_row(row, process_id))
		if menu.actions():
			menu.exec(self.queue_table.viewport().mapToGlobal(pos))

	def _cancel_running_process(self, process_id: str) -> None:
		reply = QMessageBox.question(
			self, "Cancel Process",
			"Cancel this running process?",
			QMessageBox.Yes | QMessageBox.No,
			QMessageBox.No,
		)
		if reply != QMessageBox.Yes:
			return
		worker = self._workers_by_pid.get(process_id)
		if worker:
			worker.cancel()

	def _remove_queue_row(self, row: int, process_id: str) -> None:
		self._queue_progress_bars.pop(process_id, None)
		self.queue_table.removeRow(row)
		self._update_empty_states()

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
		"""Start posting process with queue manager."""
		self._log("[START] ==================================================")
		self._log("[START] _start() method called")
		self._log(f"[START] Queue manager exists: {self.queue_manager is not None}")
		if self.queue_manager:
			self._log("[START] Queue manager signals available: process_queued, process_started, process_completed, process_failed, status_changed")
			
			# Validate AI settings if enabled
			if self.ai_retitle_checkbox.isChecked():
				api_key = self.gemini_api_key_edit.text().strip()
				if not api_key:
					self._toast.show_error("AI retitle enabled but no API key provided")
					self.log_fn("✗ Cannot start: AI enabled but Gemini API key is missing")
					return
				
				# Test if Gemini service can be initialized
				self.log_fn("Validating Gemini API configuration...")
				test_service = AICaptionService(api_key, self.log_fn)
				is_ready, init_error = test_service.is_ready()
				
				if not is_ready:
					self._toast.show_error(f"Gemini initialization failed: {init_error}")
					self.log_fn(f"✗ Cannot start: Gemini init failed - {init_error}")
					self.log_fn("   You can either:")
					self.log_fn("   1. Fix the API key and try again")
					self.log_fn("   2. Uncheck 'Retitle by AI' to proceed without AI")
					return
				else:
					self.log_fn("✓ Gemini API configuration validated successfully")
			
			# Validate subfolder if enabled
			if self.use_subfolder_checkbox.isChecked():
				subfolder = self.subfolder_name_input.text().strip()
				if not subfolder:
					self._toast.show_error("Please enter a sub folder name.")
					self.log_fn("✗ Cannot start: Subfolder enabled but no folder name provided")
					return

			payload = self._build_run_payload(test_mode=False)
			if payload is None:
				self._log("[START] ✗ _build_run_payload returned None, aborting")
				return
			
			# Use queue manager for multi-media posts
			jobs = payload["jobs"]
			instances = payload["instances"]
			
			self._log(f"[START] ===== _start() called =====")
			self._log(f"[START] Number of jobs: {len(jobs)}, Number of instances: {len(instances)}")
			self._log(f"[START] Payload keys: {list(payload.keys())}")
			
			if not jobs or not instances:
				self._log(f"[START] ✗ Invalid payload: jobs={len(jobs)}, instances={len(instances)}")
				return
			
			# ALL posts go through queue manager (single OR multi-media)
			self._log(f"[START] ✓ Queueing process ({len(jobs)} job(s))")
			
			# Create process snapshot and add to queue
			selected_instance = instances[0]
			instance_serial = getattr(selected_instance, "adb_serial", "")
			instance_name = getattr(selected_instance, "name", instance_serial or "Unknown")
			post_mode = jobs[0].post_mode if jobs else "NOW"
			scheduled_at = jobs[0].scheduled_at if jobs else None
			page_name = jobs[0].target_page if jobs else ""
			
			self._log(f"[START] Process details:")
			self._log(f"        instance_name='{instance_name}'")
			self._log(f"        instance_serial='{instance_serial}'")
			self._log(f"        selected_instance.name='{getattr(selected_instance, 'name', 'N/A')}'")
			self._log(f"        Jobs: {len(jobs)}, Mode: {post_mode}")
			
			# Check if instance is already busy
			is_busy = self.queue_manager.is_instance_busy(instance_name)
			self._log(f"[START] Instance '{instance_name}' busy check: {is_busy}")
			if is_busy:
				self._log(f"[START] Instance is locked, process will queue")
			else:
				self._log(f"[START] Instance is free, process will start immediately")
			
			# Create immutable process snapshot
			process = ProcessSnapshot(
				process_id=str(uuid.uuid4()),
				instance_name=instance_name,
				instance_serial=instance_serial,
				page_name=page_name,
				jobs=jobs,
				post_mode=post_mode,
				scheduled_at=scheduled_at,
				have_subfolder=self.use_subfolder_checkbox.isChecked(),
				subfolder_name=self.subfolder_name_input.text().strip()
			)
			
			self._log(f"[START] ProcessSnapshot created with ID: {process.process_id[:8]}")
			
			# Register this process in status tracking
			self._process_status[process.process_id] = "queued"
			
			# Add to queue manager
			self._log(f"[START] Calling queue_manager.enqueue()")
			self.queue_manager.enqueue(process)
			
			# Log summary
			mode_str = "Immediate"
			if post_mode == "SCHEDULED" and scheduled_at:
				mode_str = f"Scheduled ({scheduled_at.strftime('%H:%M')})"
			
			self._log(f"[START] Process {process.process_id[:8]} queued: {len(jobs)} {'media' if len(jobs) > 1 else 'file'} for {instance_name}, mode={mode_str}")
			
			# Clear inputs AFTER snapshot is created
			self._clear_inputs()
			
			# Toast feedback
			self._toast.show_message(
				f"\u2713 Process added to queue\n"
				f"Instance: {instance_name}\n"
				f"Media: {len(jobs)} {'files' if len(jobs) > 1 else 'file'}  \u2022  Mode: {mode_str}"
			)

	def _test(self) -> None:
		# Validate AI settings if enabled
		if self.ai_retitle_checkbox.isChecked():
			api_key = self.gemini_api_key_edit.text().strip()
			if not api_key:
				self._toast.show_error("AI retitle enabled but no API key provided")
				self.log_fn("✗ Cannot test: AI enabled but Gemini API key is missing")
				return
			
			# Test if Gemini service can be initialized
			self.log_fn("Validating Gemini API configuration...")
			test_service = AICaptionService(api_key, self.log_fn)
			is_ready, init_error = test_service.is_ready()
			
			if not is_ready:
				self._toast.show_error(f"Gemini initialization failed: {init_error}")
				self.log_fn(f"✗ Cannot test: Gemini init failed - {init_error}")
				self.log_fn("   You can either:")
				self.log_fn("   1. Fix the API key and try again")
				self.log_fn("   2. Uncheck 'Retitle by AI' to proceed without AI")
				return
			else:
				self.log_fn("✓ Gemini API configuration validated successfully")
		
		payload = self._build_run_payload(test_mode=True)
		if payload is None:
			return
		self._start_worker(payload)

	def _stop(self) -> None:
		"""Stop all running processes."""
		self._log("[STOP] ===== _stop() called =====")
		# Confirmation dialog
		active = len(self._workers_by_pid)
		self._log(f"[STOP] Active workers: {active}")
		self._log(f"[STOP] Active worker IDs: {list(self._workers_by_pid.keys())}")
		if active > 0:
			reply = QMessageBox.question(
				self, "Confirm Stop",
				f"Cancel {active} running process(es)?",
				QMessageBox.Yes | QMessageBox.No,
				QMessageBox.No,
			)
			if reply != QMessageBox.Yes:
				self._log("[STOP] User declined to stop processes")
				return
			self._log("[STOP] User confirmed stop")

		# Stop all active multi-media workers
		self._log("[STOP] Cancelling all active workers...")
		for process_id, worker in self._workers_by_pid.items():
			if worker is not None:
				self._log(f"[STOP] Cancelling worker for process {process_id[:8]}")
				worker.cancel()
		
		# Stop the parallel worker (if any)
		self.stop_event.set()
		self.stop_btn.setEnabled(False)
		self._log("[STOP] Stop requested - all workers cancelled.")

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

		# Single media: use existing parallel flow
		self.task_runner.run(self._do_run_jobs, payload)

	def _start_multi_media_worker(self, process_id: str, instance_serial: str, jobs: list[ReelJob], instances: list[Any]) -> None:
		"""Start sequential multi-media posting worker for a queue process.
		
		Each process gets its own worker and thread, enabling parallel execution
		for different instances.
		"""
		self._log(f"[WORKER-START] ===== _start_multi_media_worker =====")
		self._log(f"[WORKER-START] Process ID: {process_id[:8]}")
		self._log(f"[WORKER-START] Instance serial: {instance_serial}")
		self._log(f"[WORKER-START] Jobs count: {len(jobs)}")
		self._log(f"[WORKER-START] Current workers: {list(self._workers_by_pid.keys())}")
		self._log(f"[WORKER-START] Current threads: {list(self._threads_by_pid.keys())}")
		self._log(f"[WORKER-START] Running serials: {self._running_serials}")
		
		# Check if worker already exists for this process
		if process_id in self._workers_by_pid:
			self._log(f"[WORKER-START] ✗ WARNING: Worker already exists for process {process_id[:8]}! Replacing...")
			old_worker = self._workers_by_pid[process_id]
			if old_worker:
				old_worker.cancel()
		
		# Create worker with process tracking
		worker = MultiReelPosterWorker(
			process_id=process_id,
			instance_serial=instance_serial,
			jobs=jobs,
			instances=instances,
			adb_manager=self.get_adb_manager_fn(),
			get_adbkeyboard_request_fn=self._create_adbkeyboard_request,
			log_fn=self.log_fn,
			get_config_fn=self.get_config_fn,
		)
		self._log(f"[WORKER-START] ✓ Worker object created")
		
		# Create thread
		thread = QThread()
		worker.moveToThread(thread)
		self._log(f"[WORKER-START] ✓ Worker moved to thread")
		
		# Connect signals (include process_id in finished signal)
		thread.started.connect(worker.run)
		worker.log_message.connect(self._on_multi_worker_log)
		pid = process_id  # capture for lambda
		worker.progress.connect(
			lambda idx, total, name, status, p=pid: self._on_multi_worker_progress(idx, total, name, status, p)
		)
		worker.finished.connect(self._on_multi_worker_finished)
		self._log(f"[WORKER-START] ✓ Signals connected")
		
		# Auto-cleanup: when thread finishes, delete worker and thread
		thread.finished.connect(worker.deleteLater)
		thread.finished.connect(thread.deleteLater)
		
		# Store references
		self._workers_by_pid[process_id] = worker
		self._threads_by_pid[process_id] = thread
		self._running_serials.add(instance_serial)
		self._log(f"[WORKER-START] ✓ Worker stored in _workers_by_pid[{process_id[:8]}]")
		self._log(f"[WORKER-START] ✓ Thread stored in _threads_by_pid[{process_id[:8]}]")
		self._log(f"[WORKER-START] Total active workers: {len(self._workers_by_pid)}")
		
		# Start thread
		thread.start()
		self._log(f"[WORKER-START] ✓ Thread started for process {process_id[:8]}")

	def _on_multi_worker_log(self, message: str) -> None:
		"""Handle log message from multi-media worker."""
		if self._is_closing:
			return
		self._log(message)

	def _on_multi_worker_progress(self, idx: int, total: int, media_name: str, status: str, process_id: str = "") -> None:
		"""Handle progress update from multi-media worker."""
		if self._is_closing:
			return
		from PySide6.QtCore import QTimer
		if process_id:
			bar = self._queue_progress_bars.get(process_id)
			if bar:
				QTimer.singleShot(0, lambda: bar.setMaximum(total))
				QTimer.singleShot(0, lambda: bar.setValue(idx))
				QTimer.singleShot(0, lambda: bar.setFormat(f"{idx} / {total} posted"))
	
	def _on_multi_worker_finished(self, process_id: str, instance_serial: str, result: dict[str, Any]) -> None:
		"""Handle completion of multi-media worker.
		
		Args:
			process_id: Queue process ID
			instance_serial: Target ADB serial
			result: Results dictionary from worker
		"""
		if self._is_closing:
			return
		from PySide6.QtCore import QTimer
		self._log(f"[WORKER-FINISH] ===== _on_multi_worker_finished callback =====")
		self._log(f"[WORKER-FINISH] Process ID: {process_id[:8]}")
		self._log(f"[WORKER-FINISH] Instance serial: {instance_serial}")
		self._log(f"[WORKER-FINISH] Active workers before cleanup: {list(self._workers_by_pid.keys())}")
		total = result.get("total", 0)
		success = result.get("success", 0)
		fail = result.get("fail", 0)
		cancelled = result.get("cancelled", False)
		self._log(f"[WORKER-FINISH] Results: total={total}, success={success}, fail={fail}, cancelled={cancelled}")
		if cancelled:
			self._log(f"[WORKER-FINISH] Process status: CANCELLED")
		else:
			self._log(f"[WORKER-FINISH] Process status: COMPLETED (success={success} fail={fail})")
		result_text = "Cancelled" if cancelled else f"\u2713 {success}  \u2717 {fail}"
		QTimer.singleShot(0, lambda: self._update_queue_result(process_id, result_text))
		self._log(f"[WORKER-FINISH] Notifying queue manager to release lock")
		if cancelled:
			self.queue_manager.mark_failed(process_id, "Cancelled by user")
		else:
			self.queue_manager.mark_completed(process_id, success, fail)
		self._log(f"[WORKER-FINISH] Queue manager notified")
		self._log(f"[WORKER-FINISH] Cleaning up worker and thread for {process_id[:8]}")
		self._running_serials.discard(instance_serial)
		if process_id in self._workers_by_pid:
			self._log(f"[WORKER-FINISH] Removing worker from _workers_by_pid")
			del self._workers_by_pid[process_id]
		if process_id in self._threads_by_pid:
			self._log(f"[WORKER-FINISH] Stopping and removing thread from _threads_by_pid")
			thread = self._threads_by_pid[process_id]
			thread.quit()
			thread.wait()
			del self._threads_by_pid[process_id]
		self._log(f"[WORKER-FINISH] Active workers after cleanup: {list(self._workers_by_pid.keys())}")
		self._log(f"[WORKER-FINISH] ===== Worker cleanup complete for {process_id[:8]} =====")
		if not self._running_serials:
			QTimer.singleShot(0, lambda: self.start_btn.setEnabled(True))
			QTimer.singleShot(0, lambda: self.test_btn.setEnabled(True))
			QTimer.singleShot(0, lambda: self.stop_btn.setEnabled(False))

	# ------------------------------------------------------------------
	# Input clearing and queue management
	# ------------------------------------------------------------------
	def _clear_inputs(self) -> None:
		"""Clear inputs after successful process creation (Rule 1)."""
		self.folder_input.clear()
		self.media_list.clear()
		self._media_paths.clear()
		self._update_media_counter()
		# Keep page input for convenience

	def _clear_completed_processes(self) -> None:
		"""Remove completed/failed processes from queue table."""
		rows_to_remove = []
		for row in range(self.queue_table.rowCount()):
			status_item = self.queue_table.item(row, self.COL_Q_STATUS)
			if status_item and status_item.text() in ("Completed", "Failed"):
				rows_to_remove.append(row)
		
		if not rows_to_remove:
			return
		
		reply = QMessageBox.question(
			self, "Clear Completed",
			f"Remove {len(rows_to_remove)} completed/failed process(es)?",
			QMessageBox.Yes | QMessageBox.No,
			QMessageBox.Yes,
		)
		if reply != QMessageBox.Yes:
			return
		
		# Clean up progress bar references
		for row in rows_to_remove:
			item = self.queue_table.item(row, self.COL_Q_INSTANCE)
			if item:
				self._queue_progress_bars.pop(item.data(Qt.UserRole), None)
		
		# Remove in reverse order to avoid index shifting
		for row in reversed(rows_to_remove):
			self.queue_table.removeRow(row)
		
		self.queue_manager.clear_completed()
		self._update_empty_states()

	# ------------------------------------------------------------------
	# Queue manager callbacks
	# ------------------------------------------------------------------
	def _on_queue_process_queued(self, process: ProcessSnapshot) -> None:
		"""Handle process queued - add row to queue table."""
		if self._is_closing:
			return
		from PySide6.QtCore import QTimer
		def add_row():
			row = self.queue_table.rowCount()
			self.queue_table.insertRow(row)
			page = process.page_name
			media_count = len(process.jobs)
			mode = "Immediate" if process.scheduled_at is None else "Scheduled"
			schedule_time = ""
			if process.scheduled_at:
				schedule_time = process.scheduled_at.strftime("%Y-%m-%d %H:%M")
			inst_item = QTableWidgetItem(process.instance_name)
			inst_item.setData(Qt.UserRole, process.process_id)
			self.queue_table.setItem(row, self.COL_Q_INSTANCE, inst_item)
			self.queue_table.setItem(row, self.COL_Q_PAGE, QTableWidgetItem(page))
			self.queue_table.setItem(row, self.COL_Q_MEDIA, QTableWidgetItem(str(media_count)))
			self.queue_table.setItem(row, self.COL_Q_MODE, QTableWidgetItem(mode))
			self.queue_table.setItem(row, self.COL_Q_SCHEDULE, QTableWidgetItem(schedule_time))
			status = "queued"
			status_item = QTableWidgetItem(status)
			color = STATUS_COLORS.get(status, "#333")
			status_item.setForeground(QColor(color))
			self.queue_table.setItem(row, self.COL_Q_STATUS, status_item)
			bar = QProgressBar()
			bar.setRange(0, media_count)
			bar.setValue(0)
			bar.setFormat(f"0 / {media_count} posted")
			self.queue_table.setCellWidget(row, self.COL_Q_PROGRESS, bar)
			self._queue_progress_bars[process.process_id] = bar
			self.queue_table.setItem(row, self.COL_Q_RESULT, QTableWidgetItem(""))
			self._update_empty_states()
		QTimer.singleShot(0, add_row)

	def _on_queue_status_changed(self, process_id: str, status: str) -> None:
		"""Handle process status change with color coding."""
		if self._is_closing:
			return
		from PySide6.QtCore import QTimer
		self._process_status[process_id] = status
		def update_status():
			for row in range(self.queue_table.rowCount()):
				item = self.queue_table.item(row, self.COL_Q_INSTANCE)
				if item and item.data(Qt.UserRole) == process_id:
					status_item = QTableWidgetItem(status)
					color = STATUS_COLORS.get(status, "#333")
					status_item.setForeground(QColor(color))
					self.queue_table.setItem(row, self.COL_Q_STATUS, status_item)
					break
		QTimer.singleShot(0, update_status)

	def _on_queue_process_started(self, process: ProcessSnapshot) -> None:
		"""Handle process started from queue.
		
		Creates a worker for parallel execution.
		The queue manager guarantees the instance is free at this point.
		"""
		if self._is_closing:
			return
		from PySide6.QtCore import QTimer
		self._log(f"[WORKER] ===== _on_queue_process_started callback =====")
		self._log(f"[WORKER] Process: {process.process_id[:8]}")
		self._log(f"[WORKER] Instance: {process.instance_name} (serial={process.instance_serial})")
		self._log(f"[WORKER] Starting worker for process")
		state = self.get_state_fn()
		instances = [inst for inst in state.instances if inst.adb_serial == process.instance_serial]
		if not instances:
			self._log(f"[WORKER] ✗ ERROR: Could not find instance with serial {process.instance_serial}")
			self._log(f"[WORKER] Available instances: {[(inst.name, inst.adb_serial) for inst in state.instances]}")
			self.queue_manager.mark_failed(process.process_id, f"Instance {process.instance_serial} not found")
			return
		self._log(f"[WORKER] ✓ Found instance: {instances[0].name} ({instances[0].adb_serial})")
		self._start_multi_media_worker(
			process.process_id,
			process.instance_serial,
			process.jobs,
			instances
		)
		self._log(f"[WORKER] Worker thread started for process {process.process_id[:8]}")
		QTimer.singleShot(0, lambda: self.stop_btn.setEnabled(True))
		
	def _on_queue_process_completed(self, process_id: str, success_count: int, fail_count: int) -> None:
		"""Handle process completion from queue."""
		if self._is_closing:
			return
		from PySide6.QtCore import QTimer
		self._log(f"Queue process {process_id[:8]} completed: {success_count} success, {fail_count} failed")
		QTimer.singleShot(0, lambda: self._update_queue_result(process_id, f"\u2713 {success_count}  \u2717 {fail_count}"))

	def _on_queue_process_failed(self, process_id: str, error: str) -> None:
		"""Handle process failure from queue."""
		if self._is_closing:
			return
		from PySide6.QtCore import QTimer
		self._log(f"Queue process {process_id[:8]} failed: {error}")
		QTimer.singleShot(0, lambda: self._update_queue_result(process_id, f"\u2717 {error}"))

	def _update_queue_result(self, process_id: str, text: str) -> None:
		"""Update the Result column for a queue row."""
		from PySide6.QtCore import QTimer
		def update_result():
			for row in range(self.queue_table.rowCount()):
				item = self.queue_table.item(row, self.COL_Q_INSTANCE)
				if item and item.data(Qt.UserRole) == process_id:
					self.queue_table.setItem(row, self.COL_Q_RESULT, QTableWidgetItem(text))
					break
		QTimer.singleShot(0, update_result)

	def _build_run_payload(self, test_mode: bool) -> dict[str, Any] | None:
		selected_media: list[str] = []
		for i in range(self.media_list.count()):
			item = self.media_list.item(i)
			if item.checkState() == Qt.Checked:
				selected_media.append(item.data(Qt.UserRole))

		if not selected_media:
			self._log("No media selected.")
			return None

		# Freshly resolve ADB serials for selected instances
		self._resolve_selected_serials()

		selected_instances = [inst for inst in self.get_state_fn().get_selected_instances() if inst.adb_serial]
		if not selected_instances:
			self._log(
				"No selected instance with ADB serial. "
				"Instance may be running but ADB not attached yet. "
				"Please wait a moment and click Refresh ADB on the Instances tab."
			)
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

			additional_hashtags = None
			if hasattr(self, "additional_hashtag_checkbox") and self.additional_hashtag_checkbox.isChecked():
				additional_hashtags = self.additional_hashtag_input.text().strip()

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
					use_smart_title=self.smart_title_checkbox.isChecked(),
					use_ai_retitle=self.ai_retitle_checkbox.isChecked(),
					ai_target_language=self.ai_language_combo.currentText(),
					ai_caption_cache=None,
					have_subfolder=self.use_subfolder_checkbox.isChecked(),
					subfolder_name=self.subfolder_name_input.text().strip(),
					additional_hashtags=additional_hashtags if additional_hashtags else None,
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
			get_config_fn=self.get_config_fn,
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
							moved_path = move_to_posted(original_media_path)
							log_fn and log_fn(
								f"[{instance.adb_serial}] ✓ {Path(job.media_path).name}: SUCCESS "
								f"(moved to posted: {moved_path})"
							)
						except Exception as exc:
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
								moved_path = move_to_failed(original_media_path)
								log_fn and log_fn(
									f"[{instance.adb_serial}] ✗ {Path(job.media_path).name}: FAILED - {error} "
									f"(moved to failed: {moved_path})"
								)
							except Exception as exc:
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

	def _on_worker_error(self, message: str) -> None:
		if self._is_closing:
			return
		self._log(f"Worker error: {message}")

	def _on_worker_done(self, result: Any) -> None:
		if self._is_closing:
			return

		if isinstance(result, dict) and result.get("type") == "run":
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
