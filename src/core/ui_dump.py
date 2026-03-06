from __future__ import annotations

from typing import Any, Mapping
from pathlib import Path
import re
import time
import xml.etree.ElementTree as ET


Bounds = tuple[int, int, int, int]


def _shell_quote(value: str) -> str:
	"""Safely quote a string for Android shell commands."""
	return "'" + str(value).replace("'", "'\"'\"'") + "'"


def _now_ts() -> int:
	return int(time.time())


def _debug_dump_artifacts(adb: Any, serial: str, prefix: str, log_fn: Any = None) -> None:
	"""Dump XML + screenshot to local workspace for failure debugging."""
	def _log(msg: str) -> None:
		if log_fn:
			try:
				log_fn(msg)
			except Exception:
				pass

	tag = f"{prefix}_{serial}_{time.strftime('%Y%m%d_%H%M%S')}"
	workspace = Path.cwd()
	xml_path = workspace / f"{tag}.xml"
	png_remote = f"/sdcard/{tag}.png"
	png_local = workspace / f"{tag}.png"

	try:
		xml = dump_ui_xml(adb, serial)
		if xml:
			xml_path.write_text(xml, encoding="utf-8")
			_log(f"[{serial}] Debug XML saved: {xml_path}")
	except Exception as exc:
		_log(f"[{serial}] Failed writing debug XML: {exc}")

	try:
		adb.shell(serial, f"screencap -p {_shell_quote(png_remote)}")
		if hasattr(adb, "_adb"):
			dev = adb._adb.device(serial=serial)
			dev.sync.pull(png_remote, str(png_local))
			adb.shell(serial, f"rm -f {_shell_quote(png_remote)}")
			_log(f"[{serial}] Debug screenshot saved: {png_local}")
		else:
			_log(f"[{serial}] Screenshot captured on device: {png_remote}")
	except Exception as exc:
		_log(f"[{serial}] Failed capturing screenshot: {exc}")


def dump_ui_xml(adb: Any, serial: str) -> str:
	"""Dump current UI hierarchy from device and return raw XML text.

	Uses:
	- ``uiautomator dump /sdcard/ui.xml``
	- ``cat /sdcard/ui.xml``
	"""
	try:
		adb.shell(serial, "uiautomator dump /sdcard/ui.xml")
	except Exception:
		# continue; second command may still succeed if file already exists
		pass

	try:
		xml_text = adb.shell(serial, "cat /sdcard/ui.xml")
	except Exception:
		return ""

	if not isinstance(xml_text, str):
		return ""

	return xml_text.strip()


def parse_bounds(bounds_text: str) -> Bounds:
	"""Parse Android bounds string ``[x1,y1][x2,y2]``.

	Raises ``ValueError`` on invalid input.
	"""
	if not isinstance(bounds_text, str):
		raise ValueError("bounds must be a string")

	match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds_text.strip())
	if not match:
		raise ValueError(f"invalid bounds format: {bounds_text!r}")

	x1, y1, x2, y2 = map(int, match.groups())
	return x1, y1, x2, y2


def find_first(xml: str, criteria: Mapping[str, Any]) -> Bounds | None:
	"""Find first node matching criteria and return its bounds.

	Supported criteria keys:
	- ``text_equals``: exact text match
	- ``text_contains``: substring in text
	- ``desc_equals``: exact content-desc match
	- ``desc_contains``: substring in content-desc
	- ``res_id_equals``: exact resource-id match
	- ``res_id_contains``: substring in resource-id
	- ``class_name``: exact class attribute match
	- ``clickable``: bool
	"""
	if not xml or not isinstance(xml, str):
		return None

	try:
		root = ET.fromstring(xml)
	except ET.ParseError:
		return None
	except Exception:
		return None

	text_equals = criteria.get("text_equals")
	text_contains = criteria.get("text_contains")
	desc_equals = criteria.get("desc_equals")
	desc_contains = criteria.get("desc_contains")
	res_id_equals = criteria.get("res_id_equals")
	res_id_contains = criteria.get("res_id_contains")
	class_name = criteria.get("class_name")
	clickable = criteria.get("clickable")

	for node in root.iter():
		attrs = node.attrib
		text = attrs.get("text", "")
		content_desc = attrs.get("content-desc", "")
		resource_id = attrs.get("resource-id", "")
		class_attr = attrs.get("class", "")
		clickable_attr = attrs.get("clickable", "false").lower() == "true"

		if text_equals is not None and text != str(text_equals):
			continue
		if text_contains is not None and str(text_contains) not in text:
			continue
		if desc_equals is not None and content_desc != str(desc_equals):
			continue
		if desc_contains is not None and str(desc_contains) not in content_desc:
			continue
		if res_id_equals is not None and resource_id != str(res_id_equals):
			continue
		if res_id_contains is not None and str(res_id_contains) not in resource_id:
			continue
		if class_name is not None and class_attr != str(class_name):
			continue
		if clickable is not None and bool(clickable) != clickable_attr:
			continue

		bounds_text = attrs.get("bounds")
		if not bounds_text:
			continue
		try:
			return parse_bounds(bounds_text)
		except ValueError:
			continue

	return None


def tap_center(adb: Any, serial: str, bounds: Bounds) -> bool:
	"""Tap center point of bounds rectangle."""
	try:
		x1, y1, x2, y2 = bounds
		cx = (x1 + x2) // 2
		cy = (y1 + y2) // 2
		adb.shell(serial, f"input tap {cx} {cy}")
		return True
	except Exception:
		return False


def swipe(adb: Any, serial: str, x1: int, y1: int, x2: int, y2: int, duration: int) -> bool:
	"""Perform swipe using Android input command."""
	try:
		adb.shell(serial, f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(duration)}")
		return True
	except Exception:
		return False


