"""Core Process Queue Manager - Business logic for queue management.

This manager handles:
- Queue storage and ordering
- Instance locking (per-instance serialization)
- Running process tracking
- Process lifecycle (queued -> running -> completed/failed)
- Schedule detection and triggering

UI is notified via callbacks, not signals.
Process data is immutable snapshots - UI state is never referenced.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable
import uuid
import logging

from src.core.reel_jobs import ReelJob


logger = logging.getLogger(__name__)


@dataclass
class ProcessSnapshot:
	"""Immutable snapshot of a process at enqueue time.
	
	Created once when user clicks Start, never modified.
	Workers use this snapshot - no dependency on live UI state.
	"""
	process_id: str
	instance_name: str  # e.g., "LDPlayer-17-31"
	instance_serial: str
	page_name: str
	jobs: list[ReelJob]  # Immutable job list
	post_mode: str  # "NOW" or "SCHEDULED"
	scheduled_at: datetime | None
	have_subfolder: bool
	subfolder_name: str
	created_at: datetime = field(default_factory=datetime.now)
	started_at: datetime | None = None
	finished_at: datetime | None = None


class ProcessQueueManager:
	"""Core queue manager with per-instance locking.
	
	Guarantees:
	- Only one process runs per instance at a time
	- Processes on different instances run in parallel
	- Queued processes wait for instance to become free
	- Scheduled processes wait until scheduled time + instance free
	"""

	def __init__(self, log_fn: Callable[[str], None] | None = None):
		"""Initialize queue manager.
		
		Args:
			log_fn: Optional logging callback
		"""
		self.log_fn = log_fn or (lambda msg: None)
		
		# Queue state
		self.queue: list[ProcessSnapshot] = []  # Pending processes in order
		self.running: dict[str, str] = {}  # instance_name -> process_id
		self.registry: dict[str, ProcessSnapshot] = {}  # process_id -> snapshot
		
		# Callbacks for UI notification
		self.on_process_queued: Callable[[ProcessSnapshot], None] | None = None
		self.on_process_started: Callable[[ProcessSnapshot], None] | None = None
		self.on_process_completed: Callable[[str, int, int], None] | None = None
		self.on_process_failed: Callable[[str, str], None] | None = None
		self.on_status_changed: Callable[[str, str], None] | None = None

	def enqueue(self, process: ProcessSnapshot) -> None:
		"""Add process to queue.
		
		Args:
			process: ProcessSnapshot with all immutable data
		"""
		self.queue.append(process)
		self.registry[process.process_id] = process
		
		self._log(f"[Queue] ===== Process {process.process_id[:8]} ENQUEUED =====")
		self._log(f"        Instance: {process.instance_name} (serial={process.instance_serial})")
		self._log(f"        Media: {len(process.jobs)}, Mode: {process.post_mode}")
		self._log(f"        Queue depth: {len(self.queue)}, Running: {len(self.running)}")
		self._log(f"        Running instances: {list(self.running.keys())}")
		self._log(f"[Queue] on_process_queued callback: {self.on_process_queued}")
		
		if self.on_process_queued:
			try:
				self._log(f"[Queue] Invoking on_process_queued callback...")
				self.on_process_queued(process)
				self._log(f"[Queue] ✓ on_process_queued callback completed")
			except Exception as e:
				self._log(f"[Queue] ✗ ERROR in on_process_queued: {type(e).__name__}: {e}")
				import traceback
				self._log(f"[Queue] Traceback: {traceback.format_exc()}")
		else:
			self._log(f"[Queue] ✗ on_process_queued callback is NOT set!")
		
		# Try to start immediately if instance is free
		self._log(f"[Queue] Calling try_start_next() for process {process.process_id[:8]}")
		self.try_start_next()

	def try_start_next(self) -> None:
		"""Attempt to start next queued process.
		
		Checks in order:
		1. Is queue non-empty?
		2. Is instance free?
		3. Is schedule time reached (if scheduled)?
		
		Starts first process matching all conditions.
		"""
		now = datetime.now()
		
		self._log(f"[Queue] ===== try_start_next() called =====")
		self._log(f"        Queue length: {len(self.queue)}, Running: {len(self.running)}")
		self._log(f"        Running instances: {self.running}")
		
		if not self.queue:
			self._log(f"[Queue] Queue is empty, nothing to start")
			return
		
		for idx, process in enumerate(self.queue):
			self._log(f"[Queue] Checking queue[{idx}]: {process.process_id[:8]} for instance '{process.instance_name}'")
			
			# Is instance busy?
			if process.instance_name in self.running:
				busy_pid = self.running[process.instance_name]
				self._log(f"[Queue]   ✗ Instance '{process.instance_name}' is BUSY (running process {busy_pid[:8]})")
				continue
			
			self._log(f"[Queue]   ✓ Instance '{process.instance_name}' is FREE")
			
			# Is schedule time reached?
			if process.post_mode == "SCHEDULED":
				if process.scheduled_at is None or now < process.scheduled_at:
					time_left = (process.scheduled_at - now).total_seconds() if process.scheduled_at else 0
					self._log(f"[Queue]   ✗ Waiting for schedule ({time_left:.0f}s)")
					continue
				else:
					self._log(f"[Queue]   ✓ Schedule time reached")
			
			# All conditions met - start this process
			self._log(f"[Queue]   >>> STARTING process {process.process_id[:8]} on instance '{process.instance_name}'")
			self.queue.pop(idx)
			self._start_process(process)
			self._log(f"[Queue] Queue state after start: depth={len(self.queue)}, running={list(self.running.keys())}")
			break
		else:
			self._log(f"[Queue] All queue[0..{len(self.queue)-1}] are waiting, nothing started")

	def _start_process(self, process: ProcessSnapshot) -> None:
		"""Mark process as running and lock instance.
		
		Args:
			process: The ProcessSnapshot to start
		"""
		# Lock instance
		self.running[process.instance_name] = process.process_id
		process.started_at = datetime.now()
		
		self._log(f"[Queue] ===== Process {process.process_id[:8]} STARTED =====")
		self._log(f"        Instance '{process.instance_name}' is now LOCKED")
		self._log(f"        Running processes: {list(self.running.keys())}")
		
		if self.on_process_started:
			try:
				self._log(f"[Queue] Invoking on_process_started callback...")
				self.on_process_started(process)
				self._log(f"[Queue] ✓ on_process_started callback completed")
			except Exception as e:
				self._log(f"[Queue] ✗ ERROR in on_process_started: {type(e).__name__}: {e}")
				import traceback
				self._log(f"[Queue] Traceback: {traceback.format_exc()}")
		else:
			self._log(f"[Queue] ✗ on_process_started callback is NOT set!")
		
		if self.on_status_changed:
			try:
				self._log(f"[Queue] Invoking on_status_changed callback with status='running'...")
				self.on_status_changed(process.process_id, "running")
				self._log(f"[Queue] ✓ on_status_changed callback completed")
			except Exception as e:
				self._log(f"[Queue] ✗ ERROR in on_status_changed: {type(e).__name__}: {e}")
				import traceback
				self._log(f"[Queue] Traceback: {traceback.format_exc()}")
		else:
			self._log(f"[Queue] ✗ on_status_changed callback is NOT set!")

	def mark_completed(self, process_id: str, success: int, fail: int) -> None:
		"""Mark process as completed and free instance.
		
		Args:
			process_id: Process ID
			success: Number of successful jobs
			fail: Number of failed jobs
		"""
		process = self.registry.get(process_id)
		if not process:
			self._log(f"[Queue] mark_completed() called for unknown process {process_id[:8]}")
			return
		
		# Release instance lock
		self.running.pop(process.instance_name, None)
		process.finished_at = datetime.now()
		
		self._log(f"[Queue] ===== Process {process.process_id[:8]} COMPLETED =====")
		self._log(f"        Instance '{process.instance_name}' is now UNLOCKED")
		self._log(f"        Results: {success} success, {fail} failed")
		self._log(f"        Running processes: {list(self.running.keys())}")
		
		if self.on_process_completed:
			try:
				self._log(f"[Queue] Invoking on_process_completed callback...")
				self.on_process_completed(process_id, success, fail)
				self._log(f"[Queue] ✓ on_process_completed callback completed")
			except Exception as e:
				self._log(f"[Queue] ✗ ERROR in on_process_completed: {type(e).__name__}: {e}")
				import traceback
				self._log(f"[Queue] Traceback: {traceback.format_exc()}")
		else:
			self._log(f"[Queue] ✗ on_process_completed callback is NOT set!")
		
		if self.on_status_changed:
			try:
				self._log(f"[Queue] Invoking on_status_changed callback with status='completed'...")
				self.on_status_changed(process_id, "completed")
				self._log(f"[Queue] ✓ on_status_changed callback completed")
			except Exception as e:
				self._log(f"[Queue] ✗ ERROR in on_status_changed: {type(e).__name__}: {e}")
				import traceback
				self._log(f"[Queue] Traceback: {traceback.format_exc()}")
		else:
			self._log(f"[Queue] ✗ on_status_changed callback is NOT set!")
		
		# Try to start next process
		self._log(f"[Queue] Calling try_start_next() after completion")
		self.try_start_next()

	def mark_failed(self, process_id: str, error: str) -> None:
		"""Mark process as failed and free instance.
		
		Args:
			process_id: Process ID
			error: Error message
		"""
		process = self.registry.get(process_id)
		if not process:
			self._log(f"[Queue] mark_failed() called for unknown process {process_id[:8]}")
			return
		
		# Release instance lock
		self.running.pop(process.instance_name, None)
		process.finished_at = datetime.now()
		
		self._log(f"[Queue] ===== Process {process.process_id[:8]} FAILED =====")
		self._log(f"        Instance '{process.instance_name}' is now UNLOCKED")
		self._log(f"        Error: {error}")
		self._log(f"        Running processes: {list(self.running.keys())}")
		
		if self.on_process_failed:
			try:
				self._log(f"[Queue] Invoking on_process_failed callback...")
				self.on_process_failed(process_id, error)
				self._log(f"[Queue] ✓ on_process_failed callback completed")
			except Exception as e:
				self._log(f"[Queue] ✗ ERROR in on_process_failed: {type(e).__name__}: {e}")
				import traceback
				self._log(f"[Queue] Traceback: {traceback.format_exc()}")
		else:
			self._log(f"[Queue] ✗ on_process_failed callback is NOT set!")
		
		if self.on_status_changed:
			try:
				self._log(f"[Queue] Invoking on_status_changed callback with status='failed'...")
				self.on_status_changed(process_id, "failed")
				self._log(f"[Queue] ✓ on_status_changed callback completed")
			except Exception as e:
				self._log(f"[Queue] ✗ ERROR in on_status_changed: {type(e).__name__}: {e}")
				import traceback
				self._log(f"[Queue] Traceback: {traceback.format_exc()}")
		else:
			self._log(f"[Queue] ✗ on_status_changed callback is NOT set!")
		
		# Try to start next process
		self._log(f"[Queue] Calling try_start_next() after failure")
		self.try_start_next()

	def get_process(self, process_id: str) -> ProcessSnapshot | None:
		"""Get process snapshot by ID."""
		return self.registry.get(process_id)

	def is_instance_busy(self, instance_name: str) -> bool:
		"""Check if instance has a running process."""
		return instance_name in self.running

	def get_queue_depth(self) -> int:
		"""Get number of queued processes."""
		return len(self.queue)

	def get_running_count(self) -> int:
		"""Get number of running processes."""
		return len(self.running)

	def _log(self, msg: str) -> None:
		"""Log message via callback and to logger."""
		self.log_fn(msg)
		logger.info(msg)
