"""Multi-media sequential Reel poster worker for QThread execution."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from src.core.media_manager import move_to_posted, move_to_failed
from src.core.reel_jobs import ReelJob
from src.core.reel_poster import ReelPoster


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


@dataclass
class PostResult:
	"""Result of posting a single media."""
	media_path: str
	success: bool
	error: str | None = None
	instance_name: str = ""


class MultiReelPosterWorker(QObject):
	"""Sequential multi-media Reel poster with 10-second delays between posts.
	
	Emits signals for logging and completion.
	Can be cancelled via cancel_requested flag.
	"""
	
	# Signals for communication with UI thread
	log_message = Signal(str)  # Emit log messages
	progress = Signal(int, int, str, str)  # (current_idx, total, media_name, status)
	finished = Signal(str, str, dict)  # (process_id, serial, results_dict)
	
	def __init__(
		self,
		process_id: str,
		instance_serial: str,
		jobs: list[ReelJob],
		instances: list[Any],
		adb_manager: Any,
		get_adbkeyboard_request_fn: Callable[[str], Any],
		log_fn: Callable[[str], None] | None = None,
	):
		"""Initialize the worker.
		
		Args:
			process_id: Queue process ID
			instance_serial: Target ADB serial (e.g., 'emulator-5574')
			jobs: List of ReelJob objects in order
			instances: List of ADB instances
			adb_manager: ADB manager instance
			get_adbkeyboard_request_fn: Callback for ADBKeyboard requests
			log_fn: Optional logging function (in addition to signals)
		"""
		super().__init__()
		self.process_id = process_id
		self.instance_serial = instance_serial
		self.jobs = jobs
		self.instances = instances
		self.adb_manager = adb_manager
		self.get_adbkeyboard_request_fn = get_adbkeyboard_request_fn
		self.log_fn = log_fn
		self.cancel_requested = False
		
	def _log(self, msg: str) -> None:
		"""Emit log message and call log function if provided."""
		self.log_message.emit(msg)
		if self.log_fn:
			self.log_fn(msg)
	
	def cancel(self) -> None:
		"""Request cancellation of the posting loop."""
		self.cancel_requested = True
	
	def run(self) -> None:
		"""Execute sequential posting of all jobs."""
		total = len(self.jobs)
		results: list[PostResult] = []
		instance_idx = 0
		
		self._log(f"Starting multi-media posting: {total} media(s)")
		self._log("=" * 70)
		
		for idx, job in enumerate(self.jobs, start=1):
			if self.cancel_requested:
				self._log(f"[{idx}/{total}] Cancelled by user")
				break
			
			media_name = Path(job.media_path).name
			instance = self.instances[instance_idx % len(self.instances)]
			instance_idx += 1
			
			self._log(f"[{idx}/{total}] Starting: {media_name}")
			self.progress.emit(idx, total, media_name, "RUNNING")
			
			try:
				# Create poster and run single job
				poster = ReelPoster(
					self.adb_manager,
					self._log,
					skip_push_media=True,
					fallback_push_if_missing=False,
					get_adbkeyboard_request_fn=self.get_adbkeyboard_request_fn,
				)
				
				device_media_path = f"/sdcard/shared/Pictures/{media_name}"
				success, error = poster.run(instance.adb_serial, job, device_media_path)
				
				if success:
					self._log(f"[{idx}/{total}] ✓ {media_name}")
					self.progress.emit(idx, total, media_name, "SUCCESS")
					
					# Move to posted folder
					try:
						move_to_posted(job.media_path)
					except Exception as exc:
						self._log(f"[{idx}/{total}] ⚠ Success but move to posted/ failed: {exc}")
					
					results.append(PostResult(
						media_path=job.media_path,
						success=True,
						instance_name=getattr(instance, "name", instance.adb_serial),
					))
				else:
					self._log(f"[{idx}/{total}] ✗ {media_name} | Error: {error}")
					self.progress.emit(idx, total, media_name, "FAILED")
					
					if _is_adbkeyboard_setup_error(error):
						self._log(
							f"[{idx}/{total}] ⚠ Kept in place due to ADBKeyboard setup issue: {media_name}"
						)
					else:
						# Move to failed folder
						try:
							move_to_failed(job.media_path)
						except Exception as exc:
							self._log(f"[{idx}/{total}] ⚠ Failed move to failed/ folder: {exc}")
					
					results.append(PostResult(
						media_path=job.media_path,
						success=False,
						error=error,
						instance_name=getattr(instance, "name", instance.adb_serial),
					))
			
			except Exception as exc:
				error_msg = str(exc)
				self._log(f"[{idx}/{total}] ✗ Exception: {media_name} | {error_msg}")
				self.progress.emit(idx, total, media_name, "ERROR")
				
				if _is_adbkeyboard_setup_error(error_msg):
					self._log(
						f"[{idx}/{total}] ⚠ Kept in place due to ADBKeyboard setup issue: {media_name}"
					)
				else:
					try:
						move_to_failed(job.media_path)
					except Exception:
						pass
				
				results.append(PostResult(
					media_path=job.media_path,
					success=False,
					error=error_msg,
					instance_name=getattr(self.instances[0], "name", self.instances[0].adb_serial),
				))
			
			# Wait before next post (except after last one)
			if idx < total and not self.cancel_requested:
				self._log(f"Waiting 10 seconds before next post...")
				for remaining in range(10, 0, -1):
					if self.cancel_requested:
						break
					time.sleep(1)
					if remaining % 5 == 0 or remaining == 1:
						self._log(f"  {remaining}s remaining...")
		
		# Generate summary
		self._log("=" * 70)
		success_count = sum(1 for r in results if r.success)
		fail_count = len(results) - success_count
		
		self._log(f"Multi-post finished: total={total} success={success_count} fail={fail_count}")
		
		if fail_count > 0:
			self._log("Failed items:")
			for r in results:
				if not r.success:
					self._log(f"  - {Path(r.media_path).name}: {r.error}")
		
		# Emit finished signal with results
		result_dict = {
			"total": total,
			"success": success_count,
			"fail": fail_count,
			"cancelled": self.cancel_requested,
			"results": results,
		}
		self.finished.emit(self.process_id, self.instance_serial, result_dict)