def find_facebook_hamburger(xml: str) -> Bounds | None:
	"""Find Facebook hamburger menu button.
	
	Strategy:
	1. Find node where content-desc contains "Facebook menu" and clickable="true"
	2. If not found, find node where content-desc contains "menu" (case-insensitive)
	   and clickable="true" and positioned in top-left (x1<150, y1<200)
	3. Return bounds tuple (x1, y1, x2, y2) or None
	"""
	if not xml or not isinstance(xml, str):
		return None
	
	try:
		root = ET.fromstring(xml)
	except (ET.ParseError, Exception):
		return None
	
	# Strategy 1: Look for "Facebook menu" in content-desc
	for node in root.iter():
		attrs = node.attrib
		content_desc = attrs.get("content-desc", "")
		clickable_attr = attrs.get("clickable", "false").lower() == "true"
		
		if "Facebook menu" in content_desc and clickable_attr:
			bounds_text = attrs.get("bounds")
			if bounds_text:
				try:
					return parse_bounds(bounds_text)
				except ValueError:
					continue
	
	# Strategy 2: Look for "menu" (case-insensitive) in top-left corner
	for node in root.iter():
		attrs = node.attrib
		content_desc = attrs.get("content-desc", "")
		clickable_attr = attrs.get("clickable", "false").lower() == "true"
		bounds_text = attrs.get("bounds")
		
		if not bounds_text or not clickable_attr:
			continue
		
		# Check if "menu" is in content-desc (case-insensitive)
		if "menu" not in content_desc.lower():
			continue
		
		# Parse bounds and check position
		try:
			x1, y1, x2, y2 = parse_bounds(bounds_text)
			if x1 < 150 and y1 < 200:
				return (x1, y1, x2, y2)
		except ValueError:
			continue
	
	return None


def find_create_button(xml: str) -> Bounds | None:
	"""Find Facebook 'Create' button in UI hierarchy.
	
	Looks for:
	- content-desc contains "Create"
	- clickable="true"
	"""
	if not xml or not isinstance(xml, str):
		return None
	
	try:
		root = ET.fromstring(xml)
	except (ET.ParseError, Exception):
		return None
	
	for node in root.iter():
		attrs = node.attrib
		content_desc = attrs.get("content-desc", "")
		clickable_attr = attrs.get("clickable", "false").lower() == "true"
		
		if "Create" in content_desc and clickable_attr:
			bounds_text = attrs.get("bounds")
			if bounds_text:
				try:
					return parse_bounds(bounds_text)
				except ValueError:
					continue
	
	return None


def find_top_bar(xml: str) -> Bounds | None:
	"""Find top navigation bar in Facebook UI.
	
	Returns bounds of topmost UI element (usually app bar).
	Used for stability checking.
	"""
	if not xml or not isinstance(xml, str):
		return None
	
	try:
		root = ET.fromstring(xml)
	except (ET.ParseError, Exception):
		return None
	
	# Find topmost element with small y1 (typically y1 < 100)
	min_y1 = float('inf')
	topmost_bounds = None
	
	for node in root.iter():
		attrs = node.attrib
		bounds_text = attrs.get("bounds")
		if not bounds_text:
			continue
		
		try:
			x1, y1, x2, y2 = parse_bounds(bounds_text)
			# Look for top bar area (top-left or top-center, small height)
			if y1 < 100 and y1 < min_y1 and (x2 - x1) > 50:
				min_y1 = y1
				topmost_bounds = (x1, y1, x2, y2)
		except ValueError:
			continue
	
	return topmost_bounds


def is_facebook_running(adb: Any, serial: str) -> bool:
	"""Check if Facebook is in foreground (current focused window).
	
	Uses: dumpsys window mCurrentFocus
	Returns True if com.facebook.katana is the focused package.
	"""
	try:
		output = adb.shell(serial, "dumpsys window | grep mCurrentFocus")
		if not output or not isinstance(output, str):
			return False
		
		# mCurrentFocus output looks like: mCurrentFocus=Window{...com.facebook.katana...}
		return "com.facebook.katana" in output
	except Exception:
		return False


def is_facebook_home_feed(xml: str) -> bool:
	"""Check if current screen is Facebook Home feed.
	
	Returns True if XML contains:
	- "Facebook menu" AND
	- "Create, Double tap to create a new post, story, or reel" AND
	- "Stories" AND
	- "Reels"
	"""
	if not xml or not isinstance(xml, str):
		return False
	
	has_fb_menu = "Facebook menu" in xml
	has_create_button = "Create, Double tap to create a new post, story, or reel" in xml
	has_stories = "Stories" in xml
	has_reels = "Reels" in xml
	
	return has_fb_menu and has_create_button and has_stories and has_reels


def is_reel_composer_caption_screen(xml: str) -> bool:
	"""Check if current screen is Reel composer caption/description screen.
	
	Returns True if XML contains any of:
	- "Describe your reel"
	- "Write a description"
	- "Write a caption"
	- "Add a caption"
	- "Share reel"
	- A visible android.widget.EditText (on composer screen)
	"""
	if not xml or not isinstance(xml, str):
		return False
	
	# Check for caption screen text indicators
	caption_indicators = [
		"Describe your reel",
		"Write a description",
		"Write a caption",
		"Add a caption",
		"Share reel",
		"Add description",
	]
	
	for indicator in caption_indicators:
		if indicator in xml:
			return True
	
	# Check for EditText on composer screen (fallback)
	try:
		root = ET.fromstring(xml)
		for node in root.iter():
			attrs = node.attrib
			class_attr = attrs.get("class", "")
			enabled = attrs.get("enabled", "false").lower() == "true"
			focusable = attrs.get("focusable", "false").lower() == "true"
			
			if class_attr == "android.widget.EditText" and enabled and focusable:
				return True
	except (ET.ParseError, Exception):
		pass
	
	return False


def find_caption_target(xml: str) -> Bounds | None:
	"""Find caption/description field on Reel composer screen.
	
	Strategies (in order):
	1. Find android.widget.EditText that is enabled and focusable
	2. Find clickable container with content-desc/text containing: "Describe", "description", "caption", "Write"
	
	Returns bounds tuple or None if not found.
	"""
	if not xml or not isinstance(xml, str):
		return None
	
	try:
		root = ET.fromstring(xml)
	except (ET.ParseError, Exception):
		return None
	
	# Strategy 1: Find EditText that's enabled and focusable
	for node in root.iter():
		attrs = node.attrib
		class_attr = attrs.get("class", "")
		enabled = attrs.get("enabled", "false").lower() == "true"
		focusable = attrs.get("focusable", "false").lower() == "true"
		
		if class_attr == "android.widget.EditText" and enabled and focusable:
			bounds_text = attrs.get("bounds")
			if bounds_text:
				try:
					return parse_bounds(bounds_text)
				except ValueError:
					continue
	
	# Strategy 2: Find clickable container with caption-related keywords
	caption_keywords = ["Describe", "description", "caption", "Write", "Add a caption"]
	
	for node in root.iter():
		attrs = node.attrib
		text = attrs.get("text", "")
		content_desc = attrs.get("content-desc", "")
		clickable = attrs.get("clickable", "false").lower() == "true"
		
		if not clickable:
			continue
		
		for keyword in caption_keywords:
			if keyword.lower() in text.lower() or keyword.lower() in content_desc.lower():
				bounds_text = attrs.get("bounds")
				if bounds_text:
					try:
						return parse_bounds(bounds_text)
					except ValueError:
						continue
	
	return None


