# open_profile_tab() Function Reference

## Overview
Robust Facebook Profile tab automation for Android devices using ADB.

**Location**: `src/core/ui_dump.py`

## Function Signature
```python
def open_profile_tab(
    adb: Any,           # ADB manager instance
    serial: str,        # Device serial number
    log_fn: Any = None, # Optional logging callback
    timeout_s: int = 20 # Total timeout in seconds
) -> bool               # Returns True on success
```

## Target Element
- **Package**: `com.facebook.katana`
- **Class**: `android.view.View`
- **Content-desc**: `"Profile, tab 5 of 5"`
- **Bounds**: `[576,136][720,224]`
- **Tap center**: `(648, 180)`

## Features

### ✅ Multi-Strategy Selector Approach
1. **Exact Match**: Searches for `"Profile, tab 5 of 5"` in content-desc
2. **Partial Match**: Contains both `"Profile"` AND `"tab"` with position validation
3. **Coordinate Fallback**: Uses hard-coded bounds center as last resort

### ✅ Robust Retry Logic
- **3 main retry attempts** with progressive delays (0.5s → 0.8s → 1.1s)
- Each tap followed by **verification** (checks for profile screen indicators)
- If verification fails, **re-taps once** before moving to next strategy

### ✅ Crash Detection & Recovery
- Checks if Facebook is in foreground before each attempt
- **Auto-relaunches** if app crashes or closes
- Returns to feed and retries tab opening

### ✅ Verification System
Confirms profile screen opened by checking for:
- `"Edit profile"` button
- `"Activity log"` link
- `"Settings & privacy"` option
- `"View as"` feature
- `"Go to profile"` in content-desc

Takes **2 UI dumps 600ms apart** to avoid stale data.

### ✅ UI Stability Checks
- Waits for feed UI to stabilize (no loading overlays)
- Detects loading indicators: `"Loading"`, `"Please wait"`, `"Refreshing"`
- Requires **2 consecutive stable checks** before proceeding

### ✅ Debug Features
- **Detailed logging** at every step:
  - `"[serial] ═══ OPEN_PROFILE_TAB START ═══"`
  - `"[serial] ─── Attempt 1/3 ───"`
  - `"[serial] Strategy 1: Searching for exact content-desc..."`
  - `"[serial] ✓ Found profile tab by exact desc at (576, 136, 720, 224)"`
  - `"[serial] ✗ Tap succeeded but verification failed"`
- **XML dump on failure**: Saves `ui_dump_failure_{serial}_{timestamp}.xml` to current directory
- **Exception with clear message**: `"Profile tab not found - all selector and fallback attempts failed"`

## Usage Examples

### Basic Usage
```python
from src.core.adb_manager import ADBManager
from src.core.ui_dump import open_profile_tab

adb = ADBManager()
serial = adb.list_devices()[0]

try:
    success = open_profile_tab(adb, serial, log_fn=print)
    if success:
        print("✓ Profile tab opened!")
except Exception as exc:
    print(f"Failed: {exc}")
```

### With Custom Logging
```python
def custom_logger(msg):
    with open("automation.log", "a") as f:
        f.write(f"{msg}\n")

try:
    open_profile_tab(adb, serial, log_fn=custom_logger, timeout_s=25)
except Exception as exc:
    custom_logger(f"ERROR: {exc}")
```

### Silent Mode (No Logging)
```python
try:
    success = open_profile_tab(adb, serial)  # log_fn defaults to None
    return success
except Exception:
    return False
```

## Helper Functions (Internal)

### `_ensure_facebook_running(adb, serial, log_fn)`
- Checks if Facebook in foreground
- Launches if not running
- Verifies successful launch

### `_wait_for_stable_ui(adb, serial, log_fn, timeout)`
- Polls UI for loading indicators
- Requires 2 consecutive stable checks
- Returns True if stable within timeout

### `_find_profile_tab_partial(xml, log_fn)`
- Searches XML for partial content-desc match
- Validates position (top-right area, Y: 100-300, X: 500+)
- Returns bounds or None

### `_tap_and_verify(adb, serial, bounds, log_fn)`
- Taps center of bounds
- Verifies profile screen opened
- Retries tap once if verification fails

### `_verify_profile_screen(adb, serial, log_fn)`
- Takes 2 UI dumps with 600ms delay
- Checks for profile screen indicators
- Returns True if any indicator found

### `_dump_xml_on_failure(adb, serial, log_fn)`
- Dumps current UI XML to file
- Filename: `ui_dump_failure_{serial}_{timestamp}.xml`
- Used for post-mortem debugging

## Error Handling

### Returns `False`
- Never returns False (always raises on failure)

### Raises `Exception`
- **"Profile tab not found - timeout exceeded"**: Total timeout reached
- **"Profile tab not found - all selector and fallback attempts failed"**: All 3 attempts exhausted

### Success Returns `True`
- Profile tab found by any strategy
- Profile screen verified successfully

## Integration with State Machine

To replace old OPEN_PAGE_PROFILE logic:

```python
# OLD CODE (remove):
def _state_open_page_profile(self, serial, job, device_media_path, timeout_s):
    # Old logic searching by resource-id
    ...

# NEW CODE:
def _state_open_page_profile(self, serial, job, device_media_path, timeout_s):
    """Open Profile tab."""
    try:
        success = open_profile_tab(self.adb, serial, self.log_fn, timeout_s)
        if success:
            return True, None
    except Exception as exc:
        return False, str(exc)
    
    return False, "Profile tab not opened"
```

## Performance Characteristics

- **Typical duration**: 3-8 seconds (if tab found on first attempt)
- **With retries**: 10-15 seconds (if fallback strategies needed)
- **Timeout**: 20 seconds (configurable)
- **App relaunch penalty**: +3.5 seconds per relaunch

## Debugging Failed Attempts

1. Check console logs for detailed strategy execution
2. Review `ui_dump_failure_*.xml` file in current directory
3. Search XML for:
   - `content-desc` containing "Profile"
   - Tab elements (`"tab"` string)
   - Bounds coordinates
4. Adjust `PROFILE_BOUNDS` if screen resolution differs from 720x1280

## Known Limitations

- Assumes 720x1280 screen resolution for coordinate fallback
- Profile tab must be at `"tab 5 of 5"` position
- Requires Facebook app version with bottom/top tab navigation
- Chinese/localized Facebook apps may have different content-desc values
