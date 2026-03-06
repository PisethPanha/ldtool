from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
from threading import Lock
from typing import Iterable, Literal, Optional


PostMode = Literal["NOW", "SCHEDULED"]


@dataclass
class ReelJob:
	id: str
	media_path: str
	caption: str
	target_page: str
	post_mode: PostMode
	scheduled_at: datetime | None
	status: str
	attempts: int
	max_attempts: int
	last_error: str | None
	label: int = 0  # For ordered posting


class ReelJobQueue:
	"""In-memory queue for reel jobs with optional JSON persistence."""

	def __init__(self, persist_path: str | Path | None = "./jobs.json"):
		self._jobs: list[ReelJob] = []
		self._persist_path = Path(persist_path) if persist_path else None
		self._lock = Lock()
		self._load()

	# ------------------------------------------------------------------
	# Queue operations
	# ------------------------------------------------------------------
	def add_job(self, job: ReelJob) -> None:
		with self._lock:
			self._jobs.append(job)
			self._save()

	def add_jobs(self, jobs: Iterable[ReelJob]) -> None:
		with self._lock:
			self._jobs.extend(jobs)
			self._save()

	def pop_next_ready(self, now: datetime) -> ReelJob | None:
		"""Return and remove the next ready job from pending queue.

		Ready means:
		- post_mode == "NOW"
		- OR post_mode == "SCHEDULED" and scheduled_at <= now

		Only jobs with status "PENDING" are considered.
		"""
		with self._lock:
			for index, job in enumerate(self._jobs):
				if job.status != "PENDING":
					continue
				if self._is_ready(job, now):
					picked = self._jobs.pop(index)
					self._save()
					return picked
		return None

	def mark_running(self, job: ReelJob) -> None:
		with self._lock:
			job.status = "RUNNING"
			self._save()

	def mark_success(self, job: ReelJob) -> None:
		with self._lock:
			job.status = "SUCCESS"
			job.last_error = None
			self._save()

	def mark_failed(self, job: ReelJob, error: str) -> None:
		with self._lock:
			job.attempts += 1
			job.last_error = error
			if self.should_retry(job):
				job.status = "PENDING"
				# push back into queue for retry if not currently queued
				if all(existing.id != job.id for existing in self._jobs):
					self._jobs.append(job)
			else:
				job.status = "FAILED"
			self._save()

	def should_retry(self, job: ReelJob) -> bool:
		return job.attempts < job.max_attempts

	# ------------------------------------------------------------------
	# Persistence
	# ------------------------------------------------------------------
	def _load(self) -> None:
		if not self._persist_path:
			return
		if not self._persist_path.exists():
			return

		try:
			raw = json.loads(self._persist_path.read_text(encoding="utf-8"))
			if not isinstance(raw, list):
				return

			loaded: list[ReelJob] = []
			for item in raw:
				if not isinstance(item, dict):
					continue

				scheduled_at_raw = item.get("scheduled_at")
				scheduled_at: Optional[datetime]
				if isinstance(scheduled_at_raw, str) and scheduled_at_raw:
					try:
						scheduled_at = datetime.fromisoformat(scheduled_at_raw)
					except ValueError:
						scheduled_at = None
				else:
					scheduled_at = None

				post_mode = item.get("post_mode", "NOW")
				if post_mode not in ("NOW", "SCHEDULED"):
					post_mode = "NOW"

				loaded.append(
					ReelJob(
						id=str(item.get("id", "")),
						media_path=str(item.get("media_path", "")),
						caption=str(item.get("caption", "")),
						target_page=str(item.get("target_page", "")),
						post_mode=post_mode,
						scheduled_at=scheduled_at,
						status=str(item.get("status", "PENDING")),
						attempts=int(item.get("attempts", 0) or 0),
						max_attempts=max(1, int(item.get("max_attempts", 1) or 1)),
						last_error=(
							str(item.get("last_error"))
							if item.get("last_error") is not None
							else None
						),
						label=int(item.get("label", 0) or 0),
					)
				)

			self._jobs = loaded
		except Exception:
			# safe-load policy: ignore malformed files
			self._jobs = []

	def _save(self) -> None:
		if not self._persist_path:
			return

		try:
			payload: list[dict] = []
			for job in self._jobs:
				item = asdict(job)
				item["scheduled_at"] = (
					job.scheduled_at.isoformat() if job.scheduled_at else None
				)
				payload.append(item)

			self._persist_path.parent.mkdir(parents=True, exist_ok=True)
			temp_path = self._persist_path.with_suffix(self._persist_path.suffix + ".tmp")
			temp_path.write_text(
				json.dumps(payload, ensure_ascii=False, indent=2),
				encoding="utf-8",
			)
			temp_path.replace(self._persist_path)
		except Exception:
			# safe-save policy: suppress persistence failures
			pass

	# ------------------------------------------------------------------
	# Internals
	# ------------------------------------------------------------------
	@staticmethod
	def _is_ready(job: ReelJob, now: datetime) -> bool:
		if job.post_mode == "NOW":
			return True
		if job.post_mode == "SCHEDULED" and job.scheduled_at is not None:
			return job.scheduled_at <= now
		return False