def find_reel_title_field(xml: str) -> Bounds | None:
	"""Find the Title field on Reel Settings screen.
	
	Looks for EditText with text="Add title" or containing "title" text.
	
	Returns bounds tuple or None if not found.
	"""
	if not xml or not isinstance(xml, str):
		return None
	
	try:
		root = ET.fromstring(xml)
	except (ET.ParseError, Exception):
		return None
	
	# Strategy 1: Exact text match "Add title"
	for node in root.iter():
		attrs = node.attrib
		class_attr = attrs.get("class", "")
		text = attrs.get("text", "")
		
		if class_attr == "android.widget.EditText" and text == "Add title":
			bounds_text = attrs.get("bounds")
			if bounds_text:
				try:
					return parse_bounds(bounds_text)
				except ValueError:
					continue
	
	# Strategy 2: Text contains "title" (case-insensitive)
	for node in root.iter():
		attrs = node.attrib
		class_attr = attrs.get("class", "")
		text = attrs.get("text", "").lower()
		
		if class_attr == "android.widget.EditText" and "title" in text:
			bounds_text = attrs.get("bounds")
			if bounds_text:
				try:
					return parse_bounds(bounds_text)
				except ValueError:
					continue
	
	return None


def find_reel_describe_field(xml: str) -> Bounds | None:
	"""Find the Describe field on Reel Settings screen.
	
	Looks for EditText with:
	- long-clickable="true"
	- significant height (> 120 pixels)
	- positioned below title field (y1 >= some threshold)
	
	Returns bounds tuple or None if not found.
	"""
	if not xml or not isinstance(xml, str):
		return None
	
	try:
		root = ET.fromstring(xml)
	except (ET.ParseError, Exception):
		return None
	
	# Find all EditText candidates
	candidates = []
	
	for node in root.iter():
		attrs = node.attrib
		class_attr = attrs.get("class", "")
		long_clickable = attrs.get("long-clickable", "false").lower() == "true"
		bounds_text = attrs.get("bounds")
		
		if class_attr != "android.widget.EditText" or not long_clickable:
			continue
		
		if not bounds_text:
			continue
		
		try:
			x1, y1, x2, y2 = parse_bounds(bounds_text)
			height = y2 - y1
			
			# Filter: height > 120 pixels (describe field is much taller than title)
			if height > 120:
				candidates.append((x1, y1, x2, y2))
		except ValueError:
			continue
	
	# Return the first candidate (should be the describe field)
	if candidates:
		return candidates[0]
	
	return None


def open_account_switcher_from_menu(adb: Any, serial: str, target_page_name: str = "", log_fn: Any = None) -> bool:
	"""
	Open account switcher/page selector from Facebook menu panel.
	
	Uses coordinate-safe taps inside menu panel [0,48][632,1280] on 720x1280 screen.
	Avoids close overlay region (x>=632).
	
	Strategy:
	1. Tap at (560, 120) - top-right inside panel
	2. Wait up to 5s and verify account switcher opened by checking:
	   - "Go to Accounts Center" OR
	   - "Create Facebook profile" OR
	   - target_page_name (if provided)
	3. If not opened, retry once at (450, 120)
	
	Args:
		adb: ADB manager instance
		serial: Device serial
		target_page_name: Optional page name to verify in switcher
		log_fn: Optional logging function
	
	Returns:
		True if account switcher opened, False otherwise
	"""
	import time
	
	def _log(msg: str) -> None:
		if log_fn:
			try:
				log_fn(msg)
			except Exception:
				pass
	
	# Attempt 1: tap at (560, 120)
	tap_x1, tap_y1 = 560, 120
	_log(f"[{serial}] Account switcher: attempt 1, tap at ({tap_x1}, {tap_y1})")
	
	try:
		adb.shell(serial, f"input tap {tap_x1} {tap_y1}")
	except Exception as exc:
		_log(f"[{serial}] Tap failed: {exc}")
		return False
	
	time.sleep(0.5)
	
	# Verify account switcher opened
	_log(f"[{serial}] Verifying account switcher opened...")
	verify_end = time.time() + 5.0
	while time.time() < verify_end:
		xml = dump_ui_xml(adb, serial)
		if xml:
			# Check for account switcher indicators
			if "Go to Accounts Center" in xml:
				_log(f"[{serial}] ✓ Account switcher opened (found 'Go to Accounts Center')")
				return True
			if "Create Facebook profile" in xml:
				_log(f"[{serial}] ✓ Account switcher opened (found 'Create Facebook profile')")
				return True
			if target_page_name and target_page_name in xml:
				_log(f"[{serial}] ✓ Account switcher opened (found '{target_page_name}')")
				return True
		time.sleep(0.5)
	
	_log(f"[{serial}] Account switcher did not open, retrying...")
	
	# Attempt 2: retry at (450, 120)
	tap_x2, tap_y2 = 450, 120
	_log(f"[{serial}] Account switcher: attempt 2, tap at ({tap_x2}, {tap_y2})")
	
	try:
		adb.shell(serial, f"input tap {tap_x2} {tap_y2}")
	except Exception as exc:
		_log(f"[{serial}] Tap failed: {exc}")
		return False
	
	time.sleep(0.5)
	
	# Verify again
	_log(f"[{serial}] Verifying account switcher opened...")
	verify_end = time.time() + 5.0
	while time.time() < verify_end:
		xml = dump_ui_xml(adb, serial)
		if xml:
			if "Go to Accounts Center" in xml:
				_log(f"[{serial}] ✓ Account switcher opened (found 'Go to Accounts Center')")
				return True
			if "Create Facebook profile" in xml:
				_log(f"[{serial}] ✓ Account switcher opened (found 'Create Facebook profile')")
				return True
			if target_page_name and target_page_name in xml:
				_log(f"[{serial}] ✓ Account switcher opened (found '{target_page_name}')")
				return True
		time.sleep(0.5)
	
	_log(f"[{serial}] ✗ Account switcher failed to open after 2 attempts")
	return False


