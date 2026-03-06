"""Process Queue Manager for multi-instance scheduling."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable
import uuid

from PySide6.QtCore import QObject, Signal, QTimer

from src.core.reel_jobs import ReelJob


@dataclass
class ProcessInfo:
	"""Information about a process in the queue."""
	process_id: str
	instance: Any
	instance_serial: str
	instance_name: str
	page_name: str
	jobs: list[ReelJob]
	post_mode: str  # "NOW" or "SCHEDULED"
	scheduled_at: datetime | None
	status: str = "Waiting"  # Waiting, Running, Completed, Failed
	created_at: datetime = field(default_factory=datetime.now)
	started_at: datetime | None = None
	finished_at: datetime | None = None
	success_count: int = 0
	fail_count: int = 0
	error: str | None = None


class ProcessQueueManager(QObject):
	"""Manage process queue with instance locking and scheduling."""
	
	# Signals
	process_added = Signal(object)  # ProcessInfo
	process_started = Signal(str)  # process_id
	process_completed = Signal(str, int, int)  # process_id, success_count, fail_count
	process_failed = Signal(str, str)  # process_id, error
	status_changed = Signal(str, str)  # process_id, new_status
	log_message = Signal(str)
	
	def __init__(self, log_fn: Callable[[str], None] | None = None):
		super().__init__()
		self.log_fn = log_fn
		
		# Queue management
		self.processes: dict[str, ProcessInfo] = {}  # process_id -> ProcessInfo
		self.process_order: list[str] = []  # Ordered list of process IDs
		self.running_instances: dict[str, str] = {}  # instance_serial -> process_id
		self._running_serials: set[str] = set()  # Track which serials are currently running
		
		# Scheduler timer
		self.scheduler_timer = QTimer()
		self.scheduler_timer.timeout.connect(self._check_scheduled_processes)
		self.scheduler_timer.start(10000)  # Check every 10 seconds
	
	def _log(self, msg: str) -> None:
		"""Emit log message."""
		self.log_message.emit(msg)
		if self.log_fn:
			self.log_fn(msg)
	
	def validate_new_process(
		self,
		instance_serial: str,
		instance_name: str,
		selected_instances: list[Any],
		post_mode: str,
		scheduled_at: datetime | None,
	) -> tuple[bool, str]:
		"""Validate if a new process can be added.
		
		Same-instance processes are allowed and will be queued (not rejected).
		Only truly invalid requests are rejected.
		
		Returns:
			(is_valid, error_message)
		"""
		# Reject multi-instance start (one process = one instance)
		if len(selected_instances) > 1:
			return False, "You cannot start one process for multiple instances."
		
		# Same instance already running? That's fine — process will be queued.
		# No rejection here.
		
		return True, ""
	
	def add_process(
		self,
		instance: Any,
		instance_serial: str,
		instance_name: str,
		page_name: str,
		jobs: list[ReelJob],
		post_mode: str,
		scheduled_at: datetime | None = None,
	) -> str:
		"""Add a new process to the queue.
		
		Returns:
			process_id
		"""
		process_id = str(uuid.uuid4())
		
		process = ProcessInfo(
			process_id=process_id,
			instance=instance,
			instance_serial=instance_serial,
			instance_name=instance_name,
			page_name=page_name,
			jobs=jobs,
			post_mode=post_mode,
			scheduled_at=scheduled_at,
			status="Waiting",
		)
		
		self.processes[process_id] = process
		self.process_order.append(process_id)
		
		mode_str = f"scheduled ({scheduled_at.strftime('%Y-%m-%d %H:%M')})" if post_mode == "SCHEDULED" else "immediate"
		instance_busy = instance_serial in self._running_serials
		
		if instance_busy and post_mode == "SCHEDULED":
			self._log(f"[Queue] Process added to waiting queue for {instance_name} (waiting for instance + schedule)")
		elif instance_busy:
			self._log(f"[Queue] Process added to waiting queue for {instance_name} (instance busy, will start when free)")
		elif post_mode == "SCHEDULED":
			self._log(f"[Queue] Process added: instance={instance_name} medias={len(jobs)} mode={mode_str} (waiting for schedule)")
		else:
			self._log(f"[Queue] Process added: instance={instance_name} medias={len(jobs)} mode={mode_str}")
		
		self.process_added.emit(process)
		
		# Try to start immediately (dispatcher will check instance availability + schedule)
		self.start_next_available()
		
		return process_id
	
	def is_instance_busy(self, instance_serial: str) -> bool:
		"""Check if an instance serial currently has a running process."""
		return instance_serial in self._running_serials
	
	def start_next_available(self) -> None:
		"""Dispatch queued processes.

		Rules:
		- Multiple processes may run in parallel on different instance serials.
		- Only one process per instance serial at a time (per-instance serialization).
		- Scheduled processes also require current time >= scheduled_at.
		"""
		for process_id in self.process_order:
			process = self.processes.get(process_id)
			if not process:
				continue
			
			# Skip if not waiting
			if process.status != "Waiting":
				continue
			
			# Condition 1: instance must be free
			if process.instance_serial in self._running_serials:
				continue
			
			# Condition 2: if scheduled, time must have been reached
			if process.post_mode == "SCHEDULED":
				if not process.scheduled_at or datetime.now() < process.scheduled_at:
					continue
			
			# Both conditions met — start this process
			self._log(f"[Queue] Process {process.process_id[:8]} starting on {process.instance_name}")
			self._start_process(process_id)
	
	def _start_process(self, process_id: str) -> None:
		"""Mark process as running and lock instance."""
		process = self.processes.get(process_id)
		if not process:
			return
		
		# Lock instance by serial
		self.running_instances[process.instance_serial] = process_id
		self._running_serials.add(process.instance_serial)
		
		# Update status
		process.status = "Running"
		process.started_at = datetime.now()
		
		self._log(f"[Queue] Process started: {process.instance_name}")
		self.process_started.emit(process_id)
		self.status_changed.emit(process_id, "Running")
	
	def mark_process_complete(
		self,
		process_id: str,
		success_count: int,
		fail_count: int,
	) -> None:
		"""Mark process as completed and free instance."""
		process = self.processes.get(process_id)
		if not process:
			return
		
		# Update process
		process.status = "Completed"
		process.finished_at = datetime.now()
		process.success_count = success_count
		process.fail_count = fail_count
		
		# Free instance
		if process.instance_serial in self.running_instances:
			del self.running_instances[process.instance_serial]
		self._running_serials.discard(process.instance_serial)
		
		self._log(
			f"[Queue] Process finished: {process.instance_name} "
			f"success={success_count} fail={fail_count}"
		)
		
		self.process_completed.emit(process_id, success_count, fail_count)
		self.status_changed.emit(process_id, "Completed")
		
		# Try to start next process
		self.start_next_available()
	
	def mark_process_failed(self, process_id: str, error: str) -> None:
		"""Mark process as failed and free instance."""
		process = self.processes.get(process_id)
		if not process:
			return
		
		# Update process
		process.status = "Failed"
		process.finished_at = datetime.now()
		process.error = error
		
		# Free instance
		if process.instance_serial in self.running_instances:
			del self.running_instances[process.instance_serial]
		self._running_serials.discard(process.instance_serial)
		
		self._log(f"[Queue] Process failed: {process.instance_name} error={error}")
		
		self.process_failed.emit(process_id, error)
		self.status_changed.emit(process_id, "Failed")
		
		# Try to start next process
		self.start_next_available()
	
	def _check_scheduled_processes(self) -> None:
		"""Check if any scheduled processes are ready to start."""
		self.start_next_available()
	
	def get_process(self, process_id: str) -> ProcessInfo | None:
		"""Get process info by ID."""
		return self.processes.get(process_id)
	
	def get_all_processes(self) -> list[ProcessInfo]:
		"""Get all processes in order."""
		return [self.processes[pid] for pid in self.process_order if pid in self.processes]
	
	def is_instance_running(self, instance_serial: str) -> bool:
		"""Check if instance is currently running a process."""
		return instance_serial in self.running_instances
	
	def clear_completed(self) -> None:
		"""Remove completed/failed processes from queue."""
		to_remove = []
		for process_id in self.process_order:
			process = self.processes.get(process_id)
			if process and process.status in ("Completed", "Failed"):
				to_remove.append(process_id)
		
		for process_id in to_remove:
			self.process_order.remove(process_id)
			del self.processes[process_id]
		
		if to_remove:
			self._log(f"[Queue] Cleared {len(to_remove)} completed/failed process(es)")
