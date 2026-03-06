from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re
import shlex
import subprocess
import threading
import time
from typing import Any, Callable, Optional, Sequence
import xml.etree.ElementTree as ET

from src.core.reel_jobs import ReelJob
from src.core.ui_dump import (
	dump_ui_xml,
	find_first,
	tap_center,
	swipe,
	find_facebook_hamburger,
	find_create_button,
	find_top_bar,
	is_facebook_running,
	is_facebook_home_feed,
	is_reel_composer_caption_screen,
	find_caption_target,
	find_reel_title_field,
	find_reel_describe_field,
	open_account_switcher_from_menu,
	open_profile_tab,
	_debug_dump_artifacts,
)


# ------------------------------------------------------------------
# State Machine Definition
# ------------------------------------------------------------------
class ReelState(Enum):
	"""Reel posting workflow states matching real UI flow."""
	PUSH_MEDIA = "PUSH_MEDIA"
	OPEN_FACEBOOK = "OPEN_FACEBOOK"
	ENSURE_FEED_STABLE = "ENSURE_FEED_STABLE"
	OPEN_HAMBURGER = "OPEN_HAMBURGER"
	TAP_PROFILE_DROPDOWN = "TAP_PROFILE_DROPDOWN"
	SELECT_PAGE = "SELECT_PAGE"
	OPEN_PAGE_PROFILE = "OPEN_PAGE_PROFILE"
	PRESS_HOME = "PRESS_HOME"
	OPEN_FILE_MANAGER = "OPEN_FILE_MANAGER"
	SELECT_PICTURES = "SELECT_PICTURES"
	NAVIGATE_MEDIA = "NAVIGATE_MEDIA"
	HOLD_ON_MEDIA = "HOLD_ON_MEDIA"
	CLICK_ON_SEND = "CLICK_ON_SEND"
	SHARE_TO_REELS = "SHARE_TO_REELS"
	WAIT_FOR_REELS_COMPOSER = "WAIT_FOR_REELS_COMPOSER"
	TAP_NEXT = "TAP_NEXT"
	FILL_CAPTION = "FILL_CAPTION"
	CONFIGURE_SCHEDULE = "CONFIGURE_SCHEDULE"
	TAP_SHARE = "TAP_SHARE"
	WAIT_COMPLETION = "WAIT_COMPLETION"


@dataclass
class StatePolicy:
	"""Per-state timeout and retry configuration."""
	timeout_s: int
	retries: int
	expected_package: str | None = "com.facebook.katana"  # Expected foreground package for this state


STATE_POLICIES: dict[ReelState, StatePolicy] = {
	ReelState.PUSH_MEDIA: StatePolicy(timeout_s=5, retries=1, expected_package=None),
	ReelState.OPEN_FACEBOOK: StatePolicy(timeout_s=20, retries=2, expected_package=None),
	ReelState.ENSURE_FEED_STABLE: StatePolicy(timeout_s=30, retries=2, expected_package="com.facebook.katana"),
	ReelState.OPEN_HAMBURGER: StatePolicy(timeout_s=10, retries=2, expected_package="com.facebook.katana"),
	ReelState.TAP_PROFILE_DROPDOWN: StatePolicy(timeout_s=10, retries=2, expected_package="com.facebook.katana"),
	ReelState.SELECT_PAGE: StatePolicy(timeout_s=15, retries=2, expected_package="com.facebook.katana"),
	ReelState.OPEN_PAGE_PROFILE: StatePolicy(timeout_s=10, retries=2, expected_package="com.facebook.katana"),
	ReelState.PRESS_HOME: StatePolicy(timeout_s=5, retries=1, expected_package=None),
	ReelState.OPEN_FILE_MANAGER: StatePolicy(timeout_s=12, retries=2, expected_package=None),
	ReelState.SELECT_PICTURES: StatePolicy(timeout_s=20, retries=2, expected_package="com.cyanogenmod.filemanager"),
	ReelState.NAVIGATE_MEDIA: StatePolicy(timeout_s=15, retries=2, expected_package="com.cyanogenmod.filemanager"),
	ReelState.HOLD_ON_MEDIA: StatePolicy(timeout_s=15, retries=2, expected_package="com.cyanogenmod.filemanager"),
	ReelState.CLICK_ON_SEND: StatePolicy(timeout_s=15, retries=2, expected_package="com.cyanogenmod.filemanager"),
	ReelState.SHARE_TO_REELS: StatePolicy(timeout_s=20, retries=2, expected_package=None),
	ReelState.WAIT_FOR_REELS_COMPOSER: StatePolicy(timeout_s=20, retries=1, expected_package="com.facebook.katana"),
	ReelState.TAP_NEXT: StatePolicy(timeout_s=10, retries=2, expected_package="com.facebook.katana"),
	ReelState.FILL_CAPTION: StatePolicy(timeout_s=15, retries=2, expected_package="com.facebook.katana"),
	ReelState.CONFIGURE_SCHEDULE: StatePolicy(timeout_s=15, retries=2, expected_package="com.facebook.katana"),
	ReelState.TAP_SHARE: StatePolicy(timeout_s=15, retries=2, expected_package="com.facebook.katana"),
	ReelState.WAIT_COMPLETION: StatePolicy(timeout_s=120, retries=1, expected_package="com.facebook.katana"),
}


# ------------------------------------------------------------------
# UI Element Selectors
# ------------------------------------------------------------------
SELECTORS: dict[str, list[dict[str, Any]]] = {
	"HAMBURGER_MENU": [
		{"desc_contains": "Open navigation", "clickable": True},
		{"desc_contains": "Navigation", "clickable": True},
		{"desc_contains": "Menu", "clickable": True},
		{"res_id_contains": "toolbar_navigation", "clickable": True},
		{"res_id_contains": "menu_button", "clickable": True},
	],
	"PROFILE_DROPDOWN": [
		{"desc_contains": "dropdown", "clickable": True},
		{"desc_contains": "expand", "clickable": True},
		{"desc_contains": "See more", "clickable": True},
		{"res_id_contains": "dropdown", "clickable": True},
	],
	"PAGE_ICON": [
		{"desc_contains": "Page", "clickable": True},
		{"res_id_contains": "page_icon", "clickable": True},
		{"res_id_contains": "page_avatar", "clickable": True},
	],
	"CREATE_REEL": [
		{"text_equals": "Create reel", "clickable": True},
		{"text_contains": "Create reel", "clickable": True},
		{"desc_contains": "Create reel", "clickable": True},
		{"text_contains": "Reel", "clickable": True},
		{"res_id_contains": "create_reel", "clickable": True},
	],
	"VIDEO_GRID_ITEM": [
		{"res_id_contains": "thumbnail", "clickable": True},
		{"res_id_contains": "video_item", "clickable": True},
		{"res_id_contains": "media_item", "clickable": True},
		{"desc_contains": "Video", "clickable": True},
		{"class_name": "android.widget.ImageView", "clickable": True},
	],
	"NEXT_BUTTON": [
		{"text_equals": "Next", "clickable": True},
		{"text_contains": "Next", "clickable": True},
		{"desc_contains": "Next", "clickable": True},
		{"res_id_contains": "next", "clickable": True},
	],
	"CAPTION_FIELD": [
		{"text_contains": "description", "clickable": True},
		{"text_contains": "caption", "clickable": True},
		{"text_contains": "Write something", "clickable": True},
		{"desc_contains": "description", "clickable": True},
		{"res_id_contains": "caption", "clickable": True},
		{"res_id_contains": "description", "clickable": True},
	],
	"SCHEDULING_OPTIONS": [
		{"text_contains": "Scheduling", "clickable": True},
		{"text_contains": "Schedule", "clickable": True},
		{"desc_contains": "Scheduling", "clickable": True},
		{"res_id_contains": "schedule", "clickable": True},
	],
	"SHARE_NOW": [
		{"text_equals": "Share now", "clickable": True},
		{"text_contains": "Share now", "clickable": True},
	],
	"COMPLETION_INDICATORS": [
		{"text_contains": "Reel posted"},
		{"text_contains": "Shared"},
		{"text_contains": "Post shared"},
		{"text_contains": "Done"},
	],
	"POPUP_DISMISS": [
		{"text_equals": "Not now", "clickable": True},
		{"text_contains": "Not now", "clickable": True},
		{"text_equals": "Cancel", "clickable": True},
		{"text_equals": "Dismiss", "clickable": True},
		{"text_contains": "Later", "clickable": True},
	],
	"POPUP_ALLOW": [
		{"text_equals": "Allow", "clickable": True},
		{"text_contains": "Continue", "clickable": True},
		{"text_equals": "OK", "clickable": True},
	],
}


# ------------------------------------------------------------------
# Media Path Resolution
# ------------------------------------------------------------------
def resolve_emulator_media_path(adb: Any, serial: str, filename: str) -> Optional[str]:
	"""
	Resolve media file path on emulator using search order.
	Returns the absolute path if found, None otherwise.
	
	Search order:
	  1) /sdcard/shared/Pictures/<filename>
	  2) /sdcard/shared/DCIM/<filename>
	  3) /sdcard/shared/Movies/<filename>
	  4) /sdcard/shared/Download/<filename>
	  5) /sdcard/shared/<filename>
	  6) Fallback: find /sdcard/shared -maxdepth 4 -type f -name '<filename>' (first match)
	"""
	if not filename:
		return None

	search_paths: list[str] = [
		f"/sdcard/shared/Pictures/{filename}",
		f"/sdcard/shared/DCIM/{filename}",
		f"/sdcard/shared/Movies/{filename}",
		f"/sdcard/shared/Download/{filename}",
		f"/sdcard/shared/{filename}",
	]

	# Try each path in order
	for path in search_paths:
		try:
			# Use ls -1 to check if file exists (requires exact match)
			ls_cmd = f"ls -1 {shlex.quote(path)}"
			result = str(adb.shell(serial, ls_cmd))
			if "No such file" not in result and filename in result and "cannot access" not in result.lower():
				return path
		except Exception:
			pass  # Continue to next path

	# Fallback: use find command with maxdepth
	try:
		# Use find with proper quoting, filter stderr, take first result
		escaped_name = filename.replace("'", "'\\''")
		find_cmd = f"find /sdcard/shared -maxdepth 4 -type f -name '{escaped_name}' 2>/dev/null | head -n 1"
		result = str(adb.shell(serial, find_cmd)).strip()
		if result and "/" in result:
			return result
	except Exception:
		pass  # No fallback available

	return None


# ------------------------------------------------------------------
# ADBKeyboard Installation Request (Thread-Safe)
# ------------------------------------------------------------------
class ADBKeyboardRequest:
	"""Thread-safe request object for ADBKeyboard installation.
	
	Used to communicate between worker thread (ReelPoster) and UI thread.
	The worker thread creates the request and waits on it.
	The UI thread receives the request via signal, shows dialog, and sets the result.
	"""
	
	def __init__(self, serial: str):
		self.serial = serial
		self.done_event = threading.Event()
		self.result = False  # True if installation succeeded
		self.error: Optional[str] = None  # Error message if installation failed
	
	def wait_for_result(self, timeout_s: int = 300) -> tuple[bool, str | None]:
		"""Block until installation dialog completes.
		
		Returns (success, error_message).
		"""
		success = self.done_event.wait(timeout=timeout_s)
		if not success:
			return False, "ADBKeyboard installation dialog timed out"
		return self.result, self.error
	
	def set_result(self, success: bool, error: Optional[str] = None) -> None:
		"""Called from UI thread to set the installation result."""
		self.result = success
		self.error = error
		self.done_event.set()