def open_profile_tab(adb: Any, serial: str, log_fn: Any = None, timeout_s: int = 20) -> bool:
	"""
	Open Facebook Profile tab from bottom navigation bar.
	
	Implements robust tab switching with multiple fallback strategies:
	- Checks if Profile tab already selected (returns immediately)
	- Searches by exact content-desc: "Profile, tab 5 of 5"
	- Falls back to partial match containing "Profile" and "tab"
	- Final fallback to coordinate tap at known bounds [576,48][720,136]
	- Retries up to 3 times with verification after each tap
	- Handles app crashes and relaunches
	
	Target element:
	- class: android.view.View
	- content-desc: "Profile, tab 5 of 5"
	- clickable: true
	- selected: true/false
	- bounds: [576,48][720,136] (center: 648,92)
	
	Args:
		adb: ADB manager instance
		serial: Device serial number
		log_fn: Optional logging function for debug messages
		timeout_s: Total timeout in seconds (default 20s)
	
	Returns:
		True if Profile tab opened and verified
	
	Raises:
		Exception: "Profile tab not found in xml" if all attempts exhausted
	"""
	import time
	from pathlib import Path
	
	def _log(msg: str) -> None:
		if log_fn:
			try:
				log_fn(msg)
			except Exception:
				pass
	
	start_time = time.time()
	max_main_retries = 3
	PROFILE_BOUNDS = (576, 48, 720, 136)  # Updated bounds
	center_x = (PROFILE_BOUNDS[0] + PROFILE_BOUNDS[2]) // 2  # 648
	center_y = (PROFILE_BOUNDS[1] + PROFILE_BOUNDS[3]) // 2  # 92
	
	_log(f"[{serial}] ═══ OPEN_PROFILE_TAB START ═══")
	
	# Pre-check: Ensure Facebook is running
	if not _ensure_facebook_running(adb, serial, log_fn):
		_log(f"[{serial}] ✗ Failed to ensure Facebook is running")
		raise Exception("Facebook app not running")
	
	# Wait for feed UI to stabilize (no loading overlays)
	_log(f"[{serial}] Waiting for feed UI to stabilize...")
	if not _wait_for_stable_ui(adb, serial, log_fn, timeout=8):
		_log(f"[{serial}] ⚠ UI may not be stable, proceeding anyway")
	
	# Main retry loop
	for main_attempt in range(1, max_main_retries + 1):
		if time.time() - start_time > timeout_s:
			_log(f"[{serial}] ✗ Timeout exceeded ({timeout_s}s)")
			_dump_xml_on_failure(adb, serial, log_fn)
			raise Exception("Profile tab not found in xml - timeout exceeded")
		
		_log(f"[{serial}] ─── Attempt {main_attempt}/{max_main_retries} ───")
		
		# Check if Facebook still running
		if not is_facebook_running(adb, serial):
			_log(f"[{serial}] ⚠ Facebook not in foreground, relaunching...")
			if not _ensure_facebook_running(adb, serial, log_fn):
				_log(f"[{serial}] ✗ Failed to relaunch Facebook")
				continue
		
		# Get fresh UI XML
		xml = dump_ui_xml(adb, serial)
		if not xml:
			_log(f"[{serial}] ✗ Cannot dump UI XML")
			time.sleep(0.5)
			continue
		
		# Strategy 1: Try exact content-desc match with selected state check
		_log(f"[{serial}] Strategy 1: Searching for exact content-desc 'Profile, tab 5 of 5'")
		profile_node = _find_profile_tab_node(xml, log_fn)
		
		if profile_node:
			bounds, selected, clickable = profile_node
			_log(f"[{serial}] ✓ Found profile tab at {bounds}, selected={selected}, clickable={clickable}")
			
			# If already selected, we're done
			if selected:
				_log(f"[{serial}] ✓✓ Profile tab already selected!")
				return True
			
			# Not selected, try to tap it
			if _tap_and_verify(adb, serial, bounds, log_fn):
				_log(f"[{serial}] ✓✓ Profile tab opened successfully (exact match)")
				return True
			else:
				_log(f"[{serial}] ✗ Tap succeeded but verification failed")
		else:
			_log(f"[{serial}] ✗ Exact content-desc not found")
		
		# Strategy 2: Try partial match (contains "Profile" AND "tab 5")
		_log(f"[{serial}] Strategy 2: Searching for partial match (contains 'Profile' & 'tab 5')")
		bounds_partial = _find_profile_tab_partial(xml, log_fn)
		if bounds_partial:
			_log(f"[{serial}] ✓ Found profile tab by partial match at {bounds_partial}")
			if _tap_and_verify(adb, serial, bounds_partial, log_fn):
				_log(f"[{serial}] ✓✓ Profile tab opened successfully (partial match)")
				return True
			else:
				_log(f"[{serial}] ✗ Tap succeeded but verification failed")
		else:
			_log(f"[{serial}] ✗ Partial match not found")
		
		# Strategy 3: Coordinate fallback
		_log(f"[{serial}] Strategy 3: Using coordinate fallback at ({center_x}, {center_y})")
		if _tap_and_verify(adb, serial, PROFILE_BOUNDS, log_fn):
			_log(f"[{serial}] ✓✓ Profile tab opened successfully (coordinate fallback)")
			return True
		else:
			_log(f"[{serial}] ✗ Coordinate tap failed or verification failed")
		
		# Sleep before retry
		if main_attempt < max_main_retries:
			retry_delay = 0.5 + (main_attempt * 0.3)  # 0.5s, 0.8s, 1.1s
			_log(f"[{serial}] Retrying in {retry_delay:.1f}s...")
			time.sleep(retry_delay)
	
	# All attempts exhausted
	_log(f"[{serial}] ✗✗ Failed to open Profile tab after {max_main_retries} attempts")
	_dump_xml_on_failure(adb, serial, log_fn)
	raise Exception("Profile tab not found in xml - all selector and fallback attempts failed")
	
	# Main retry loop
	for main_attempt in range(1, max_main_retries + 1):
		if time.time() - start_time > timeout_s:
			_log(f"[{serial}] ✗ Timeout exceeded ({timeout_s}s)")
			_dump_xml_on_failure(adb, serial, log_fn)
			raise Exception("Profile tab not found - timeout exceeded")
		
		_log(f"[{serial}] ─── Attempt {main_attempt}/{max_main_retries} ───")
		
		# Check if Facebook still running
		if not is_facebook_running(adb, serial):
			_log(f"[{serial}] ⚠ Facebook not in foreground, relaunching...")
			if not _ensure_facebook_running(adb, serial, log_fn):
				_log(f"[{serial}] ✗ Failed to relaunch Facebook")
				continue
		
		# Strategy 1: Try exact content-desc match
		_log(f"[{serial}] Strategy 1: Searching for exact content-desc 'Profile, tab 5 of 5'")
		xml = dump_ui_xml(adb, serial)
		
		if xml:
			# Exact match
			bounds_exact = find_first(xml, {"desc_contains": "Profile, tab 5 of 5", "clickable": True})
			if bounds_exact:
				_log(f"[{serial}] ✓ Found profile tab by exact desc at {bounds_exact}")
				if _tap_and_verify(adb, serial, bounds_exact, log_fn):
					_log(f"[{serial}] ✓✓ Profile tab opened successfully (exact match)")
					return True
				else:
					_log(f"[{serial}] ✗ Tap succeeded but verification failed")
			else:
				_log(f"[{serial}] ✗ Exact content-desc not found")
			
			# Strategy 2: Try partial match (contains "Profile" AND "tab")
			_log(f"[{serial}] Strategy 2: Searching for partial match (contains 'Profile' & 'tab')")
			bounds_partial = _find_profile_tab_partial(xml, log_fn)
			if bounds_partial:
				_log(f"[{serial}] ✓ Found profile tab by partial match at {bounds_partial}")
				if _tap_and_verify(adb, serial, bounds_partial, log_fn):
					_log(f"[{serial}] ✓✓ Profile tab opened successfully (partial match)")
					return True
				else:
					_log(f"[{serial}] ✗ Tap succeeded but verification failed")
			else:
				_log(f"[{serial}] ✗ Partial match not found")
		else:
			_log(f"[{serial}] ✗ Cannot dump UI XML")
		
		# Strategy 3: Coordinate fallback
		_log(f"[{serial}] Strategy 3: Using coordinate fallback at ({center_x}, {center_y})")
		if _tap_and_verify(adb, serial, PROFILE_BOUNDS, log_fn):
			_log(f"[{serial}] ✓✓ Profile tab opened successfully (coordinate fallback)")
			return True
		else:
			_log(f"[{serial}] ✗ Coordinate tap failed or verification failed")
		
		# Sleep before retry
		if main_attempt < max_main_retries:
			retry_delay = 0.5 + (main_attempt * 0.3)  # 0.5s, 0.8s, 1.1s
			_log(f"[{serial}] Retrying in {retry_delay:.1f}s...")
			time.sleep(retry_delay)
	
	# All attempts exhausted
	_log(f"[{serial}] ✗✗ Failed to open Profile tab after {max_main_retries} attempts")
	_dump_xml_on_failure(adb, serial, log_fn)
	raise Exception("Profile tab not found - all selector and fallback attempts failed")


