from __future__ import annotations

from pathlib import Path
from typing import Iterable


VIDEO_EXTENSIONS: tuple[str, ...] = (
	".mp4",
	".mov",
	".m4v",
	".avi",
	".mkv",
	".webm",
)


def scan_media(folder: str) -> list[str]:
	"""Scan a folder and return video file paths.

	Only files with extensions listed in ``VIDEO_EXTENSIONS`` are included.
	Returned paths are absolute and sorted for deterministic processing.
	"""
	root = Path(folder)
	if not root.exists() or not root.is_dir():
		return []

	files: list[Path] = [
		path
		for path in root.iterdir()
		if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
	]
	files.sort(key=lambda p: p.name.lower())
	return [str(path.resolve()) for path in files]


def caption_from_filename(path: str) -> str:
	"""Build a caption from a file name.

	Removes extension and replaces ``_`` and ``-`` with spaces.
	"""
	name = Path(path).stem
	caption = name.replace("_", " ").replace("-", " ")
	return caption.strip()


def ensure_subfolders(folder: str) -> None:
	"""Ensure workflow subfolders exist under ``folder``.

	Creates:
	- processing/
	- posted/
	- failed/
	"""
	root = Path(folder)
	(root / "processing").mkdir(parents=True, exist_ok=True)
	(root / "posted").mkdir(parents=True, exist_ok=True)
	(root / "failed").mkdir(parents=True, exist_ok=True)


def copy_to_processing(path: str, job_id: str = "default") -> str:
	"""Copy a media file into sibling ``processing/<job_id>/`` folder.
	
	This creates a working copy while keeping the original file intact.
	Name collisions are resolved by appending ``_1``, ``_2``, etc.
	Returns the new absolute path of the copy.
	"""
	src = Path(path)
	if not src.exists() or not src.is_file():
		raise FileNotFoundError(f"Source file not found: {src}")
	
	base = src.parent
	ensure_subfolders(str(base))
	dst_dir = base / "processing" / job_id
	dst_dir.mkdir(parents=True, exist_ok=True)
	
	dst = _next_available_name(dst_dir, src.name)
	
	# Copy instead of move - preserve original
	import shutil
	shutil.copy2(src, dst)
	return str(dst.resolve())


def move_to_processing(path: str) -> str:
	"""DEPRECATED: Use copy_to_processing() instead to preserve originals.
	
	Move a media file into sibling ``processing/`` folder.
	Name collisions are resolved by appending ``_1``, ``_2``, etc.
	Returns the new absolute path.
	"""
	src = Path(path)
	base = src.parent
	ensure_subfolders(str(base))
	dst_dir = base / "processing"
	return str(_move_atomic_with_suffix(src, dst_dir).resolve())


def move_to_posted(media_path: str) -> str:
	"""Move a media file into ``posted/`` folder.
	
	Works with both processing paths and original paths.
	Name collisions are resolved by appending ``_1``, ``_2``, etc.
	Returns the new absolute path.
	"""
	src = Path(media_path)
	# Determine root: if in processing/, go up two levels; otherwise use parent
	if src.parent.name == "processing" or (src.parent.parent.exists() and src.parent.parent.name == "processing"):
		# Handle both processing/ and processing/<job_id>/
		root = src.parent.parent if src.parent.name == "processing" else src.parent.parent.parent
	else:
		root = src.parent
	
	ensure_subfolders(str(root))
	dst_dir = root / "posted"
	return str(_move_atomic_with_suffix(src, dst_dir).resolve())


def move_to_failed(media_path: str) -> str:
	"""Move a media file into ``failed/`` folder.
	
	Works with both processing paths and original paths.
	Name collisions are resolved by appending ``_1``, ``_2``, etc.
	Returns the new absolute path.
	"""
	src = Path(media_path)
	# Determine root: if in processing/, go up two levels; otherwise use parent
	if src.parent.name == "processing" or (src.parent.parent.exists() and src.parent.parent.name == "processing"):
		# Handle both processing/ and processing/<job_id>/
		root = src.parent.parent if src.parent.name == "processing" else src.parent.parent.parent
	else:
		root = src.parent
	
	ensure_subfolders(str(root))
	dst_dir = root / "failed"
	return str(_move_atomic_with_suffix(src, dst_dir).resolve())


def _move_atomic_with_suffix(src: Path, dst_dir: Path) -> Path:
	"""Move ``src`` to ``dst_dir`` using an atomic rename when possible.

	If destination file exists, append an incrementing numeric suffix.
	"""
	if not src.exists() or not src.is_file():
		raise FileNotFoundError(f"Source file not found: {src}")

	dst_dir.mkdir(parents=True, exist_ok=True)
	dst = _next_available_name(dst_dir, src.name)

	# Path.replace performs an atomic rename on same filesystem.
	# It overwrites existing files, but we guarantee non-existing destination.
	src.replace(dst)
	return dst


def _next_available_name(dst_dir: Path, filename: str) -> Path:
	"""Return first available file path in ``dst_dir`` for ``filename``.

	If ``filename`` exists, generate:
	- stem_1.ext
	- stem_2.ext
	- ...
	"""
	candidate = dst_dir / filename
	if not candidate.exists():
		return candidate

	stem = candidate.stem
	suffix = candidate.suffix
	counter = 1
	while True:
		candidate = dst_dir / f"{stem}_{counter}{suffix}"
		if not candidate.exists():
			return candidate
		counter += 1
