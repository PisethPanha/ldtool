"""Caption mapping manager for Facebook Reels posting.

Stores and retrieves caption mappings with labels for ordered posting.
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import TypedDict


class CaptionEntry(TypedDict):
	"""Single caption mapping entry."""
	media_path: str
	caption: str
	label: int


class CaptionMapper:
	"""Manages caption mappings with JSON persistence."""

	def __init__(self, persist_path: str | Path = "./caption_mapping.json"):
		self._persist_path = Path(persist_path)
		self._lock = Lock()
		self._entries: list[CaptionEntry] = []
		self._load()

	def clear(self) -> None:
		"""Clear all caption mappings."""
		with self._lock:
			self._entries.clear()
			self._save()

	def add_or_update(self, media_path: str, caption: str, label: int) -> None:
		"""Add or update a caption mapping entry."""
		with self._lock:
			# Remove existing entry for this media_path if it exists
			self._entries = [e for e in self._entries if e["media_path"] != media_path]
			# Add new entry
			self._entries.append({
				"media_path": media_path,
				"caption": caption,
				"label": label,
			})
			# Sort by label to maintain order
			self._entries.sort(key=lambda e: e["label"])
			self._save()

	def add_batch(self, entries: list[tuple[str, str, int]]) -> None:
		"""Add multiple entries at once.
		
		Args:
			entries: List of (media_path, caption, label) tuples
		"""
		with self._lock:
			# Clear existing entries for these media paths
			new_media_paths = {e[0] for e in entries}
			self._entries = [e for e in self._entries if e["media_path"] not in new_media_paths]
			
			# Add new entries
			for media_path, caption, label in entries:
				self._entries.append({
					"media_path": media_path,
					"caption": caption,
					"label": label,
				})
			
			# Sort by label
			self._entries.sort(key=lambda e: e["label"])
			self._save()

	def get_caption(self, media_path: str) -> str | None:
		"""Get caption for a media file.
		
		Args:
			media_path: Full path to media file
			
		Returns:
			Caption string if found, None otherwise
		"""
		with self._lock:
			for entry in self._entries:
				if entry["media_path"] == media_path:
					return entry["caption"]
		return None

	def get_label(self, media_path: str) -> int | None:
		"""Get label for a media file.
		
		Args:
			media_path: Full path to media file
			
		Returns:
			Label integer if found, None otherwise
		"""
		with self._lock:
			for entry in self._entries:
				if entry["media_path"] == media_path:
					return entry["label"]
		return None

	def get_all_entries(self) -> list[CaptionEntry]:
		"""Get all caption mapping entries sorted by label."""
		with self._lock:
			return list(self._entries)

	def remove(self, media_path: str) -> bool:
		"""Remove a caption mapping entry.
		
		Args:
			media_path: Full path to media file
			
		Returns:
			True if entry was removed, False if not found
		"""
		with self._lock:
			original_len = len(self._entries)
			self._entries = [e for e in self._entries if e["media_path"] != media_path]
			if len(self._entries) < original_len:
				self._save()
				return True
		return False

	def _load(self) -> None:
		"""Load caption mappings from JSON file."""
		if not self._persist_path.exists():
			return

		try:
			data = json.loads(self._persist_path.read_text(encoding="utf-8"))
			if not isinstance(data, dict):
				return

			entries_raw = data.get("captions", [])
			if not isinstance(entries_raw, list):
				return

			loaded: list[CaptionEntry] = []
			for item in entries_raw:
				if not isinstance(item, dict):
					continue
				
				media_path = item.get("media_path", "")
				caption = item.get("caption", "")
				label = item.get("label", 0)
				
				if media_path and isinstance(label, int):
					loaded.append({
						"media_path": str(media_path),
						"caption": str(caption),
						"label": int(label),
					})

			self._entries = sorted(loaded, key=lambda e: e["label"])
		except Exception:
			# Safe-load policy: ignore malformed files
			pass

	def _save(self) -> None:
		"""Save caption mappings to JSON file."""
		if not self._persist_path:
			return

		try:
			data = {"captions": self._entries}
			self._persist_path.write_text(
				json.dumps(data, indent=2, ensure_ascii=False),
				encoding="utf-8"
			)
		except Exception:
			# Safe-save policy: ignore write errors
			pass