def _ensure_facebook_running(adb: Any, serial: str, log_fn: Any) -> bool:
	"""Ensure Facebook is in foreground, launch if needed."""
	import time
	
	def _log(msg: str):
		if log_fn:
			try:
				log_fn(msg)
			except:
				pass
	
	if is_facebook_running(adb, serial):
		_log(f"[{serial}] ✓ Facebook already running")
		return True
	
	_log(f"[{serial}] Launching Facebook...")
	try:
		adb.shell(serial, "monkey -p com.facebook.katana -c android.intent.category.LAUNCHER 1")
		time.sleep(3.5)
	except Exception as exc:
		_log(f"[{serial}] ✗ Launch failed: {exc}")
		return False
	
	# Verify
	if is_facebook_running(adb, serial):
		_log(f"[{serial}] ✓ Facebook launched successfully")
		return True
	
	_log(f"[{serial}] ✗ Facebook failed to launch")
	return False


def _wait_for_stable_ui(adb: Any, serial: str, log_fn: Any, timeout: float) -> bool:
	"""Wait until Facebook feed UI is stable (no loading overlays)."""
	import time
	
	def _log(msg: str):
		if log_fn:
			try:
				log_fn(msg)
			except:
				pass
	
	end_time = time.time() + timeout
	stable_count = 0
	
	while time.time() < end_time:
		xml = dump_ui_xml(adb, serial)
		if xml:
			# Check for loading indicators
			loading_indicators = ["Loading", "Please wait", "Refreshing"]
			has_loading = any(indicator in xml for indicator in loading_indicators)
			
			if not has_loading:
				stable_count += 1
				if stable_count >= 2:  # 2 consecutive stable checks
					_log(f"[{serial}] ✓ UI is stable")
					return True
			else:
				stable_count = 0
		
		time.sleep(0.5)
	
	return False


def _find_profile_tab_node(xml: str, log_fn: Any) -> tuple[Bounds, bool, bool] | None:
	"""
	Find Profile tab node in UI XML and extract its state.
	
	Searches for node where:
	- content-desc == "Profile, tab 5 of 5" OR startswith "Profile, tab"
	- clickable == "true"
	
	Returns:
		Tuple of (bounds, selected, clickable) or None if not found
		- bounds: (x1, y1, x2, y2) tuple
		- selected: True if @selected=="true"
		- clickable: True if @clickable=="true"
	"""
	if not xml:
		return None
	
	try:
		root = ET.fromstring(xml)
	except (ET.ParseError, Exception):
		return None
	
	for node in root.iter():
		attrs = node.attrib
		content_desc = attrs.get("content-desc", "")
		clickable_attr = attrs.get("clickable", "false").lower() == "true"
		selected_attr = attrs.get("selected", "false").lower() == "true"
		
		# Check for exact match or startswith "Profile, tab"
		if content_desc == "Profile, tab 5 of 5" or content_desc.startswith("Profile, tab"):
			if clickable_attr:
				bounds_text = attrs.get("bounds")
				if bounds_text:
					try:
						bounds = parse_bounds(bounds_text)
						return (bounds, selected_attr, clickable_attr)
					except ValueError:
						continue
	
	return None


def _find_profile_tab_partial(xml: str, log_fn: Any) -> Bounds | None:
	"""
	Find Profile tab using partial content-desc matching.
	Looks for nodes containing both "Profile" AND "tab 5" in content-desc.
	"""
	if not xml:
		return None
	
	try:
		root = ET.fromstring(xml)
	except (ET.ParseError, Exception):
		return None
	
	for node in root.iter():
		attrs = node.attrib
		content_desc = attrs.get("content-desc", "")
		
		# Check if contains both "Profile" and "tab 5" (case-sensitive)
		if "Profile" in content_desc and "tab 5" in content_desc:
			# Verify it's clickable or at least actionable
			clickable_attr = attrs.get("clickable", "false").lower() == "true"
			bounds_text = attrs.get("bounds")
			
			if bounds_text:
				try:
					bounds = parse_bounds(bounds_text)
					# Validate position (should be in bottom tab bar area)
					# Expected Y range: 40-150 for top tabs, or rightmost position
					x1, y1, x2, y2 = bounds
					if (40 <= y1 <= 150 and x1 >= 500) or x1 >= 550:
						return bounds
				except ValueError:
					continue
	
	return None