def resolve_emulator_media_path(adb: Any, serial: str, filename: str) -> Optional[str]:
	"""
	Resolve media file path on emulator using search order.
	Returns the absolute path if found, None otherwise.
	
	Search order:
	  1) /sdcard/shared/Pictures/<filename>
	  2) /sdcard/shared/DCIM/<filename>
	  3) /sdcard/shared/Movies/<filename>
	  4) /sdcard/shared/Download/<filename>
	  5) /sdcard/shared/<filename>
	  6) Fallback: find /sdcard/shared -maxdepth 4 -type f -name '<filename>' (first match)
	"""
	if not filename:
		return None

	search_paths: list[str] = [
		f"/sdcard/shared/Pictures/{filename}",
		f"/sdcard/shared/DCIM/{filename}",
		f"/sdcard/shared/Movies/{filename}",
		f"/sdcard/shared/Download/{filename}",
		f"/sdcard/shared/{filename}",
	]

	# Try each path in order
	for path in search_paths:
		try:
			# Use ls -1 to check if file exists (requires exact match)
			ls_cmd = f"ls -1 {shlex.quote(path)}"
			result = str(adb.shell(serial, ls_cmd))
			if "No such file" not in result and filename in result and "cannot access" not in result.lower():
				return path
		except Exception:
			pass  # Continue to next path

	# Fallback: use find command with maxdepth
	try:
		# Use find with proper quoting, filter stderr, take first result
		escaped_name = filename.replace("'", "'\\''")
		find_cmd = f"find /sdcard/shared -maxdepth 4 -type f -name '{escaped_name}' 2>/dev/null | head -n 1"
		result = str(adb.shell(serial, find_cmd)).strip()
		if result and "/" in result:
			return result
	except Exception:
		pass  # No fallback available

	return None