def _tap_and_verify(adb: Any, serial: str, bounds: Bounds, log_fn: Any) -> bool:
	"""
	Tap center of bounds and verify profile screen opened.
	If verification fails, retry tap once more.
	"""
	import time
	
	def _log(msg: str):
		if log_fn:
			try:
				log_fn(msg)
			except:
				pass
	
	x1, y1, x2, y2 = bounds
	center_x = (x1 + x2) // 2
	center_y = (y1 + y2) // 2
	
	# First tap attempt
	_log(f"[{serial}] Tapping at ({center_x}, {center_y})")
	if not tap_center(adb, serial, bounds):
		_log(f"[{serial}] ✗ Tap command failed")
		return False
	
	time.sleep(1.0)
	
	# Verify
	if _verify_profile_screen(adb, serial, log_fn):
		return True
	
	# Verification failed, retry tap once
	_log(f"[{serial}] Verification failed, retrying tap...")
	if not tap_center(adb, serial, bounds):
		return False
	
	time.sleep(1.2)
	
	# Final verification
	return _verify_profile_screen(adb, serial, log_fn)


def _verify_profile_screen(adb: Any, serial: str, log_fn: Any) -> bool:
	"""
	Verify that Profile screen opened successfully.
	
	Checks for indicators:
	- "Go to profile" in content-desc
	- "Edit profile" button
	- "Activity log" 
	- "Settings & privacy"
	- Any element with "Profile" in content-desc (as last resort)
	"""
	import time
	
	def _log(msg: str):
		if log_fn:
			try:
				log_fn(msg)
			except:
				pass
	
	# Take 2 dumps with delay to avoid stale data
	for dump_attempt in range(2):
		xml = dump_ui_xml(adb, serial)
		if xml:
			# Strong indicators (high confidence)
			strong_indicators = [
				"Edit profile",
				"Activity log",
				"Settings & privacy",
				"View as",
				"Go to profile",
			]
			
			for indicator in strong_indicators:
				if indicator in xml:
					_log(f"[{serial}] ✓ Profile screen verified (found '{indicator}')")
					return True
			
			# Weak indicator on second attempt
			if dump_attempt == 1 and "Profile" in xml:
				_log(f"[{serial}] ✓ Profile screen likely opened (found 'Profile' text)")
				return True
		
		if dump_attempt == 0:
			time.sleep(0.6)
	
	_log(f"[{serial}] ✗ Cannot verify profile screen opened")
	return False


def _dump_xml_on_failure(adb: Any, serial: str, log_fn: Any) -> None:
	"""Dump current window XML to file on final failure for debugging."""
	import time
	from pathlib import Path
	
	def _log(msg: str):
		if log_fn:
			try:
				log_fn(msg)
			except:
				pass
	
	xml = dump_ui_xml(adb, serial)
	if xml:
		timestamp = time.strftime("%Y%m%d_%H%M%S")
		filename = f"ui_dump_failure_{serial}_{timestamp}.xml"
		filepath = Path.cwd() / filename
		
		try:
			with open(filepath, 'w', encoding='utf-8') as f:
				f.write(xml)
			_log(f"[{serial}] UI XML dumped to: {filepath}")
		except Exception as exc:
			_log(f"[{serial}] Failed to dump XML: {exc}")


def push_media_to_emulator(
	adb: Any,
	serial: str,
	pc_path: str,
	dst_dir: str = "/sdcard/Pictures/ReelsBot/",
	log_fn: Any = None,
	retries: int = 3,
) -> tuple[str, str]:
	"""Push media file from PC into emulator shared Pictures/ReelsBot folder."""
	def _log(msg: str) -> None:
		if log_fn:
			try:
				log_fn(msg)
			except Exception:
				pass

	local_path = Path(pc_path)
	if not local_path.exists() or not local_path.is_file():
		raise FileNotFoundError(f"Source media does not exist: {pc_path}")

	dst_dir_norm = dst_dir if dst_dir.endswith("/") else f"{dst_dir}/"
	original_name = local_path.name
	dst_file_name = f"reel_{_now_ts()}_{original_name}"
	dst_full_path = f"{dst_dir_norm}{dst_file_name}"

	_log(f"[{serial}] Ensuring destination directory exists: {dst_dir_norm}")
	adb.shell(serial, f"mkdir -p {_shell_quote(dst_dir_norm)}")

	last_exc: Exception | None = None
	for attempt in range(1, retries + 1):
		try:
			_log(f"[{serial}] Pushing media ({attempt}/{retries}): {local_path.name}")
			if not hasattr(adb, "_adb"):
				raise RuntimeError("ADB manager does not expose adb client for push")
			dev = adb._adb.device(serial=serial)
			dev.sync.push(str(local_path), dst_full_path)
			break
		except Exception as exc:
			last_exc = exc
			if attempt < retries:
				time.sleep(0.8)
				continue
			raise RuntimeError(f"adb push failed: {exc}")

	# Verify file exists in destination
	verify_cmd = f"if [ -f {_shell_quote(dst_full_path)} ]; then echo OK; else echo MISSING; fi"
	verify_out = adb.shell(serial, verify_cmd)
	if "OK" not in str(verify_out):
		raise RuntimeError(f"Pushed file missing at destination: {dst_full_path}")

	# Trigger media scanner broadcast
	broadcast_cmd = (
		"am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE "
		f"-d file://{dst_full_path}"
	)
	adb.shell(serial, broadcast_cmd)
	_log(f"[{serial}] Media scanner broadcast sent for: {dst_full_path}")

	# Extra short delay then verify again
	time.sleep(0.4)
	verify_out2 = adb.shell(serial, verify_cmd)
	if "OK" not in str(verify_out2):
		raise RuntimeError(f"Destination file disappeared after media scan: {dst_full_path}")

	if last_exc:
		_ = last_exc
	return dst_full_path, dst_file_name


def open_file_manager(adb: Any, serial: str, log_fn: Any = None, timeout_s: int = 12) -> bool:
	"""Open CyanogenMod File Manager and wait until it is focused."""
	def _log(msg: str) -> None:
		if log_fn:
			try:
				log_fn(msg)
			except Exception:
				pass

	adb.shell(serial, "am start -n com.cyanogenmod.filemanager/.activities.MainActivity")
	end_at = time.time() + max(3, timeout_s)
	while time.time() < end_at:
		focus = adb.shell(serial, "dumpsys window | grep mCurrentFocus")
		if "com.cyanogenmod.filemanager" in str(focus):
			_log(f"[{serial}] ✓ File Manager opened")
			return True
		time.sleep(0.4)

	_debug_dump_artifacts(adb, serial, "filemanager_open_failed", log_fn)
	return False


def _find_node_bounds(
	xml: str,
	*,
	resource_id: str | None = None,
	class_name: str | None = None,
	text_equals: str | None = None,
	text_contains: str | None = None,
	desc_equals: str | None = None,
	desc_contains: str | None = None,
	clickable: bool | None = None,
) -> Bounds | None:
	if not xml:
		return None
	try:
		root = ET.fromstring(xml)
	except Exception:
		return None

	for node in root.iter():
		a = node.attrib
		text = a.get("text", "")
		desc = a.get("content-desc", "")
		res_id = a.get("resource-id", "")
		cls = a.get("class", "")
		is_clickable = a.get("clickable", "false").lower() == "true"

		if resource_id is not None and res_id != resource_id:
			continue
		if class_name is not None and cls != class_name:
			continue
		if text_equals is not None and text != text_equals:
			continue
		if text_contains is not None and text_contains not in text:
			continue
		if desc_equals is not None and desc != desc_equals:
			continue
		if desc_contains is not None and desc_contains not in desc:
			continue
		if clickable is not None and is_clickable != clickable:
			continue

		bounds_text = a.get("bounds")
		if not bounds_text:
			continue
		try:
			return parse_bounds(bounds_text)
		except ValueError:
			continue
	return None


def _tap_text_any(adb: Any, serial: str, xml: str, candidates: list[str]) -> bool:
	for text in candidates:
		b = _find_node_bounds(xml, text_equals=text)
		if b and tap_center(adb, serial, b):
			return True
		b = _find_node_bounds(xml, text_contains=text)
		if b and tap_center(adb, serial, b):
			return True
	return False


def navigate_to_shared_pictures_reelsbot(
	adb: Any,
	serial: str,
	log_fn: Any = None,
	timeout_s: int = 20,
) -> bool:
	"""Navigate File Manager to shared/Pictures/ReelsBot and verify path context."""
	def _log(msg: str) -> None:
		if log_fn:
			try:
				log_fn(msg)
			except Exception:
				pass

	# Ensure folder exists on storage so UI navigation can open it.
	adb.shell(serial, "mkdir -p /sdcard/Pictures/ReelsBot")

	def _has_path_context(xml_text: str) -> bool:
		low = xml_text.lower()
		return "shared" in low and "pictures" in low and "reelsbot" in low

	end_at = time.time() + timeout_s
	for _ in range(2):
		xml = dump_ui_xml(adb, serial)
		if xml and _has_path_context(xml):
			_log(f"[{serial}] ✓ Already in shared/Pictures/ReelsBot")
			return True

	while time.time() < end_at:
		xml = dump_ui_xml(adb, serial)
		if not xml:
			time.sleep(0.4)
			continue

		if _has_path_context(xml):
			_log(f"[{serial}] ✓ ReelsBot folder context verified")
			return True

		# Open drawer if visible.
		drawer_btn = (
			_find_node_bounds(xml, desc_contains="Open navigation")
			or _find_node_bounds(xml, desc_contains="Navigate up")
		)
		if drawer_btn:
			tap_center(adb, serial, drawer_btn)
			time.sleep(0.4)
			xml = dump_ui_xml(adb, serial)
			if not xml:
				continue

		# Open Pictures
		if _tap_text_any(adb, serial, xml, ["Pictures", "shared", "Storage"]):
			time.sleep(0.5)

		xml = dump_ui_xml(adb, serial)
		if not xml:
			time.sleep(0.4)
			continue

		reelsbot_bounds = _find_node_bounds(xml, text_equals="ReelsBot", class_name="android.widget.TextView")
		if reelsbot_bounds and tap_center(adb, serial, reelsbot_bounds):
			time.sleep(0.5)
			xml = dump_ui_xml(adb, serial)
			if xml and ("ReelsBot" in xml or _has_path_context(xml)):
				return True

		# Scroll content and continue search
		swipe(adb, serial, 360, 980, 360, 350, 300)
		time.sleep(0.5)

	_debug_dump_artifacts(adb, serial, "navigate_reelsbot_failed", log_fn)
	return False


def select_file_by_name(
	adb: Any,
	serial: str,
	file_name: str,
	log_fn: Any = None,
	max_scrolls: int = 8,
) -> bool:
	"""Select a media file by exact filename in File Manager list."""
	def _log(msg: str) -> None:
		if log_fn:
			try:
				log_fn(msg)
			except Exception:
				pass

	resource_id = "com.cyanogenmod.filemanager:id/navigation_view_item_name"
	for idx in range(max_scrolls + 1):
		xml = dump_ui_xml(adb, serial)
		if xml:
			bounds = _find_node_bounds(
				xml,
				resource_id=resource_id,
				class_name="android.widget.TextView",
				text_equals=file_name,
			)
			if bounds:
				_log(f"[{serial}] ✓ Found file by exact name: {file_name}")
				if tap_center(adb, serial, bounds):
					time.sleep(0.35)
					return True

		if idx < max_scrolls:
			_log(f"[{serial}] File not visible, scrolling list ({idx + 1}/{max_scrolls})")
			swipe(adb, serial, 360, 1040, 360, 380, 350)
			time.sleep(0.45)

	_debug_dump_artifacts(adb, serial, "select_file_by_name_failed", log_fn)
	return False


def _wait_for_chooser(adb: Any, serial: str, timeout_s: float = 8.0) -> bool:
	end_at = time.time() + timeout_s
	while time.time() < end_at:
		focus = str(adb.shell(serial, "dumpsys window | grep mCurrentFocus"))
		xml = dump_ui_xml(adb, serial)
		if "resolver" in focus.lower() or "chooser" in focus.lower():
			return True
		if xml and ("Share" in xml or "Send" in xml or "Complete action using" in xml):
			return True
		time.sleep(0.35)
	return False