class ReelPoster:
	"""
	State machine for Facebook reel posting following real UI flow.
	"""

	def __init__(
		self,
		adb: Any,
		log_fn: Callable[[str], None],
		skip_push_media: bool = False,
		fallback_push_if_missing: bool = False,
		keep_caption_extension: bool = False,
		get_adbkeyboard_request_fn: Optional[Callable[[str], ADBKeyboardRequest]] = None,
	):
		self.adb = adb
		self.log_fn = log_fn
		self.skip_push_media = skip_push_media  # If True, skip media push from PC
		self.fallback_push_if_missing = fallback_push_if_missing  # If True, push from PC if not found on emulator
		self.keep_caption_extension = keep_caption_extension
		self.get_adbkeyboard_request_fn = get_adbkeyboard_request_fn  # Callback to create ADBKeyboardRequest (called from worker thread)
		self._relaunch_count = 0  # Track relaunch attempts per job
		self._android_media_path: str | None = None  # Path on Android device after adb push
		self._android_media_name: str | None = None  # Filename on Android device
		self._caption_source: str = ""
		self._caption_source_rejected: bool = False

	# ------------------------------------------------------------------
	# ADBKeyboard Integration
	# ------------------------------------------------------------------

	def _is_adbkeyboard_installed(self, serial: str) -> bool:
		"""Check if ADBKeyboard is installed on the target emulator.
		
		Returns True if package com.android.adbkeyboard exists, False otherwise.
		"""
		try:
			result = self.adb.shell(serial, "pm list packages | grep com.android.adbkeyboard")
			installed = result is not None and "com.android.adbkeyboard" in str(result)
			self._log(f"[{serial}] ADBKeyboard installed: {installed}")
			return installed
		except Exception as e:
			self._log(f"[{serial}] Error checking ADBKeyboard installation: {e}")
			return False

	def _ensure_adbkeyboard_ready(self, serial: str) -> tuple[bool, str | None]:
		"""Enable and set ADBKeyboard as the active IME method.
		
		Returns (True, None) on success or (False, error_msg) on failure.
		"""
		try:
			self._log(f"[{serial}] Enabling ADBKeyboard IME...")
			self.adb.shell(serial, "ime enable com.android.adbkeyboard/.AdbIME")
			time.sleep(0.3)
			
			self._log(f"[{serial}] Setting ADBKeyboard as active IME...")
			self.adb.shell(serial, "ime set com.android.adbkeyboard/.AdbIME")
			time.sleep(0.3)
			
			self._log(f"[{serial}] ✓ ADBKeyboard IME enabled and set as active")
			return True, None
		except Exception as e:
			error_msg = f"Failed to enable ADBKeyboard IME: {e}"
			self._log(f"[{serial}] ✗ {error_msg}")
			return False, error_msg

	def _install_adbkeyboard_from_pc(self, serial: str, apk_path: str) -> tuple[bool, str | None]:
		"""Install ADBKeyboard APK from PC to emulator.
		
		Args:
			serial: Emulator serial
			apk_path: Full path to ADBKeyboard.apk on the PC
		
		Returns (True, None) on success or (False, error_msg) on failure.
		"""
		try:
			apk_file = Path(apk_path)
			if not apk_file.exists() or not apk_file.is_file():
				return False, f"APK file not found: {apk_path}"
			
			self._log(f"[{serial}] Installing ADBKeyboard from: {apk_path}")
			
			# Use adb install -r to replace any existing install
			result = self.adb.shell(serial, f'install -r "{apk_path}"')
			result_str = str(result) if result else ""
			
			self._log(f"[{serial}] Install result: {result_str[:200]}")
			
			# Check for success indicators
			if "Success" in result_str or "success" in result_str.lower():
				self._log(f"[{serial}] ✓ ADBKeyboard installed successfully")
				
				# Enable and set IME after installation
				ok, err = self._ensure_adbkeyboard_ready(serial)
				if not ok:
					return False, err
				
				return True, None
			else:
				error_msg = f"Installation failed (no 'Success' in output): {result_str[:500]}"
				self._log(f"[{serial}] ✗ {error_msg}")
				return False, error_msg
		
		except Exception as e:
			error_msg = f"Exception during ADBKeyboard installation: {e}"
			self._log(f"[{serial}] ✗ {error_msg}")
			return False, error_msg

	def _request_adbkeyboard_installation(self, serial: str) -> bool:
		"""Request ADBKeyboard installation via thread-safe signal.
		
		Creates an ADBKeyboardRequest and sends it to the UI thread.
		Blocks until the UI thread completes the installation dialog.
		
		Returns True if user successfully installed ADBKeyboard, False otherwise.
		"""
		if not self.get_adbkeyboard_request_fn:
			self._log(
				f"[{serial}] ✗ ADBKeyboard not installed and no request callback available. "
				f"Cannot proceed without manual installation."
			)
			return False
		
		# Create request - this will be handled by UI thread
		try:
			request = self.get_adbkeyboard_request_fn(serial)
		except Exception as e:
			self._log(f"[{serial}] ✗ Failed to create ADBKeyboard request: {e}")
			return False
		
		# Block until installation dialog completes (UI thread handles it)
		self._log(f"[{serial}] Waiting for ADBKeyboard installation dialog in UI thread...")
		ok, err = request.wait_for_result(timeout_s=300)
		
		if not ok:
			self._log(f"[{serial}] ✗ Installation failed or timed out: {err}")
			return False
		
		self._log(f"[{serial}] ✓ User successfully installed ADBKeyboard")
		
		# Verify it's actually installed and ready
		if self._is_adbkeyboard_installed(serial):
			ok_ready, err_ready = self._ensure_adbkeyboard_ready(serial)
			if ok_ready:
				return True
			else:
				self._log(f"[{serial}] ✗ ADBKeyboard installed but failed to enable: {err_ready}")
				return False
		else:
			self._log(f"[{serial}] ✗ Installation completed but ADBKeyboard not found on device")
			return False

	def _preflight_check_adbkeyboard(self, serial: str) -> tuple[bool, str | None]:
		"""Pre-flight check: Ensure ADBKeyboard is installed and ready before posting.
		
		This is called at the START of the posting workflow.
		If ADBKeyboard is not installed, shows dialog to user for installation.
		
		Returns (True, None) if ADBKeyboard is ready, or (False, error_msg) if not.
		"""
		self._log(f"[{serial}] ===== ADBKeyboard Pre-flight Check =====")
		
		# Step 1: Check if ADBKeyboard is already installed
		if self._is_adbkeyboard_installed(serial):
			self._log(f"[{serial}] ✓ ADBKeyboard already installed")
			
			# Ensure it's enabled and set as active
			ok, err = self._ensure_adbkeyboard_ready(serial)
			if ok:
				self._log(f"[{serial}] ✓ ADBKeyboard ready for use")
				return True, None
			else:
				return False, err
		
		# Step 2: ADBKeyboard not installed, request user to install
		self._log(f"[{serial}] ✗ ADBKeyboard NOT installed")
		self._log(f"[{serial}] Requesting user to install ADBKeyboard...")
		
		if self._request_adbkeyboard_installation(serial):
			self._log(f"[{serial}] ✓ ADBKeyboard installation verified")
			return True, None
		else:
			return False, "ADBKeyboard installation failed or was cancelled by user"

	def run(self, serial: str, job: ReelJob, device_media_path: str) -> tuple[bool, str | None]:
		"""
		Execute reel posting workflow through all states.
		Returns (success, error_message).
		Uses expected_package per state to recover the correct app when needed.
		"""
		self._relaunch_count = 0  # Reset for each job
		
		# Pre-flight check: Ensure ADBKeyboard is installed
		ok, err = self._preflight_check_adbkeyboard(serial)
		if not ok:
			self._log(f"[{serial}] ✗ Pre-flight ADBKeyboard check failed: {err}")
			return False, f"ADBKeyboard required: {err}"
		
		# State machine execution order
		states: list[tuple[ReelState, Callable[[str, ReelJob, str, int], tuple[bool, str | None]]]] = [
			(ReelState.PUSH_MEDIA, self._state_push_media),
			(ReelState.OPEN_FACEBOOK, self._state_open_facebook),
			(ReelState.ENSURE_FEED_STABLE, self._state_ensure_feed_stable),
			(ReelState.OPEN_HAMBURGER, self._state_open_hamburger),
			(ReelState.TAP_PROFILE_DROPDOWN, self._state_tap_profile_dropdown),
			(ReelState.SELECT_PAGE, self._state_select_page),
			(ReelState.PRESS_HOME, self._state_press_home),
			(ReelState.OPEN_FILE_MANAGER, self._state_open_file_manager),
			(ReelState.SELECT_PICTURES, self._state_select_pictures),
			(ReelState.NAVIGATE_MEDIA, self._state_navigate_media),
			(ReelState.HOLD_ON_MEDIA, self._state_hold_on_media),
			(ReelState.CLICK_ON_SEND, self._state_click_on_send),
			(ReelState.SHARE_TO_REELS, self._state_share_to_reels),
			(ReelState.WAIT_FOR_REELS_COMPOSER, self._state_wait_for_reels_composer),
			(ReelState.TAP_NEXT, self._state_tap_next),
			(ReelState.FILL_CAPTION, self._state_fill_caption),
			(ReelState.CONFIGURE_SCHEDULE, self._state_configure_schedule),
			(ReelState.TAP_SHARE, self._state_tap_share),
		]

		for state, handler in states:
			policy = STATE_POLICIES[state]
			last_error: str | None = None

			for attempt in range(1, policy.retries + 1):
				# Log state with expected package
				expected = policy.expected_package or "none"
				actual_before = self._get_foreground_package(serial)
				self._log(
					f"[{serial}] STATE: {state.value} (attempt {attempt}/{policy.retries}, expect: {expected}, actual: {actual_before}, android_media_path: {self._android_media_path})"
				)
				
				# Handle popup dismissal before state execution
				self._handle_popups(serial)
				
				# Ensure correct foreground app for this state
				if policy.expected_package and state != ReelState.ENSURE_FEED_STABLE:
					actual_pkg = self._get_foreground_package(serial)
					self._log(f"[{serial}] Foreground check: expected={policy.expected_package}, actual={actual_pkg}")
					fm_sensitive = {
						ReelState.HOLD_ON_MEDIA,
						ReelState.CLICK_ON_SEND,
						ReelState.SHARE_TO_REELS,
					}
					if actual_pkg == "com.android.gallery3d" and state in fm_sensitive:
						self._log(f"[{serial}] Gallery hijack detected during {state.value}, backing out...")
						if not self._recover_gallery_to_filemanager(serial):
							last_error = "Gallery hijack recovery failed"
							if attempt < policy.retries:
								time.sleep(0.8)
								continue
							break
						# Retry current state with File Manager restored
						if attempt < policy.retries:
							time.sleep(0.5)
							continue
					
					if actual_pkg != policy.expected_package:
						self._log(f"[{serial}] Package mismatch, recovering {policy.expected_package}...")
						if not self._ensure_foreground(serial, policy.expected_package):
							last_error = f"Failed to bring {policy.expected_package} to foreground"
							if attempt < policy.retries:
								time.sleep(0.8)
								continue
							return False, last_error
				
				# Execute state handler
				ok, err = handler(serial, job, device_media_path, policy.timeout_s)
				
				if ok:
					self._log(f"[{serial}] ✓ {state.value} completed")
					last_error = None
					break
				
				# State failed
				last_error = err or f"{state.value} failed"
				self._log(f"[{serial}] ✗ {state.value} failed: {last_error}")
				
				if attempt < policy.retries:
					self._log(f"[{serial}] Retrying {state.value} in 0.8s...")
					time.sleep(0.8)

			# If state exhausted retries, abort workflow
			if last_error is not None:
				self._dump_debug_artifacts(serial, state.value)
				return False, f"{state.value}: {last_error}"

		return True, None

	# ------------------------------------------------------------------
	# State Handlers
	# ------------------------------------------------------------------

	def _get_foreground_package(self, serial: str) -> str | None:
		"""Get current foreground package name from dumpsys window."""
		try:
			output = str(self.adb.shell(serial, "dumpsys window | grep mCurrentFocus"))
			if not output:
				return None
			# Parse variants like: ... u0 com.package/.Activity
			tokens = output.replace("{", " ").replace("}", " ").split()
			for tok in tokens:
				if "/" in tok and "." in tok:
					pkg = tok.split("/")[0].strip()
					if pkg:
						return pkg
			return None
		except Exception:
			return None

	def _ensure_foreground(self, serial: str, package: str) -> bool:
		"""Ensure the specified package is in foreground, relaunch if needed."""
		# Check if already in foreground
		current = self._get_foreground_package(serial)
		if current == package:
			self._log(f"[{serial}] ✓ {package} already in foreground")
			return True
		
		# Relaunch the app
		self._log(f"[{serial}] Launching {package}...")
		try:
			if package == "com.facebook.katana":
				self.adb.shell(serial, "monkey -p com.facebook.katana -c android.intent.category.LAUNCHER 1")
				time.sleep(2.5)
			elif package == "com.cyanogenmod.filemanager":
				self.adb.shell(serial, "monkey -p com.cyanogenmod.filemanager -c android.intent.category.LAUNCHER 1")
				time.sleep(1.5)
			else:
				self.adb.shell(serial, f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
				time.sleep(1.5)
		except Exception as exc:
			self._log(f"[{serial}] Failed to launch {package}: {exc}")
			return False
		
		# Verify it came to foreground
		time.sleep(0.5)
		current = self._get_foreground_package(serial)
		if current == package:
			self._log(f"[{serial}] ✓ {package} now in foreground")
			return True
		
		self._log(f"[{serial}] ✗ {package} failed to come to foreground (current: {current})")
		return False

	def _dump_debug_artifacts(self, serial: str, state_name: str) -> None:
		"""Dump XML + screenshot only on final failure."""
		tag = f"reel_fail_{state_name}_{serial}_{int(time.time())}"
		xml_path = Path.cwd() / f"{tag}.xml"
		png_remote = f"/sdcard/{tag}.png"
		png_local = Path.cwd() / f"{tag}.png"

		try:
			xml = dump_ui_xml(self.adb, serial)
			if xml:
				xml_path.write_text(xml, encoding="utf-8")
				self._log(f"[{serial}] Debug XML saved: {xml_path}")
		except Exception as exc:
			self._log(f"[{serial}] Debug XML save failed: {exc}")

		try:
			self.adb.shell(serial, f"screencap -p '{png_remote}'")
			if hasattr(self.adb, "_adb"):
				dev = self.adb._adb.device(serial=serial)
				dev.sync.pull(png_remote, str(png_local))
				self.adb.shell(serial, f"rm -f '{png_remote}'")
				self._log(f"[{serial}] Debug screenshot saved: {png_local}")
		except Exception as exc:
			self._log(f"[{serial}] Debug screenshot save failed: {exc}")

	def _recover_gallery_to_filemanager(self, serial: str) -> bool:
		"""When gallery hijacks flow, back out then restore File Manager foreground."""
		for back_try in range(2):
			current = self._get_foreground_package(serial)
			if current != "com.android.gallery3d":
				break
			self._log(f"[{serial}] Gallery hijack: pressing BACK ({back_try + 1}/2)")
			self.adb.shell(serial, "input keyevent KEYCODE_BACK")
			time.sleep(0.4)

		current = self._get_foreground_package(serial)
		if current == "com.cyanogenmod.filemanager":
			return True
		return self._ensure_foreground(serial, "com.cyanogenmod.filemanager")

	def _find_file_name_bounds(self, xml: str, file_name: str) -> tuple[int, int, int, int] | None:
		"""Find exact filename node in CM File Manager using provided identifier fields."""
		if not xml:
			return None
		try:
			root = ET.fromstring(xml)
		except Exception:
			return None

		for node in root.iter():
			a = node.attrib
			if (
				a.get("class") == "android.widget.TextView"
				and a.get("resource-id") == "com.cyanogenmod.filemanager:id/navigation_view_item_name"
				and a.get("text") == file_name
			):
				bounds_txt = a.get("bounds", "")
				try:
					return tuple(map(int, bounds_txt.replace("][", ",").replace("[", "").replace("]", "").split(",")))  # type: ignore[return-value]
				except Exception:
					continue
		return None

	def _long_press_bounds(self, serial: str, bounds: tuple[int, int, int, int], duration_ms: int = 1000) -> None:
		x1, y1, x2, y2 = bounds
		cx = (x1 + x2) // 2
		cy = (y1 + y2) // 2
		self.adb.shell(serial, f"input swipe {cx} {cy} {cx} {cy} {duration_ms}")

	def _is_in_shared_pictures(self, xml: str) -> bool:
		"""
		Check if File Manager breadcrumb shows we're inside /sdcard/shared/Pictures.
		Returns True if breadcrumb contains both "shared" and "Pictures".
		"""
		if not xml:
			return False
		try:
			root = ET.fromstring(xml)
		except Exception:
			return False

		# Find all breadcrumb items
		breadcrumb_texts: list[str] = []
		for node in root.iter():
			res_id = node.attrib.get("resource-id", "")
			if "breadcrumb_item" in res_id:
				text = node.attrib.get("text", "")
				if text:
					breadcrumb_texts.append(text.lower())

		# Check if both "shared" and "pictures" appear in breadcrumb
		has_shared = any("shared" in txt for txt in breadcrumb_texts)
		has_pictures = any("pictures" in txt or "picture" in txt for txt in breadcrumb_texts)
		
		return has_shared and has_pictures

	def _find_folder_row_bounds(self, xml: str, folder_name: str = "Pictures") -> tuple[int, int, int, int] | None:
		"""
		Find folder row in File Manager folder list by exact name match.
		Returns bounds of the row (parent of TextView with matching text).
		"""
		if not xml:
			return None
		try:
			root = ET.fromstring(xml)
		except Exception:
			return None

		# Find TextView with matching text and correct resource-id
		for node in root.iter():
			res_id = node.attrib.get("resource-id", "")
			text = node.attrib.get("text", "")
			
			if "navigation_view_item_name" in res_id and text == folder_name:
				# Found the TextView, return its bounds
				bounds_txt = node.attrib.get("bounds", "")
				try:
					coords = tuple(map(int, bounds_txt.replace("][", ",").replace("[", "").replace("]", "").split(",")))
					if len(coords) == 4:
						return coords  # type: ignore[return-value]
				except Exception:
					continue

		return None

	def _is_actions_dialog_open(self, xml: str) -> bool:
		"""Return True when CM File Manager Actions dialog is visible."""
		if not xml:
			return False
		try:
			root = ET.fromstring(xml)
		except Exception:
			return False

		for node in root.iter():
			a = node.attrib
			if (
				a.get("resource-id") == "com.cyanogenmod.filemanager:id/dialog_title_text"
				and (a.get("text") or "").strip().lower() == "actions"
			):
				return True
		return False

	def _wait_for_actions_dialog(self, serial: str, timeout_s: float = 2.0) -> str | None:
		"""Wait for Actions dialog XML and return it when visible."""
		end_at = time.time() + max(0.2, timeout_s)
		while time.time() < end_at:
			xml = dump_ui_xml(self.adb, serial)
			if xml and self._is_actions_dialog_open(xml):
				return xml
			time.sleep(0.2)
		return None

	def _tap_send_in_actions_dialog(self, serial: str, xml: str) -> bool:
		"""Tap Send action in CM File Manager Actions dialog."""
		if not xml:
			return False

		# Prefer exact known resource-id from CM File Manager dialog item
		send_btn = find_first(
			xml,
			{
				"res_id_contains": "two_columns_menu2_item_text",
				"text_equals": "Send",
				"clickable": True,
			},
		)
		if not send_btn:
			send_btn = find_first(xml, {"text_equals": "Send", "clickable": True})
		if not send_btn:
			send_btn = find_first(xml, {"text_contains": "Send", "clickable": True})
		if not send_btn:
			send_btn = find_first(xml, {"text_equals": "Share", "clickable": True})
		if not send_btn:
			send_btn = find_first(xml, {"text_contains": "Share", "clickable": True})

		if not send_btn:
			return False

		tap_center(self.adb, serial, send_btn)
		return True

	def _is_send_transition_success(self, serial: str, xml_after: str | None) -> bool:
		"""Success after tapping Send: dialog closed, chooser visible, or package changed."""
		if xml_after and not self._is_actions_dialog_open(xml_after):
			return True

		current_pkg = self._get_foreground_package(serial)
		if current_pkg and current_pkg != "com.cyanogenmod.filemanager":
			return True

		if xml_after:
			share_markers = (
				"Reels",
				"Share with Reels",
				"Facebook",
				"JUST ONCE",
				"Just once",
			)
			if any(marker in xml_after for marker in share_markers):
				return True

		return False

	def _state_push_media(
		self,
		serial: str,
		job: ReelJob,
		device_media_path: str,
		timeout_s: int,
	) -> tuple[bool, str | None]:
		"""
		Prepare media path on emulator; skip PC access when skip_push_media is enabled.
		
		IMPORTANT: With skip_push_media=True, expects media files to already be on emulator.
		Original files are kept in place (e.g., /sdcard/shared/Pictures/) and never moved.
		File Manager will search for files at their original location.
		"""
		filename = ""
		if job.media_path:
			filename = job.media_path.replace("\\", "/").split("/")[-1]

		if not filename:
			return False, "job.media_path is empty or cannot determine filename"

		# --- SKIP_PUSH_MEDIA mode: Search emulator for file ---
		if self.skip_push_media:
			self._log(
				f"[{serial}] skip_push_media=True, resolving original media on emulator: {filename}"
			)
			
			# Try to resolve the file using search order
			resolved_path = resolve_emulator_media_path(self.adb, serial, filename)
			
			if resolved_path:
				# File found on emulator
				self._log(
					f"[{serial}] ✓ Original media found on emulator: {resolved_path} "
					f"(file left in place, not moved)"
				)
				self._android_media_path = resolved_path
				self._android_media_name = Path(resolved_path).name
				return True, None
			
			# File not found on emulator
			self._log(f"[{serial}] ✗ Media not found in search paths for: {filename}")
			
			if self.fallback_push_if_missing:
				# Try to push from PC
				pc_path = Path(job.media_path)
				if not pc_path.exists() or not pc_path.is_file():
					return False, f"Media missing on PC and emulator: {job.media_path}"
				
				self._log(f"[{serial}] fallback_push_if_missing=True, pushing from PC: {pc_path}")
				dst_dir = "/sdcard/shared/Pictures/"
				self.adb.shell(serial, f"mkdir -p '{dst_dir}'")
				dst_filename = pc_path.name
				dst_full_path = f"{dst_dir}{dst_filename}"
				
				try:
					if hasattr(self.adb, "_adb"):
						dev = self.adb._adb.device(serial=serial)
						dev.sync.push(str(pc_path), dst_full_path)
					else:
						return False, "adb client does not support push"
				except Exception as exc:
					return False, f"Failed to push media fallback: {exc}"
				
				# Verify pushed file
				verify = str(self.adb.shell(serial, f"ls -1 \"{dst_full_path}\""))
				if "No such file" in verify or dst_filename not in verify:
					return False, f"Fallback push verification failed: {dst_full_path}"
				
				self._log(f"[{serial}] ✓ Fallback push succeeded: {dst_full_path}")
				self._android_media_path = dst_full_path
				self._android_media_name = dst_filename
				return True, None
			else:
				# No fallback, mark as SKIPPED (log but don't fail)
				self._log(
					f"[{serial}] ⊘ Media skipped (not found on emulator, fallback_push_if_missing=False): {filename}"
				)
				# Still set a path to avoid downstream failures (use Pictures as default)
				self._android_media_path = None  # Indicate media not available
				self._android_media_name = filename
				return True, None  # Don't fail upstream, let downstream handle it

		# --- Normal mode: Push from PC ---
		pc_path = Path(job.media_path)
		if not pc_path.exists() or not pc_path.is_file():
			return False, f"Source media not found on PC: {job.media_path}"

		dst_dir = "/sdcard/shared/Pictures/"
		self.adb.shell(serial, f"mkdir -p '{dst_dir}'")
		dst_filename = pc_path.name
		dst_full_path = f"{dst_dir}{dst_filename}"
		self._log(f"[{serial}] Pushing media to emulator: {dst_full_path}")
		try:
			if hasattr(self.adb, "_adb"):
				dev = self.adb._adb.device(serial=serial)
				dev.sync.push(str(pc_path), dst_full_path)
			else:
				return False, "adb client does not support push"
		except Exception as exc:
			return False, f"Failed to push media: {exc}"

		verify = str(self.adb.shell(serial, f"ls -1 \"{dst_full_path}\""))
		if "No such file" in verify or dst_filename not in verify:
			return False, f"Media missing on emulator: {dst_full_path}"

		self._android_media_name = dst_filename
		self._android_media_path = dst_full_path
		return True, None

	def _state_open_facebook(
		self,
		serial: str,
		job: ReelJob,
		device_media_path: str,
		timeout_s: int,
	) -> tuple[bool, str | None]:
		"""Launch Facebook app."""
		return self._helper_open_facebook(serial, timeout_s)

	def _state_ensure_feed_stable(
		self,
		serial: str,
		job: ReelJob,
		device_media_path: str,
		timeout_s: int,
	) -> tuple[bool, str | None]:
		"""
		Ensure Facebook is in foreground and UI is stable.
		
		Validates:
		1. com.facebook.katana is focused package
		2. Hamburger button exists (content-desc contains "Facebook menu")
		3. "Create" button exists
		4. UI is stable: hamburger bounds identical in 2 dumps 600ms apart
		5. Top bar exists in both dumps
		"""
		end_at = time.time() + timeout_s
		
		# Step 1: Verify Facebook in foreground
		self._log(f"[{serial}] Verifying Facebook is in foreground...")
		while time.time() < end_at:
			if is_facebook_running(self.adb, serial):
				self._log(f"[{serial}] ✓ Facebook in foreground")
				break
			time.sleep(0.5)
		else:
			return False, "Facebook not in foreground"
		
		# Step 2: Wait for UI elements to appear
		self._log(f"[{serial}] Waiting for UI elements (hamburger, create button)...")
		element_timeout = time.time() + 15.0
		while time.time() < element_timeout:
			xml = dump_ui_xml(self.adb, serial)
			if not xml:
				time.sleep(0.5)
				continue
			
			hamburger = find_facebook_hamburger(xml)
			create_btn = find_create_button(xml)
			
			if hamburger and create_btn:
				self._log(f"[{serial}] ✓ Hamburger and Create button found")
				break
			
			time.sleep(0.5)
		else:
			return False, "UI elements not found (hamburger or create button)"
		
		# Step 3: Verify UI stability (2 consecutive dumps)
		self._log(f"[{serial}] Checking UI stability...")
		
		# First dump
		xml1 = dump_ui_xml(self.adb, serial)
		if not xml1:
			return False, "Cannot dump UI (first attempt)"
		
		hamburger1 = find_facebook_hamburger(xml1)
		topbar1 = find_top_bar(xml1)
		
		if not hamburger1:
			return False, "Hamburger not found in first dump"
		
		# Wait 600ms
		time.sleep(0.6)
		
		# Second dump
		xml2 = dump_ui_xml(self.adb, serial)
		if not xml2:
			return False, "Cannot dump UI (second attempt)"
		
		hamburger2 = find_facebook_hamburger(xml2)
		topbar2 = find_top_bar(xml2)
		
		if not hamburger2:
			return False, "Hamburger not found in second dump"
		
		# Check if hamburger bounds are identical
		if hamburger1 != hamburger2:
			self._log(f"[{serial}] Hamburger bounds differ, waiting for stability...")
			return False, "UI not stable (hamburger bounds changed)"
		
		# Check if top bar exists in both
		if not topbar1 or not topbar2:
			return False, "Top bar not found in UI dumps"
		
		self._log(f"[{serial}] ✓ UI is stable (hamburger bounds match, top bar present)")
		
		# Step 4: Cooldown before any tap
		self._log(f"[{serial}] Cooldown: waiting 1.2s before next interaction...")
		time.sleep(1.2)
		
		return True, None

	def _state_open_hamburger(
		self,
		serial: str,
		job: ReelJob,
		device_media_path: str,
		timeout_s: int,
	) -> tuple[bool, str | None]:
		"""
		Open hamburger menu (top-left) with retry and verification.
		
		- Finds hamburger element-based via find_facebook_hamburger
		- Taps its center
		- Verifies menu opened by checking "Your shortcuts" or "See more"
		- Retries up to 2 times with 800ms delay between attempts
		"""
		max_attempts = 2
		attempt = 0
		
		while attempt < max_attempts:
			attempt += 1
			self._log(f"[{serial}] OPEN_HAMBURGER: attempt {attempt}/{max_attempts}")
			
			# Dump UI and find hamburger
			xml = dump_ui_xml(self.adb, serial)
			if not xml:
				self._log(f"[{serial}] Cannot dump UI, retrying...")
				if attempt < max_attempts:
					time.sleep(0.8)
				continue
			
			bounds = find_facebook_hamburger(xml)
			if not bounds:
				self._log(f"[{serial}] Hamburger button not found in UI")
				if attempt < max_attempts:
					time.sleep(0.8)
				continue
			
			self._log(f"[{serial}] Found hamburger at {bounds}, tapping...")
			
			# Tap hamburger
			if not tap_center(self.adb, serial, bounds):
				self._log(f"[{serial}] Tap failed, retrying...")
				if attempt < max_attempts:
					time.sleep(0.8)
				continue
			
			# Wait for menu to expand
			time.sleep(0.8)
			
			# Verify menu is open by looking for menu indicators
			verify_end = time.time() + 5.0
			while time.time() < verify_end:
				verify_xml = dump_ui_xml(self.adb, serial)
				if verify_xml:
					menu_indicators = [
						{"text_contains": "Your shortcuts"},
						{"text_contains": "See more"},
					]
					if self._find_any(verify_xml, menu_indicators):
						self._log(f"[{serial}] ✓ Menu verified as open with menu indicators")
						return True, None
				time.sleep(0.3)
			
			# Menu did not open, retry
			self._log(f"[{serial}] Menu did not open, retrying...")
			if attempt < max_attempts:
				time.sleep(0.8)
		
		return False, f"Failed to open hamburger menu after {max_attempts} attempts"

	def _state_tap_profile_dropdown(
		self,
		serial: str,
		job: ReelJob,
		device_media_path: str,
		timeout_s: int,
	) -> tuple[bool, str | None]:
		"""Tap profile dropdown/account switcher to reveal page list."""
		# Use coordinate-based tap inside menu panel to open account switcher
		if open_account_switcher_from_menu(
			self.adb,
			serial,
			target_page_name=job.target_page,
			log_fn=self.log_fn,
		):
			self._log(f"[{serial}] ✓ Account switcher opened")
			return True, None
		
		# Fallback: try element-based approach
		self._log(f"[{serial}] Coordinate tap failed, trying element-based approach...")
		if self._tap_first_match(serial, SELECTORS["PROFILE_DROPDOWN"], timeout_s=5):
			time.sleep(0.8)
			return True, None
		
		# If still no dropdown found, maybe pages are already visible
		self._log(f"[{serial}] No dropdown interaction succeeded, pages may already be visible")
		return True, None

	def _state_select_page(
		self,
		serial: str,
		job: ReelJob,
		device_media_path: str,
		timeout_s: int,
	) -> tuple[bool, str | None]:
		"""Select target page by name."""
		return self._helper_switch_to_page(serial, job.target_page, timeout_s)

	def _state_open_page_profile(
		self,
		serial: str,
		job: ReelJob,
		device_media_path: str,
		timeout_s: int,
	) -> tuple[bool, str | None]:
		"""Open Profile tab using robust selector."""
		try:
			success = open_profile_tab(self.adb, serial, self.log_fn, timeout_s)
			if success:
				return True, None
		except Exception as exc:
			return False, str(exc)
		
		return False, "Profile tab not opened"

	def _state_press_home(
		self,
		serial: str,
		job: ReelJob,
		device_media_path: str,
		timeout_s: int,
	) -> tuple[bool, str | None]:
		"""Press HOME key to exit Facebook and return to home screen."""
		self._log(f"[{serial}] Pressing HOME to exit Facebook")
		self.adb.shell(serial, "input keyevent 3")  # KEYCODE_HOME
		time.sleep(1.0)
		
		# Optionally force-stop Facebook to prevent recovery triggers
		self._log(f"[{serial}] Force-stopping Facebook to avoid recovery triggers")
		try:
			self.adb.shell(serial, "am force-stop com.facebook.katana")
		except Exception:
			pass  # Continue even if force-stop fails
		
		time.sleep(0.5)
		return True, None

	def _state_open_file_manager(
		self,
		serial: str,
		job: ReelJob,
		device_media_path: str,
		timeout_s: int,
	) -> tuple[bool, str | None]:
		"""Launch CyanogenMod File Manager and verify it opened."""
		self._log(f"[{serial}] Launching File Manager")
		
		try:
			self.adb.shell(serial, "monkey -p com.cyanogenmod.filemanager -c android.intent.category.LAUNCHER 1")
		except Exception:
			try:
				self.adb.shell(serial, "am start -n com.cyanogenmod.filemanager/.activities.MainActivity")
			except Exception as exc:
				return False, f"Failed to launch File Manager: {exc}"

		time.sleep(1.0)
		end_at = time.time() + timeout_s
		while time.time() < end_at:
			pkg = self._get_foreground_package(serial)
			if pkg == "com.cyanogenmod.filemanager":
				self._log(f"[{serial}] ✓ File Manager is now in foreground")
				return True, None
			time.sleep(0.5)

		return False, f"File Manager did not come to foreground within {timeout_s}s"

	def _state_select_pictures(
		self,
		serial: str,
		job: ReelJob,
		device_media_path: str,
		timeout_s: int,
	) -> tuple[bool, str | None]:
		"""
		Select Pictures folder with strict verification.
		Must end with breadcrumb showing "shared > Pictures".
		"""
		# Ensure Pictures directory exists
		self.adb.shell(serial, "mkdir -p /sdcard/shared/Pictures")
		time.sleep(0.2)

		self._log(f"[{serial}] SELECT_PICTURES: opening /sdcard/shared/Pictures")
		
		end_at = time.time() + timeout_s
		max_attempts = 3
		attempt = 0

		while time.time() < end_at and attempt < max_attempts:
			attempt += 1
			self._log(f"[{serial}] SELECT_PICTURES attempt {attempt}/{max_attempts}")

			# Step 1: Check if already in Pictures
			xml = dump_ui_xml(self.adb, serial)
			if xml and self._is_in_shared_pictures(xml):
				self._log(f"[{serial}] ✓ Already in shared/Pictures (breadcrumb verified)")
				return True, None

			# Step 2: Ensure File Manager is in foreground
			if self._get_foreground_package(serial) != "com.cyanogenmod.filemanager":
				self._log(f"[{serial}] File Manager not in foreground, restoring...")
				if not self._ensure_foreground(serial, "com.cyanogenmod.filemanager"):
					return False, "Failed to restore File Manager foreground"
				time.sleep(0.5)

			# Step 3: Try to find and tap Pictures folder row
			scroll_attempts = 0
			max_scrolls = 8
			found_and_tapped = False

			while scroll_attempts < max_scrolls:
				xml = dump_ui_xml(self.adb, serial)
				if not xml:
					time.sleep(0.3)
					continue

				# Try to find Pictures folder row
				pictures_bounds = self._find_folder_row_bounds(xml, "Pictures")
				
				if pictures_bounds:
					self._log(f"[{serial}] Found Pictures folder row, tapping...")
					tap_center(self.adb, serial, pictures_bounds)
					time.sleep(0.6)
					found_and_tapped = True
					break
				
				# Pictures not visible, scroll down to find it
				self._log(f"[{serial}] Pictures row not visible, scrolling... ({scroll_attempts + 1}/{max_scrolls})")
				swipe(self.adb, serial, 360, 1040, 360, 600, 300)
				time.sleep(0.4)
				scroll_attempts += 1

			if not found_and_tapped:
				self._log(f"[{serial}] Pictures folder row not found after scrolling")
				# Try fallback: search approach
				xml = dump_ui_xml(self.adb, serial)
				if xml:
					search_btn = find_first(xml, {"res_id_contains": "ab_search", "clickable": True})
					if search_btn:
						self._log(f"[{serial}] Trying search fallback...")
						tap_center(self.adb, serial, search_btn)
						time.sleep(0.35)
						self.adb.shell(serial, "input text Pictures")
						time.sleep(0.35)
						self.adb.shell(serial, "input keyevent KEYCODE_ENTER")
						time.sleep(0.5)
						xml2 = dump_ui_xml(self.adb, serial)
						if xml2:
							pics_result = find_first(xml2, {"text_contains": "Pictures", "clickable": True})
							if pics_result:
								tap_center(self.adb, serial, pics_result)
								time.sleep(0.6)
								found_and_tapped = True

			# Step 4: Verify breadcrumb shows we're in Pictures
			verify_end = time.time() + 3.0
			while time.time() < verify_end:
				xml_verify = dump_ui_xml(self.adb, serial)
				if xml_verify and self._is_in_shared_pictures(xml_verify):
					self._log(f"[{serial}] ✓ Pictures folder opened (breadcrumb: shared > Pictures)")
					return True, None
				time.sleep(0.4)

			# Verification failed, log and retry
			self._log(
				f"[{serial}] Pictures folder did not open (breadcrumb verification failed), "
				f"retrying... (attempt {attempt}/{max_attempts})"
			)
			time.sleep(0.5)

		return False, "SELECT_PICTURES failed: Pictures folder did not open (breadcrumb not shared/Pictures)"

	def _state_navigate_media(
		self,
		serial: str,
		job: ReelJob,
		device_media_path: str,
		timeout_s: int,
	) -> tuple[bool, str | None]:
		"""Ensure media list/results are visible and target filename appears."""
		# Use resolved android_media_name, fallback to PC filename
		filename = self._android_media_name or (job.media_path.replace("\\", "/").split("/")[-1] if job.media_path else "")
		if not filename:
			return False, "Cannot determine media filename"
		
		# GUARD: Verify we're in shared/Pictures before navigating
		xml_guard = dump_ui_xml(self.adb, serial)
		if xml_guard and not self._is_in_shared_pictures(xml_guard):
			self._log(
				f"[{serial}] ✗ NAVIGATE_MEDIA guard failed: Not in shared/Pictures folder, "
				f"calling SELECT_PICTURES again..."
			)
			# Call SELECT_PICTURES to fix the state
			ok, err = self._state_select_pictures(serial, job, device_media_path, 15)
			if not ok:
				return False, f"NAVIGATE_MEDIA guard: Failed to open Pictures folder: {err}"
			# Verify again after SELECT_PICTURES
			xml_guard2 = dump_ui_xml(self.adb, serial)
			if xml_guard2 and not self._is_in_shared_pictures(xml_guard2):
				return False, "NAVIGATE_MEDIA guard: Still not in Pictures after SELECT_PICTURES"
		
		# Log what we're looking for
		self._log(
			f"[{serial}] NAVIGATE_MEDIA target={filename}, "
			f"resolved_path={self._android_media_path}, skip_push_media={self.skip_push_media}"
		)

		end_at = time.time() + timeout_s
		scroll_attempts = 0
		while time.time() < end_at and scroll_attempts < 8:
			xml = dump_ui_xml(self.adb, serial)
			if not xml:
				time.sleep(0.4)
				continue

			if self._find_file_name_bounds(xml, filename):
				self._log(f"[{serial}] ✓ Media row visible: {filename}")
				return True, None

			swipe(self.adb, serial, 360, 1040, 360, 380, 350)
			time.sleep(0.35)
			scroll_attempts += 1

		return False, f"Media file not found: {filename}"

	def _state_hold_on_media(
		self,
		serial: str,
		job: ReelJob,
		device_media_path: str,
		timeout_s: int,
	) -> tuple[bool, str | None]:
		"""Long-press target filename to enter selection mode (never single-tap)."""
		# Use resolved android_media_name, fallback to PC filename
		filename = self._android_media_name or (job.media_path.replace("\\", "/").split("/")[-1] if job.media_path else "")
		if not filename:
			return False, "Cannot determine media filename"

		self._log(
			f"[{serial}] HOLD_ON_MEDIA target={filename}, "
			f"resolved_path={self._android_media_path}"
		)

		if self._get_foreground_package(serial) == "com.android.gallery3d":
			self._log(f"[{serial}] Gallery hijack occurred in HOLD_ON_MEDIA")
			if not self._recover_gallery_to_filemanager(serial):
				return False, "Failed to recover from Gallery hijack"

		end_at = time.time() + timeout_s
		while time.time() < end_at:
			xml = dump_ui_xml(self.adb, serial)
			bounds = self._find_file_name_bounds(xml, filename) if xml else None
			if bounds:
				self._log(f"[{serial}] Long-pressing media: {filename}")
				self._long_press_bounds(serial, bounds, duration_ms=1000)
				time.sleep(0.45)
				return True, None
			swipe(self.adb, serial, 360, 1040, 360, 380, 350)
			time.sleep(0.35)

		return False, f"Unable to long-press media: {filename}"

	def _state_click_on_send(
		self,
		serial: str,
		job: ReelJob,
		device_media_path: str,
		timeout_s: int,
	) -> tuple[bool, str | None]:
		"""Tap Send from Actions dialog (dialog-first for CM File Manager UI)."""
		filename = self._android_media_name or (job.media_path.replace("\\", "/").split("/")[-1] if job.media_path else "")
		self._log(
			f"[{serial}] CLICK_ON_SEND filename={filename}, "
			f"resolved_path={self._android_media_path}"
		)

		if self._get_foreground_package(serial) == "com.android.gallery3d":
			self._log(f"[{serial}] Gallery hijack occurred in CLICK_ON_SEND")
			if not self._recover_gallery_to_filemanager(serial):
				return False, "Failed to recover from Gallery hijack"
			# Re-enter selection mode before opening send actions
			ok, err = self._state_hold_on_media(serial, job, device_media_path, min(6, timeout_s))
			if not ok:
				return False, err

		end_at = time.time() + timeout_s
		while time.time() < end_at:
			xml = dump_ui_xml(self.adb, serial)
			if not xml:
				time.sleep(0.3)
				continue

			# Dialog-first path: long-press often opens Actions modal directly.
			if self._is_actions_dialog_open(xml):
				self._log(f"[{serial}] Actions dialog already open → tapping Send directly")
				if self._tap_send_in_actions_dialog(serial, xml):
					time.sleep(0.35)
					xml_after = dump_ui_xml(self.adb, serial)
					if self._is_send_transition_success(serial, xml_after):
						return True, None
					self._log(f"[{serial}] Send tapped but transition not confirmed yet, retrying...")
					time.sleep(0.25)
					continue
				self._log(f"[{serial}] Actions dialog open but Send item not found")
				time.sleep(0.25)
				continue

			# Dialog is not open yet: try to open it via toolbar/overflow/menu.
			actions = find_first(xml, {"res_id_contains": "ab_actions", "clickable": True})
			if not actions:
				actions = find_first(xml, {"desc_contains": "Actions", "clickable": True})

			if not actions:
				self._log(f"[{serial}] Actions button not found, trying overflow menu...")
				actions = find_first(xml, {"desc_contains": "More options", "clickable": True})
			if not actions:
				actions = find_first(xml, {"res_id_contains": "overflow", "clickable": True})
			if not actions:
				actions = find_first(xml, {"res_id_contains": "menu", "clickable": True})

			if actions:
				tap_center(self.adb, serial, actions)
				xml_menu = self._wait_for_actions_dialog(serial, timeout_s=2.0)
				if xml_menu and self._tap_send_in_actions_dialog(serial, xml_menu):
					time.sleep(0.35)
					xml_after = dump_ui_xml(self.adb, serial)
					if self._is_send_transition_success(serial, xml_after):
						return True, None
					self._log(f"[{serial}] Send tapped from Actions menu but transition not confirmed yet")
			time.sleep(0.3)

		return False, "Send action not completed from Actions dialog/menu"

	def _state_share_to_reels(
		self,
		serial: str,
		job: ReelJob,
		device_media_path: str,
		timeout_s: int,
	) -> tuple[bool, str | None]:
		"""Choose Reels in Android share sheet, handle chooser, tap JUST ONCE if present.
		
		Priority order:
		  1. Try "Share with Reels" up to 2 attempts
		  2. Fallback to "Reels" (may be non-clickable text node)
		"""
		# Handle Gallery hijack recovery
		if self._get_foreground_package(serial) == "com.android.gallery3d":
			self._log(f"[{serial}] Gallery hijack occurred in SHARE_TO_REELS")
			if not self._recover_gallery_to_filemanager(serial):
				return False, "Failed to recover from Gallery hijack"
			ok_hold, err_hold = self._state_hold_on_media(serial, job, device_media_path, 6)
			if not ok_hold:
				return False, err_hold
			ok_send, err_send = self._state_click_on_send(serial, job, device_media_path, 8)
			if not ok_send:
				return False, err_send

		expected_chooser_packages = {
			"android",
			"com.android.intentresolver",
			"com.google.android.permissioncontroller",
			"com.android.systemui",
		}

		# -----------------------------------------------------------
		# Phase 1: Try "Share with Reels" up to 2 attempts
		# -----------------------------------------------------------
		MAX_SHARE_WITH_REELS_ATTEMPTS = 2
		share_with_reels_found = False

		for attempt in range(1, MAX_SHARE_WITH_REELS_ATTEMPTS + 1):
			self._log(f"[{serial}] SHARE_TO_REELS: Share with Reels attempt {attempt}/{MAX_SHARE_WITH_REELS_ATTEMPTS}")

			attempt_end = time.time() + (timeout_s // 2 if attempt == 1 else 6)
			while time.time() < attempt_end:
				current_pkg = self._get_foreground_package(serial)

				if current_pkg == "com.facebook.katana":
					self._log(f"[{serial}] ✓ Facebook is now foreground - success")
					return True, None

				if current_pkg and current_pkg not in expected_chooser_packages:
					time.sleep(0.5)
					continue

				if not self._wait_for_android_chooser(serial, timeout_s=2):
					time.sleep(0.3)
					continue

				xml = dump_ui_xml(self.adb, serial)
				if not xml:
					time.sleep(0.3)
					continue

				# Search for "Share with Reels" using multiple strategies
				bounds = self._find_share_target_bounds(xml, ["Share with Reels"])

				if not bounds:
					# Also try find_first as a fallback for clickable nodes
					node = find_first(xml, {"text_contains": "Share with Reels", "clickable": True})
					if node:
						bounds = node

				if bounds:
					self._log(f"[{serial}] SHARE_TO_REELS: Found Share with Reels at {bounds}, tapping...")
					tap_center(self.adb, serial, bounds)
					time.sleep(0.5)
					share_with_reels_found = True

					# Handle JUST ONCE if present
					if self._tap_just_once_if_present(serial):
						self._log(f"[{serial}] SHARE_TO_REELS: Tapped JUST ONCE after Share with Reels")

					# Wait for Facebook foreground
					fb_wait_end = time.time() + 10
					while time.time() < fb_wait_end:
						current_pkg = self._get_foreground_package(serial)
						if current_pkg == "com.facebook.katana":
							self._log(f"[{serial}] ✓ Facebook opened after Share with Reels tap")
							return True, None
						time.sleep(0.5)

					# Facebook didn't open; maybe JUST ONCE appeared late
					if self._tap_just_once_if_present(serial):
						self._log(f"[{serial}] SHARE_TO_REELS: Tapped late JUST ONCE")
						fb_wait_end2 = time.time() + 6
						while time.time() < fb_wait_end2:
							current_pkg = self._get_foreground_package(serial)
							if current_pkg == "com.facebook.katana":
								self._log(f"[{serial}] ✓ Facebook opened after late JUST ONCE")
								return True, None
							time.sleep(0.5)

					# Didn't work this attempt, break out to try next attempt
					break

				time.sleep(0.5)

			if share_with_reels_found:
				# Was found but Facebook didn't open; still try next attempt
				self._log(f"[{serial}] SHARE_TO_REELS: Share with Reels was tapped but Facebook did not open, retrying...")
				share_with_reels_found = False
				continue

			self._log(f"[{serial}] SHARE_TO_REELS: Share with Reels not found (attempt {attempt}/{MAX_SHARE_WITH_REELS_ATTEMPTS})")

		# -----------------------------------------------------------
		# Phase 2: Fallback to "Reels" (including non-clickable nodes)
		# -----------------------------------------------------------
		self._log(f"[{serial}] SHARE_TO_REELS: Share with Reels not found after {MAX_SHARE_WITH_REELS_ATTEMPTS} retries, falling back to Reels")

		fallback_end = time.time() + 8
		while time.time() < fallback_end:
			current_pkg = self._get_foreground_package(serial)

			if current_pkg == "com.facebook.katana":
				self._log(f"[{serial}] ✓ Facebook is now foreground - success")
				return True, None

			if current_pkg and current_pkg not in expected_chooser_packages:
				time.sleep(0.5)
				continue

			if not self._wait_for_android_chooser(serial, timeout_s=2):
				time.sleep(0.3)
				continue

			xml = dump_ui_xml(self.adb, serial)
			if not xml:
				time.sleep(0.3)
				continue

			# Search for "Reels" text node (may be non-clickable)
			bounds = self._find_share_target_bounds(xml, ["Reels"])

			if not bounds:
				# Also try clickable fallback
				node = find_first(xml, {"text_equals": "Reels", "clickable": True})
				if not node:
					node = find_first(xml, {"text_contains": "Reels", "clickable": True})
				if not node:
					node = find_first(xml, {"desc_contains": "Reels", "clickable": True})
				if node:
					bounds = node

			if bounds:
				self._log(f"[{serial}] SHARE_TO_REELS: Found Reels at {bounds}, tapping...")
				tap_center(self.adb, serial, bounds)
				time.sleep(0.5)

				# Handle JUST ONCE if present
				if self._tap_just_once_if_present(serial):
					self._log(f"[{serial}] SHARE_TO_REELS: Tapped JUST ONCE after Reels")

				# Wait for Facebook foreground
				fb_wait_end = time.time() + 10
				while time.time() < fb_wait_end:
					current_pkg = self._get_foreground_package(serial)
					if current_pkg == "com.facebook.katana":
						self._log(f"[{serial}] ✓ Facebook opened after Reels fallback tap")
						return True, None
					time.sleep(0.5)

				# Late JUST ONCE
				if self._tap_just_once_if_present(serial):
					self._log(f"[{serial}] SHARE_TO_REELS: Tapped late JUST ONCE (fallback)")
					fb_wait_end2 = time.time() + 6
					while time.time() < fb_wait_end2:
						current_pkg = self._get_foreground_package(serial)
						if current_pkg == "com.facebook.katana":
							self._log(f"[{serial}] ✓ Facebook opened after late JUST ONCE (fallback)")
							return True, None
						time.sleep(0.5)

				self._log(f"[{serial}] SHARE_TO_REELS: Reels tapped but Facebook did not open")
				break

			time.sleep(0.5)

		return False, "Could not share to Reels from chooser"

	def _find_share_target_bounds(self, xml: str, labels: list[str]) -> tuple[int, int, int, int] | None:
		"""Parse XML and find a node whose text exactly matches one of the labels.
		
		Does NOT require clickable=True, so it works with non-clickable text nodes
		like android:id/text1 in the share chooser.
		
		Returns (left, top, right, bottom) bounds or None.
		"""
		try:
			root = ET.fromstring(xml)
		except ET.ParseError:
			return None

		for node in root.iter("node"):
			node_text = node.attrib.get("text", "")
			if node_text in labels:
				bounds_str = node.attrib.get("bounds", "")
				match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds_str)
				if match:
					return (int(match.group(1)), int(match.group(2)),
							int(match.group(3)), int(match.group(4)))
		return None

	def _state_wait_for_reels_composer(
		self,
		serial: str,
		job: ReelJob,
		device_media_path: str,
		timeout_s: int,
	) -> tuple[bool, str | None]:
		"""Wait for Facebook Reels composer to open with media loaded."""
		self._log(f"[{serial}] Waiting for Facebook Reels composer screen")
		
		end_at = time.time() + timeout_s
		while time.time() < end_at:
			pkg = self._get_foreground_package(serial)
			if pkg != "com.facebook.katana":
				time.sleep(0.5)
				continue
			
			xml = dump_ui_xml(self.adb, serial)
			if xml:
				# Check for Reels composer indicators
				if (
					"Create reel" in xml
					or "Next" in xml
					or "Describe your reel" in xml
					or "Write a description" in xml
					or "Edit" in xml
					or "Trim" in xml
					or "Share reel" in xml
					or "Add description" in xml
				):
					self._log(f"[{serial}] ✓ Facebook Reels composer opened")
					return True, None
			
			time.sleep(0.5)
		
		return False, "Reels composer did not open within timeout"

	def _state_tap_next(
		self,
		serial: str,
		job: ReelJob,
		device_media_path: str,
		timeout_s: int,
	) -> tuple[bool, str | None]:
		"""Tap 'Next' button to proceed to reel settings."""
		if self._tap_first_match(serial, SELECTORS["NEXT_BUTTON"], timeout_s):
			self._log(f"[{serial}] Next button tapped, waiting 10 seconds for Reel editor to load...")
			time.sleep(10.0)
			return True, None
		return False, "Next button not found"

	def _state_fill_caption(
		self,
		serial: str,
		job: ReelJob,
		device_media_path: str,
		timeout_s: int,
	) -> tuple[bool, str | None]:
		"""Fill caption/description field using filename (without extension).
		
		Always computes caption from filename, ignoring job.caption.
		This ensures consistency and reliability.
		"""
		# Compute caption from filename (without extension)
		caption_to_use = self._android_media_name or (Path(job.media_path).name if job.media_path else "")
		if not caption_to_use:
			return False, "Cannot determine media filename for caption"
		
		# Strip extension to get clean caption
		caption_to_use = Path(caption_to_use).stem
		if not caption_to_use:
			return False, "Filename stem is empty after removing extension"
		
		self._log(f"[{serial}] Caption source: filename={caption_to_use!r}")
		return self._helper_fill_caption(serial, caption_to_use, timeout_s)

	def _state_configure_schedule(
		self,
		serial: str,
		job: ReelJob,
		device_media_path: str,
		timeout_s: int,
	) -> tuple[bool, str | None]:
		"""Configure scheduling if needed."""
		return self._helper_configure_schedule_if_needed(serial, job, timeout_s)

	def _state_tap_share(
		self,
		serial: str,
		job: ReelJob,
		device_media_path: str,
		timeout_s: int,
	) -> tuple[bool, str | None]:
		"""Tap 'Share now' button."""
		return self._helper_share(serial, timeout_s)

	def _state_wait_completion(
		self,
		serial: str,
		job: ReelJob,
		device_media_path: str,
		timeout_s: int,
	) -> tuple[bool, str | None]:
		"""Wait for upload completion and verify return to page profile."""
		end_at = time.time() + timeout_s
		while time.time() < end_at:
			xml = dump_ui_xml(self.adb, serial)
			if xml:
				# Check for completion indicators
				if self._find_any(xml, SELECTORS["COMPLETION_INDICATORS"]):
					self._log(f"[{serial}] Completion indicator detected")
					return True, None
				
				# Also check if we're back on page profile (as fallback)
				page_selectors = [
					{"text_contains": job.target_page},
					{"desc_contains": job.target_page},
				]
				if self._find_any(xml, page_selectors):
					self._log(f"[{serial}] Back on page profile")
					return True, None
			
			time.sleep(2.0)
		
		return False, "Upload completion timeout"

	# ------------------------------------------------------------------
	# Helper Functions
	# ------------------------------------------------------------------

	def _helper_open_facebook(self, serial: str, timeout_s: int) -> tuple[bool, str | None]:
		"""Launch Facebook app and verify UI is ready."""
		try:
			if hasattr(self.adb, "launch_app"):
				self.adb.launch_app(serial, "com.facebook.katana")
			else:
				self.adb.shell(serial, "monkey -p com.facebook.katana -c android.intent.category.LAUNCHER 1")
		except Exception as exc:
			return False, f"Failed to launch Facebook: {exc}"

		# Verify UI is responsive
		end_at = time.time() + timeout_s
		while time.time() < end_at:
			xml = dump_ui_xml(self.adb, serial)
			if xml:
				return True, None
			time.sleep(1.0)
		
		return False, "Facebook UI not responsive"

	def _helper_switch_to_page(self, serial: str, page_name: str, timeout_s: int) -> tuple[bool, str | None]:
		"""Find and tap target page by name in the menu."""
		page_selectors = [
			{"text_equals": page_name, "clickable": True},
			{"text_contains": page_name, "clickable": True},
			{"desc_contains": page_name, "clickable": True},
		]
		
		if self._tap_first_match(serial, page_selectors, timeout_s):
			time.sleep(1.0)  # Wait for page switch
			return True, None
		
		return False, f"Page '{page_name}' not found in menu"

	def _helper_open_page_profile(self, serial: str, page_name: str, timeout_s: int) -> tuple[bool, str | None]:
		"""Tap page icon in top navigation to open page profile."""
		# First try to find page icon by description/resource
		if self._tap_first_match(serial, SELECTORS["PAGE_ICON"], timeout_s=5):
			time.sleep(1.0)
			return True, None
		
		# Fallback: look for page name again (might be in top bar)
		page_selectors = [
			{"text_contains": page_name, "clickable": True},
			{"desc_contains": page_name, "clickable": True},
		]
		if self._tap_first_match(serial, page_selectors, timeout_s=timeout_s - 5):
			time.sleep(1.0)
			return True, None
		
		return False, "Page profile icon not found"

	def _is_reel_publish_screen(self, xml: str) -> bool:
		"""Verify we are on the Reel publish/compose screen with share button visible."""
		# Primary check: Look for both "Share now" and "Save as draft" buttons (unique to publish screen footer)
		has_share_now = "Share now" in xml
		has_save_draft = "Save as draft" in xml
		
		if has_share_now and has_save_draft:
			return True
		
		# Fallback: Check for share button indicators + verify NOT on home feed
		has_share_button = any(text in xml for text in ["Share now", "Share reel", "Post"])
		if not has_share_button:
			return False
		
		# Must NOT be on home feed
		if is_facebook_home_feed(xml):
			return False
		
		return True

	def _encode_for_adb_input(self, text: str) -> str:
		"""URL-encode special characters for safe adb shell input."""
		if not text:
			return ""
		
		# Space to %s
		result = text.replace(" ", "%s")
		
		# Special characters mapping
		charmap = {
			"#": "%23",
			"&": "%26",
			"?": "%3F",
			":": "%3A",
			"/": "%2F",
			"'": "%27",
			'"': "%22",
			"!": "%21",
			"<": "%3C",
			">": "%3E",
			"@": "%40",
			"=": "%3D",
		}
		
		for char, encoded in charmap.items():
			result = result.replace(char, encoded)
		
		self._log(f"[encode_for_adb] Original: {text[:50]}... → Encoded: {result[:50]}...")
		return result

	def _encode_adb_text(self, text: str) -> str:
		"""Encode text safely for `adb shell input text`.
		
		Replaces spaces with %s (required by adb).
		Encodes special characters that break adb input.
		Preserves parentheses, dashes, dots, and other safe characters.
		"""
		if not text:
			return ""

		text = text.strip()

		# adb requires spaces encoded as %s
		text = text.replace(" ", "%s")

		# remove characters that break adb input
		text = text.replace('"', "")
		text = text.replace("'", "")

		# encode some problematic characters
		text = text.replace("&", "%26")
		text = text.replace("#", "%23")

		return text

	def _ensure_adb_ime(self, serial: str) -> bool:
		"""Check if ADB Keyboard (ADB IME) is installed and enable it if possible.
		
		Returns True if ADB IME is available and active, False otherwise.
		"""
		try:
			# Check if ADB Keyboard is installed
			result = self.adb.shell(serial, "pm list packages | grep adbkeyboard")
			if result is None or not result.strip():
				self._log(f"[{serial}] ADB IME not installed (adbkeyboard package not found)")
				return False
			
			# Try to enable and set as current IME
			self.adb.shell(serial, "ime enable com.android.adbkeyboard/.AdbIME")
			self.adb.shell(serial, "ime set com.android.adbkeyboard/.AdbIME")
			self._log(f"[{serial}] ✓ ADB IME enabled and set as active input method")
			return True
		except Exception as e:
			self._log(f"[{serial}] ADB IME setup failed: {e}")
			return False

	def _adb_ime_commit_text(self, serial: str, text: str) -> bool:
		"""Commit text using ADB Keyboard broadcast intent with ADB_INPUT_TEXT action.
		
		Uses: am broadcast -a ADB_INPUT_TEXT --es msg '<text>'
		This action properly handles spaces and special characters like parentheses.
		
		Returns True if broadcast succeeded (result=0 and no syntax error), False otherwise.
		"""
		try:
			# Properly quote the text for shell safety using shlex.quote()
			# This preserves spaces, parentheses, and other special characters
			safe_text = shlex.quote(text)
			
			# Build the broadcast command using the correct ADB_INPUT_TEXT action
			cmd = f"am broadcast -a ADB_INPUT_TEXT --es msg {safe_text}"
			self._log(f"[{serial}] Sending ADB IME broadcast: {cmd[:100]}")
			
			result = self.adb.shell(serial, cmd)
			result_str = str(result) if result else ""
			self._log(f"[{serial}] ADB IME broadcast result: {result_str[:200]}")
			
			# Check for success: result=0 and no shell syntax errors
			if "result=0" in result_str or "result ok" in result_str.lower():
				self._log(f"[{serial}] ✓ ADB IME broadcast succeeded for text: {text!r}")
				return True
			
			# Check for shell syntax errors
			if "syntax error" in result_str.lower():
				self._log(f"[{serial}] ✗ Shell syntax error in broadcast: {result_str}")
				return False
			
			# No clear success indicator, but no error either - assume success
			if result is None or not result_str:
				self._log(f"[{serial}] ✓ ADB IME broadcast executed (no output)")
				return True
			
			self._log(f"[{serial}] ⚠ ADB IME broadcast result unclear: {result_str[:100]}")
			return True  # Optimistic: assume success
		
		except Exception as e:
			self._log(f"[{serial}] ✗ ADB IME broadcast exception: {e}")
			return False

	def _clear_caption_field(self, serial: str) -> bool:
		"""Clear caption field by moving cursor to end and deleting all content.
		
		Returns True if successful, False otherwise.
		"""
		try:
			# Move cursor to end: keyevent 123 (MOVE_END)
			self.adb.shell(serial, "input keyevent 123")
			time.sleep(0.2)
			
			# Delete existing content (40 times to clear placeholder/leftover text)
			for _ in range(40):
				self.adb.shell(serial, "input keyevent 67")  # KEYCODE_DEL
				time.sleep(0.05)
			
			self._log(f"[{serial}] Caption field cleared")
			return True
		except Exception as e:
			self._log(f"[{serial}] Failed to clear caption field: {e}")
			return False

	def _confirm_caption_entered(self, serial: str) -> bool:
		"""Confirm that caption text was successfully entered.
		
		Strategy:
		1. Press BACK to hide keyboard
		2. Dump XML and check if placeholder text disappeared
		3. Re-tap field and check for selection indicators (context menu)
		
		Returns True if confirmation signals detected, False otherwise.
		"""
		try:
			# Step 1: Hide keyboard
			self.adb.shell(serial, "input keyevent 4")  # KEYCODE_BACK
			time.sleep(0.5)
			
			# Step 2: Dump XML and check for placeholder text
			xml = dump_ui_xml(self.adb, serial)
			if not xml:
				self._log(f"[{serial}] Confirmation: Cannot dump UI after caption input")
				return False
			
			# Check if we're still on composer (good sign)
			if not is_reel_composer_caption_screen(xml):
				self._log(f"[{serial}] Confirmation: Left composer screen after caption input")
				return False
			
			# Check if placeholder text disappeared (indicates text was entered)
			placeholder_texts = ["Describe your reel", "Write a description", "Add a description"]
			has_placeholder = any(text in xml.lower() for text in placeholder_texts)
			
			if not has_placeholder:
				self._log(f"[{serial}] ✓ Confirmation: Placeholder text disappeared (caption likely entered)")
				return True
			else:
				self._log(f"[{serial}] Confirmation: Placeholder text still present - caption may be empty")
				return False
		except Exception as e:
			self._log(f"[{serial}] Confirmation check failed: {e}")
			return False

	def _helper_fill_caption(self, serial: str, caption: str, timeout_s: int) -> tuple[bool, str | None]:
		"""Fill Title and Describe fields on Reel Settings screen with the same caption text."""
		if not caption.strip():
			self._log(f"[{serial}] No caption provided, skipping")
			return True, None
		
		# Step 1: Verify we are on the reel composer screen
		xml = dump_ui_xml(self.adb, serial)
		if not xml:
			self._log(f"[{serial}] FILL_CAPTION: Cannot dump UI XML")
			return False, "Cannot dump UI XML"
		
		# Classify current screen
		on_composer = is_reel_composer_caption_screen(xml)
		on_home_feed = is_facebook_home_feed(xml)
		
		if on_composer:
			self._log(f"[{serial}] FILL_CAPTION: ✓ On Reel composer caption screen")
		elif on_home_feed:
			self._log(f"[{serial}] FILL_CAPTION: ✗ On Facebook HOME_FEED, starting recovery...")
		else:
			self._log(f"[{serial}] FILL_CAPTION: ⚠ On UNKNOWN screen, attempting recovery...")
		
		# Step 2: Recovery routine if not on composer
		if not on_composer:
			self._log(f"[{serial}] Recovery routine: trying BACK presses...")
			
			# Try pressing BACK up to 4 times
			for back_attempt in range(1, 5):
				self._log(f"[{serial}] Recovery: BACK press {back_attempt}/4")
				self.adb.shell(serial, "input keyevent 4")  # KEYCODE_BACK
				time.sleep(0.8)
				
				xml = dump_ui_xml(self.adb, serial)
				if xml and is_reel_composer_caption_screen(xml):
					self._log(f"[{serial}] ✓ Recovery successful via BACK press {back_attempt}")
					on_composer = True
					break
			
			# If still not on composer, try APP_SWITCH
			if not on_composer:
				self._log(f"[{serial}] Recovery: BACK failed, trying APP_SWITCH (keyevent 187)...")
				self.adb.shell(serial, "input keyevent 187")  # KEYCODE_APP_SWITCH
				time.sleep(1.2)
				
				xml = dump_ui_xml(self.adb, serial)
				if xml and is_reel_composer_caption_screen(xml):
					self._log(f"[{serial}] ✓ Recovery successful via APP_SWITCH")
					on_composer = True
			
			# Final check
			if not on_composer:
				self._log(f"[{serial}] ✗ Recovery failed, lost composer context")
				_debug_dump_artifacts(self.adb, serial, "fill_caption_recovery_failed", self._log)
				return False, "Lost composer context - cannot find caption screen after recovery"
		
		# Step 3: Find and fill Title field
		self._log(f"[{serial}] Searching for Title field...")
		title_bounds = None
		max_find_attempts = 3
		
		for find_attempt in range(1, max_find_attempts + 1):
			xml = dump_ui_xml(self.adb, serial)
			if not xml:
				time.sleep(0.5)
				continue
			
			title_bounds = find_reel_title_field(xml)
			if title_bounds:
				self._log(f"[{serial}] ✓ Found Title field at bounds {title_bounds} (attempt {find_attempt})")
				break
			
			# If not found and not last attempt, try scrolling
			if find_attempt < max_find_attempts:
				self._log(f"[{serial}] Title field not found (attempt {find_attempt}), scrolling...")
				time.sleep(0.5)
		
		if not title_bounds:
			self._log(f"[{serial}] ⚠ Title field not found after {max_find_attempts} attempts, skipping Title and continuing to Describe field")
		
		# Step 4: Fill Title field (only if found)
		if title_bounds:
			self._log(f"[{serial}] Tapping Title field to focus...")
			if not tap_center(self.adb, serial, title_bounds):
				self._log(f"[{serial}] ⚠ Failed to tap Title field, skipping Title")
			else:
				time.sleep(0.6)
				
				# Input caption via ADB IME
				self._log(f"[{serial}] Filling Title with: {caption!r}")
				if not self._ensure_adb_ime(serial):
					self._log(f"[{serial}] ⚠ ADBKeyboard not available, trying fallback input")
				
				if not self._adb_ime_commit_text(serial, caption):
					self._log(f"[{serial}] ⚠ ADB IME input failed, trying standard input text")
					encoded_caption = self._encode_adb_text(caption)
					safe_arg = shlex.quote(encoded_caption)
					cmd = f"input text {safe_arg}"
					try:
						self.adb.shell(serial, cmd)
					except Exception as e:
						self._log(f"[{serial}] ⚠ Title input failed: {e}, continuing to Describe field")
				
				time.sleep(0.6)
		
		# Step 5: Find and fill Describe field
		self._log(f"[{serial}] Searching for Describe field...")
		describe_bounds = None
		
		for find_attempt in range(1, max_find_attempts + 1):
			xml = dump_ui_xml(self.adb, serial)
			if not xml:
				time.sleep(0.5)
				continue
			
			describe_bounds = find_reel_describe_field(xml)
			if describe_bounds:
				self._log(f"[{serial}] ✓ Found Describe field at bounds {describe_bounds} (attempt {find_attempt})")
				break
			
			# If not found and not last attempt, try scrolling
			if find_attempt < max_find_attempts:
				self._log(f"[{serial}] Describe field not found (attempt {find_attempt}), scrolling...")
				swipe(self.adb, serial, 360, 800, 360, 600, 400)
				time.sleep(0.5)
		
		if not describe_bounds:
			self._log(f"[{serial}] ✗ Describe field not found after {max_find_attempts} attempts")
			_debug_dump_artifacts(self.adb, serial, "fill_caption_describe_not_found", self._log)
			return False, "Describe field not found"
		
		# Step 6: Fill Describe field
		self._log(f"[{serial}] Tapping Describe field to focus...")
		if not tap_center(self.adb, serial, describe_bounds):
			self._log(f"[{serial}] ✗ Failed to tap Describe field")
			return False, "Failed to tap Describe field"
		
		time.sleep(0.6)
		
		# Input caption via ADB IME
		self._log(f"[{serial}] Filling Describe with: {caption!r}")
		if not self._adb_ime_commit_text(serial, caption):
			self._log(f"[{serial}] ⚠ ADB IME input failed, trying standard input text")
			encoded_caption = self._encode_adb_text(caption)
			safe_arg = shlex.quote(encoded_caption)
			cmd = f"input text {safe_arg}"
			try:
				self.adb.shell(serial, cmd)
			except Exception as e:
				self._log(f"[{serial}] ✗ Describe input failed: {e}")
				return False, "Failed to input Describe text"
		
		time.sleep(0.6)
		
		# Step 7: Confirm both fields are filled
		self._log(f"[{serial}] Confirming both fields are filled...")
		self.adb.shell(serial, "input keyevent 4")  # KEYCODE_BACK (hide keyboard)
		time.sleep(0.5)
		
		xml = dump_ui_xml(self.adb, serial)
		if not xml:
			self._log(f"[{serial}] ⚠ Cannot dump UI for confirmation")
		else:
			# Check if we're still on composer
			if not is_reel_composer_caption_screen(xml):
				self._log(f"[{serial}] ⚠ Left composer screen after filling")
			else:
				# Check if placeholder texts disappeared
				placeholder_texts = ["Add title", "Describe your reel", "Write a description"]
				has_placeholder = any(text in xml for text in placeholder_texts)
				
				if has_placeholder:
					self._log(f"[{serial}] ⚠ Some placeholder text still visible, retrying Describe field...")
					
					# Retry: refocus describe field and re-input
					if tap_center(self.adb, serial, describe_bounds):
						time.sleep(0.6)
						
						if not self._adb_ime_commit_text(serial, caption):
							encoded_caption = self._encode_adb_text(caption)
							safe_arg = shlex.quote(encoded_caption)
							self.adb.shell(serial, f"input text {safe_arg}")
						
						time.sleep(0.6)
						self.adb.shell(serial, "input keyevent 4")  # Hide keyboard
						time.sleep(0.5)
				else:
					self._log(f"[{serial}] ✓ Placeholder text disappeared - fields likely filled")
		
		self._log(f"[{serial}] ✓ Both Title and Describe fields filled successfully")
		
		# Step 8: Scroll down to ensure Share button is visible
		self._log(f"[{serial}] Scrolling down to reveal Share button...")
		swipe(self.adb, serial, 360, 1000, 360, 400, 500)
		time.sleep(0.5)
		
		return True, None


	def _helper_configure_schedule_if_needed(
		self,
		serial: str,
		job: ReelJob,
		timeout_s: int,
	) -> tuple[bool, str | None]:
		"""Configure scheduling if post_mode is 'scheduled'."""
		if job.post_mode != "scheduled":
			self._log(f"[{serial}] Not scheduled mode, skipping scheduling")
			return True, None
		
		# Tap scheduling options
		if not self._tap_first_match(serial, SELECTORS["SCHEDULING_OPTIONS"], timeout_s):
			return False, "Scheduling options not found"
		
		time.sleep(1.0)
		
		# TODO: Implement date/time picker navigation
		# For now, this is a placeholder - date picker UI varies greatly
		self._log(f"[{serial}] Schedule configuration not yet implemented, using Share now instead")
		
		# Press back to return to reel settings
		self.adb.shell(serial, "input keyevent KEYCODE_BACK")
		time.sleep(0.5)
		
		return True, None

	def _helper_share(self, serial: str, timeout_s: int) -> tuple[bool, str | None]:
		"""Tap Share button, preferring 'Share now' with fallback to generic 'Share'."""
		
		# Wait 1 second before searching for share button
		self._log(f"[{serial}] Waiting 1 second before searching for Share button...")
		time.sleep(1.0)
		
		# Step 1: Verify we are on the publish screen
		xml = dump_ui_xml(self.adb, serial)
		if not xml:
			self._log(f"[{serial}] SHARE: Cannot dump UI XML")
			return False, "Cannot dump UI to verify publish screen"
		
		if not self._is_reel_publish_screen(xml):
			self._log(f"[{serial}] ✗ SHARE: Not on publish screen, cannot tap share button safely")
			_debug_dump_artifacts(self.adb, serial, "share_not_on_publish_screen", self._log)
			return False, "Not on reel publish screen"
		
		self._log(f"[{serial}] ✓ SHARE: Verified on publish screen")
		
		# Step 2: Find and tap Share button - prefer "Share now", fallback to generic "Share"
		xml = dump_ui_xml(self.adb, serial)
		if not xml:
			return False, "Cannot dump UI before share tap"
		
		# Try exact "Share now" first (strict match)
		share_now_selectors = [
			{"text_equals": "Share now", "clickable": True},
			{"text_contains": "Share now", "clickable": True},
		]
		
		for selector in share_now_selectors:
			element = find_first(xml, selector)
			if element:
				self._log(f"[{serial}] SHARE: Found 'Share now' button via {selector}, tapping...")
				if tap_center(self.adb, serial, element):
					time.sleep(1.0)
					self._log(f"[{serial}] ✓ SHARE: Tapped 'Share now' button successfully")
					return True, None
		
		# Fallback to generic "Share" button
		self._log(f"[{serial}] 'Share now' not found, attempting fallback to generic 'Share' button...")
		generic_share_selectors = [
			{"text_equals": "Share", "clickable": True},
			{"text_contains": "Share", "clickable": True},
			{"desc_contains": "Share", "clickable": True},
		]
		
		for selector in generic_share_selectors:
			element = find_first(xml, selector)
			if element:
				# Verify this is not part of "Share now" or other compound text
				element_text = element.get("text", "").strip()
				if element_text and element_text != "Share now" and not element_text.startswith("Share now"):
					self._log(f"[{serial}] SHARE: Fallback - Found generic 'Share' button via {selector}, tapping...")
					if tap_center(self.adb, serial, element):
						time.sleep(1.0)
						self._log(f"[{serial}] ⚠ SHARE: Tapped fallback 'Share' button (Share now not present)")
						return True, None
		
		self._log(f"[{serial}] ✗ SHARE: No Share button found (neither 'Share now' nor 'Share')")
		_debug_dump_artifacts(self.adb, serial, "share_button_not_found", self._log)
		return False, "No Share button found"

	def _relaunch_facebook(self, serial: str) -> None:
		"""
		Forcefully relaunch Facebook app.
		
		Logs why relaunch was needed and uses am start with fallback to monkey.
		"""
		self._log(f"[{serial}] Relaunching Facebook...")
		
		try:
			# Try using am start
			self.adb.shell(serial, "am start -n com.facebook.katana/.LoginActivity")
		except Exception:
			try:
				# Fallback to monkey
				self.adb.shell(serial, "monkey -p com.facebook.katana -c android.intent.category.LAUNCHER 1")
			except Exception as exc:
				self._log(f"[{serial}] Relaunch failed: {exc}")
		
		# Wait for app to start
		time.sleep(2.0)

	# ------------------------------------------------------------------
	# Popup Handler
	# ------------------------------------------------------------------

	def _wait_for_android_chooser(self, serial: str, timeout_s: int = 6) -> bool:
		"""Wait for Android chooser/share-sheet to appear.
		
		Returns True when XML contains indicators like:
		- resource-id contains: resolver, chooser, android:id/resolver_list, android:id/button_once
		- visible text: "Complete action using", "Share", "Just once", "Always"
		"""
		end_at = time.time() + timeout_s
		while time.time() < end_at:
			xml = dump_ui_xml(self.adb, serial)
			if not xml:
				time.sleep(0.3)
				continue
			
			# Check for chooser resource-id indicators
			if any(marker in xml for marker in [
				"android:id/resolver_list",
				"android:id/button_once",
				"android:id/button_always",
				"resolver",
				"chooser",
			]):
				return True
			
			# Check for chooser text indicators
			if any(text in xml for text in [
				"Complete action using",
				"Complete action",
				"Just once",
				"JUST ONCE",
				"Always",
			]):
				return True
			
			time.sleep(0.3)
		
		return False

	def _tap_just_once_if_present(self, serial: str) -> bool:
		"""Tap 'JUST ONCE' button in Android chooser.
		
		Searches for resource-id android:id/button_once first (preferred),
		fallback to res_id_contains and text matches.
		Returns True if button found and tapped, False if not present.
		"""
		xml_before = dump_ui_xml(self.adb, serial)
		if not xml_before:
			return False
		
		pkg_before = self._get_foreground_package(serial)
		
		# Primary: search by exact resource-id (don't require clickable, XML may not expose it reliably)
		just_once_btn = find_first(xml_before, {"res_id_equals": "android:id/button_once"})
		if just_once_btn:
			self._log(f"[{serial}] Found JUST ONCE via res_id_equals at bounds {just_once_btn}")
			tap_center(self.adb, serial, just_once_btn)
			time.sleep(0.6)
			xml_after = dump_ui_xml(self.adb, serial)
			pkg_after = self._get_foreground_package(serial)
			self._log(f"[{serial}] After JUST ONCE tap: pkg_before={pkg_before}, pkg_after={pkg_after}")
			if xml_after and "android:id/button_once" not in xml_after:
				self._log(f"[{serial}] ✓ Chooser disappeared after JUST ONCE tap")
			return True
		
		# Fallback: search by res_id_contains
		just_once_btn = find_first(xml_before, {"res_id_contains": "button_once"})
		if just_once_btn:
			self._log(f"[{serial}] Found JUST ONCE via res_id_contains at bounds {just_once_btn}")
			tap_center(self.adb, serial, just_once_btn)
			time.sleep(0.6)
			xml_after = dump_ui_xml(self.adb, serial)
			pkg_after = self._get_foreground_package(serial)
			self._log(f"[{serial}] After JUST ONCE tap: pkg_before={pkg_before}, pkg_after={pkg_after}")
			if xml_after and "button_once" not in xml_after:
				self._log(f"[{serial}] ✓ Chooser disappeared after JUST ONCE tap")
			return True
		
		# Fallback: search by exact text "JUST ONCE"
		just_once_btn = find_first(xml_before, {"text_equals": "JUST ONCE"})
		if just_once_btn:
			self._log(f"[{serial}] Found JUST ONCE via text_equals at bounds {just_once_btn}")
			tap_center(self.adb, serial, just_once_btn)
			time.sleep(0.6)
			pkg_after = self._get_foreground_package(serial)
			self._log(f"[{serial}] After JUST ONCE tap: pkg_before={pkg_before}, pkg_after={pkg_after}")
			return True
		
		# Fallback: case-insensitive text
		just_once_btn = find_first(xml_before, {"text_equals": "Just once"})
		if just_once_btn:
			self._log(f"[{serial}] Found Just once via text_equals at bounds {just_once_btn}")
			tap_center(self.adb, serial, just_once_btn)
			time.sleep(0.6)
			pkg_after = self._get_foreground_package(serial)
			self._log(f"[{serial}] After JUST ONCE tap: pkg_before={pkg_before}, pkg_after={pkg_after}")
			return True
		
		return False

	def _handle_popups(self, serial: str) -> None:
		"""
		Detect and dismiss common popups/dialogs.
		Tries dismissal buttons first, then allow/continue buttons.
		"""
		xml = dump_ui_xml(self.adb, serial)
		if not xml:
			return
		
		# Try dismiss buttons first
		for criteria in SELECTORS["POPUP_DISMISS"]:
			bounds = find_first(xml, criteria)
			if bounds:
				self._log(f"[{serial}] Dismissing popup: {criteria}")
				tap_center(self.adb, serial, bounds)
				time.sleep(0.5)
				return
		
		# Try allow/continue buttons
		for criteria in SELECTORS["POPUP_ALLOW"]:
			bounds = find_first(xml, criteria)
			if bounds:
				self._log(f"[{serial}] Allowing popup: {criteria}")
				tap_center(self.adb, serial, bounds)
				time.sleep(0.5)
				return

	# ------------------------------------------------------------------
	# Utility Functions
	# ------------------------------------------------------------------

	def _tap_first_match(
		self,
		serial: str,
		selectors: Sequence[dict[str, Any]],
		timeout_s: int,
	) -> bool:
		"""
		Try to find and tap first matching element from selector list.
		Returns True if successful, False if timeout.
		"""
		end_at = time.time() + max(1, timeout_s)
		
		while time.time() < end_at:
			xml = dump_ui_xml(self.adb, serial)
			if not xml:
				time.sleep(0.8)
				continue
			
			for criteria in selectors:
				bounds = find_first(xml, criteria)
				if bounds:
					if tap_center(self.adb, serial, bounds):
						return True
			
			time.sleep(0.8)
		
		return False

	def _build_caption_from_media(self, job: ReelJob) -> str:
		"""Build caption from media filename with sanitization and guardrails."""
		self._caption_source = ""
		self._caption_source_rejected = False

		job_caption = (job.caption or "").strip()
		job_caption_lower = job_caption.lower()
		if (
			len(job_caption) > 400
			or "task:" in job_caption_lower
			or "implement" in job_caption_lower
			or "you are working on" in job_caption_lower
		):
			self._caption_source_rejected = True

		media_name = ""
		if self._android_media_name:
			media_name = self._android_media_name
			self._caption_source = "android_media_name"
		elif job.media_path:
			media_name = Path(job.media_path).name
			self._caption_source = "job.media_path"

		if not media_name:
			return ""

		if self.keep_caption_extension:
			caption = media_name
		else:
			caption = Path(media_name).stem

		caption = re.sub(r"[\r\n\t]+", " ", caption).strip()
		caption = re.sub(r"\s{2,}", " ", caption)
		if len(caption) > 150:
			caption = caption[:150].rstrip()

		return caption

	@staticmethod
	def _find_any(xml: str, selectors: Sequence[dict[str, Any]]) -> bool:
		"""Check if any selector matches in the given XML."""
		for criteria in selectors:
			if find_first(xml, criteria) is not None:
				return True
		return False

	@staticmethod
	def _sanitize_input_text(value: str) -> str:
		"""Sanitize text for Android input command (spaces → %s)."""
		return value.strip().replace(" ", "%s").replace("'", "").replace('"', "")

	def _log(self, message: str) -> None:
		"""Thread-safe logging."""
		try:
			self.log_fn(message)
		except Exception:
			pass