def _wait_for_fb_reel_composer(adb: Any, serial: str, timeout_s: float = 20.0) -> bool:
	end_at = time.time() + timeout_s
	while time.time() < end_at:
		focus = str(adb.shell(serial, "dumpsys window | grep mCurrentFocus"))
		xml = dump_ui_xml(adb, serial)
		if "com.facebook.katana" in focus and xml:
			if (
				"Next" in xml
				or "Describe your reel" in xml
				or "Write a description" in xml
				or "Share reel" in xml
			):
				return True
		time.sleep(0.45)
	return False


def share_to_facebook_reels(adb: Any, serial: str, log_fn: Any = None, timeout_s: int = 25) -> bool:
	"""Share selected file from File Manager into Facebook Reels chooser path."""
	def _log(msg: str) -> None:
		if log_fn:
			try:
				log_fn(msg)
			except Exception:
				pass

	start_xml = dump_ui_xml(adb, serial)
	if not start_xml:
		_debug_dump_artifacts(adb, serial, "share_reels_no_xml", log_fn)
		return False

	# Tap Actions button
	actions_bounds = _find_node_bounds(
		start_xml,
		resource_id="com.cyanogenmod.filemanager:id/ab_actions",
		desc_equals="Actions",
	)
	if not actions_bounds:
		actions_bounds = _find_node_bounds(start_xml, desc_contains="Actions")
	if not actions_bounds or not tap_center(adb, serial, actions_bounds):
		_debug_dump_artifacts(adb, serial, "share_reels_actions_failed", log_fn)
		return False

	time.sleep(0.45)
	xml = dump_ui_xml(adb, serial)
	if not xml or not _tap_text_any(adb, serial, xml, ["Send", "Share"]):
		_debug_dump_artifacts(adb, serial, "share_reels_send_failed", log_fn)
		return False

	# Verify chooser appeared
	if not _wait_for_chooser(adb, serial, timeout_s=8.0):
		_debug_dump_artifacts(adb, serial, "share_reels_chooser_missing", log_fn)
		return False

	time.sleep(0.4)
	chooser_xml = dump_ui_xml(adb, serial)
	if chooser_xml and _tap_text_any(adb, serial, chooser_xml, ["Share with Reels", "Reels"]):
		_log(f"[{serial}] ✓ Selected direct Reels target from chooser")
		if _wait_for_fb_reel_composer(adb, serial, timeout_s=max(10, timeout_s)):
			return True

	# Fallback: pick Facebook app then select Share to Reels inside FB
	chooser_xml = dump_ui_xml(adb, serial)
	if chooser_xml and _tap_text_any(adb, serial, chooser_xml, ["Facebook"]):
		_log(f"[{serial}] Chooser fallback: selected Facebook app")
		end_at = time.time() + 10
		while time.time() < end_at:
			xml_fb = dump_ui_xml(adb, serial)
			if xml_fb and _tap_text_any(adb, serial, xml_fb, ["Share to Reels", "Reels"]):
				break
			time.sleep(0.4)

	if _wait_for_fb_reel_composer(adb, serial, timeout_s=max(10, timeout_s)):
		return True

	_debug_dump_artifacts(adb, serial, "share_reels_fb_composer_missing", log_fn)
	return False


def continue_reel_post_flow(
	adb: Any,
	serial: str,
	caption: str = "",
	log_fn: Any = None,
	timeout_s: int = 20,
) -> bool:
	"""Continue FB reel flow after media import: Next -> caption -> Share."""
	def _log(msg: str) -> None:
		if log_fn:
			try:
				log_fn(msg)
			except Exception:
				pass

	time.sleep(1.0)
	end_at = time.time() + timeout_s
	next_tapped = False

	while time.time() < end_at and not next_tapped:
		xml = dump_ui_xml(adb, serial)
		if xml and _tap_text_any(adb, serial, xml, ["Next"]):
			next_tapped = True
			break
		time.sleep(0.35)
	if not next_tapped:
		_debug_dump_artifacts(adb, serial, "continue_reel_next_failed", log_fn)
		return False

	time.sleep(1.0)
	if caption.strip():
		xml = dump_ui_xml(adb, serial)
		if xml:
			caption_target = (
				_find_node_bounds(xml, text_contains="description")
				or _find_node_bounds(xml, text_contains="caption")
				or _find_node_bounds(xml, text_contains="Write")
			)
			if caption_target:
				tap_center(adb, serial, caption_target)
				time.sleep(0.3)
		safe = caption.strip().replace(" ", "%s").replace("'", "").replace('"', "")
		adb.shell(serial, f"input text {safe}")

	end_share = time.time() + timeout_s
	while time.time() < end_share:
		xml = dump_ui_xml(adb, serial)
		if xml and _tap_text_any(adb, serial, xml, ["Share", "Share now"]):
			_log(f"[{serial}] ✓ Reel Share tapped")
			return True
		time.sleep(0.45)

	_debug_dump_artifacts(adb, serial, "continue_reel_share_failed", log_fn)
	return False


def select_media_for_reels_via_filemanager(
	adb: Any,
	serial: str,
	pc_path: str,
	log_fn: Any = None,
) -> tuple[bool, str | None, str | None, str | None]:
	"""Orchestrator: push media -> file manager select by filename -> share to FB Reels."""
	def _log(msg: str) -> None:
		if log_fn:
			try:
				log_fn(msg)
			except Exception:
				pass

	_log(f"[{serial}] [FM_FLOW] START media selection via File Manager")
	try:
		dst_full_path, dst_file_name = push_media_to_emulator(
			adb,
			serial,
			pc_path,
			dst_dir="/sdcard/Pictures/ReelsBot/",
			log_fn=log_fn,
		)

		if not open_file_manager(adb, serial, log_fn=log_fn):
			return False, "File Manager did not open", dst_full_path, dst_file_name

		if not navigate_to_shared_pictures_reelsbot(adb, serial, log_fn=log_fn):
			return False, "Failed navigating to shared/Pictures/ReelsBot", dst_full_path, dst_file_name

		if not select_file_by_name(adb, serial, dst_file_name, log_fn=log_fn):
			return False, f"File not found in list: {dst_file_name}", dst_full_path, dst_file_name

		if not share_to_facebook_reels(adb, serial, log_fn=log_fn):
			return False, "Failed sharing selected media to Facebook Reels", dst_full_path, dst_file_name

		_log(f"[{serial}] [FM_FLOW] ✓ Media selected and sent to FB Reels: {dst_file_name}")
		return True, None, dst_full_path, dst_file_name

	except Exception as exc:
		_debug_dump_artifacts(adb, serial, "fm_orchestrator_exception", log_fn)
		return False, str(exc), None, None
