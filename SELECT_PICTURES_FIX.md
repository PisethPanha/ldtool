## SELECT_PICTURES State Fix - Complete Implementation

**Problem**: SELECT_PICTURES was marking success without actually opening Pictures folder or verifying breadcrumb, causing NAVIGATE_MEDIA to fail when still at File Manager root.

**Solution**: Implemented strict verification with breadcrumb checking and retry logic.

---

## Changes Implemented

### 1. Helper Function: `_is_in_shared_pictures()` (Lines ~461-486)

**Purpose**: Check if File Manager breadcrumb shows we're inside `/sdcard/shared/Pictures`

**Logic**:
- Parse XML for nodes with `resource-id="...breadcrumb_item"`
- Extract all breadcrumb text values
- Return `True` if both "shared" AND "pictures" appear in breadcrumb texts (case-insensitive)

**Example breadcrumb when in Pictures**:
```
Breadcrumb items: ["shared", "Pictures"]
Returns: True
```

**Example breadcrumb at root**:
```
Breadcrumb items: ["0"] or []
Returns: False
```

---

### 2. Helper Function: `_find_folder_row_bounds()` (Lines ~488-515)

**Purpose**: Find folder row in File Manager folder list by exact name match

**Logic**:
- Parse XML for `TextView` nodes with `resource-id="...navigation_view_item_name"`
- Match `text` attribute exactly (e.g., "Pictures")
- Return bounds of the matched node for tapping

**Returns**:
- `(x1, y1, x2, y2)` if folder found
- `None` if not found

---

### 3. Rewritten `_state_select_pictures()` (Lines ~900-1015)

**New Implementation**:

#### Step 1: Check if already in Pictures
```python
if self._is_in_shared_pictures(xml):
    return True, None  # Already there, success
```

#### Step 2: Ensure File Manager foreground
```python
if current_package != "com.cyanogenmod.filemanager":
    self._ensure_foreground(serial, "com.cyanogenmod.filemanager")
```

#### Step 3: Find and tap Pictures folder
- Try up to 8 scrolls to find Pictures row using `_find_folder_row_bounds()`
- If not found after scrolling, fallback to search button approach:
  1. Tap search button (`ab_search`)
  2. Input "Pictures"
  3. Press ENTER
  4. Tap search result

#### Step 4: Verify breadcrumb (CRITICAL)
- Wait up to 3 seconds for breadcrumb to update
- Check repeatedly using `_is_in_shared_pictures()`
- Only return success if breadcrumb shows "shared > Pictures"

#### Step 5: Retry logic
- Max 3 attempts
- If verification fails, log clear error and retry from Step 1
- Final error message: `"SELECT_PICTURES failed: Pictures folder did not open (breadcrumb not shared/Pictures)"`

**Success Condition**: Breadcrumb verified showing "shared > Pictures"

**Logging**:
```
[device] SELECT_PICTURES: opening /sdcard/shared/Pictures
[device] SELECT_PICTURES attempt 1/3
[device] Found Pictures folder row, tapping...
[device] ✓ Pictures folder opened (breadcrumb: shared > Pictures)
```

**Failure Logging** (if verification fails):
```
[device] Pictures folder did not open (breadcrumb verification failed), retrying... (attempt 1/3)
```

---

### 4. Guard Added to `_state_navigate_media()` (Lines ~1017-1030)

**Purpose**: Prevent NAVIGATE_MEDIA from running when not in Pictures folder

**Guard Logic** (runs at very start of state):
```python
xml_guard = dump_ui_xml(self.adb, serial)
if xml_guard and not self._is_in_shared_pictures(xml_guard):
    # Not in Pictures! Call SELECT_PICTURES again
    self._log("NAVIGATE_MEDIA guard failed: Not in shared/Pictures folder")
    ok, err = self._state_select_pictures(serial, job, device_media_path, 15)
    if not ok:
        return False, f"Failed to open Pictures folder: {err}"
    # Verify again
    if still not in Pictures:
        return False, "Still not in Pictures after SELECT_PICTURES"
```

**Effect**:
- NAVIGATE_MEDIA will never proceed unless breadcrumb shows "shared > Pictures"
- If guard fails, automatically calls SELECT_PICTURES to fix the state
- Prevents scrolling through folder list when still at root

---

## Acceptance Criteria (✓ Met)

### ✓ Criterion 1: Pictures folder actually opens
**Status**: IMPLEMENTED
- SELECT_PICTURES now taps folder row and waits for breadcrumb verification
- Only returns success after breadcrumb shows "shared > Pictures"

### ✓ Criterion 2: NAVIGATE_MEDIA guard prevents early execution
**Status**: IMPLEMENTED
- Guard checks breadcrumb before any file search
- Calls SELECT_PICTURES if not in Pictures
- Fails fast with clear error if recovery doesn't work

### ✓ Criterion 3: Scrolling logic to find Pictures
**Status**: IMPLEMENTED
- Up to 8 scroll attempts (360, 1040 → 360, 600, 300ms)
- Re-dump XML after each scroll
- Fallback to search button if scrolling doesn't find it

### ✓ Criterion 4: Clear logging
**Status**: IMPLEMENTED
- Logs each attempt number
- Logs when folder row found and tapped
- Logs breadcrumb verification success/failure
- Final success only logged after breadcrumb verified

### ✓ Criterion 5: Retry logic
**Status**: IMPLEMENTED
- Max 3 attempts in SELECT_PICTURES
- Each attempt: scroll → tap → verify → retry if fails
- Clear error message on final failure

---

## Execution Flow Example

### Successful Flow:
```
STATE: OPEN_FILE_MANAGER ... actual: com.cyanogenmod.filemanager
✓ OPEN_FILE_MANAGER completed

STATE: SELECT_PICTURES ... actual: com.cyanogenmod.filemanager
SELECT_PICTURES: opening /sdcard/shared/Pictures
SELECT_PICTURES attempt 1/3
Found Pictures folder row, tapping...
✓ Pictures folder opened (breadcrumb: shared > Pictures)
✓ SELECT_PICTURES completed

STATE: NAVIGATE_MEDIA ... actual: com.cyanogenmod.filemanager
NAVIGATE_MEDIA target=video.mp4, resolved_path=/sdcard/shared/Pictures/video.mp4
✓ Media row visible: video.mp4
✓ NAVIGATE_MEDIA completed
```

### Guard Trigger Flow (if Pictures didn't open):
```
STATE: SELECT_PICTURES ... actual: com.cyanogenmod.filemanager
SELECT_PICTURES: opening /sdcard/shared/Pictures
Found Pictures folder row, tapping...
Pictures folder did not open (breadcrumb verification failed), retrying... (attempt 1/3)
Found Pictures folder row, tapping...
✓ Pictures folder opened (breadcrumb: shared > Pictures)
✓ SELECT_PICTURES completed

STATE: NAVIGATE_MEDIA ... actual: com.cyanogenmod.filemanager
NAVIGATE_MEDIA guard failed: Not in shared/Pictures folder, calling SELECT_PICTURES again...
SELECT_PICTURES: opening /sdcard/shared/Pictures
✓ Already in shared/Pictures (breadcrumb verified)
NAVIGATE_MEDIA target=video.mp4
✓ Media row visible: video.mp4
```

---

## Key Implementation Details

### Breadcrumb Parsing:
```python
for node in root.iter():
    res_id = node.attrib.get("resource-id", "")
    if "breadcrumb_item" in res_id:
        text = node.attrib.get("text", "")
        breadcrumb_texts.append(text.lower())

has_shared = any("shared" in txt for txt in breadcrumb_texts)
has_pictures = any("pictures" in txt for txt in breadcrumb_texts)
return has_shared and has_pictures
```

### Folder Row Finding:
```python
for node in root.iter():
    res_id = node.attrib.get("resource-id", "")
    text = node.attrib.get("text", "")
    
    if "navigation_view_item_name" in res_id and text == folder_name:
        return parse_bounds(node.attrib.get("bounds"))
```

### Verification Wait Loop:
```python
verify_end = time.time() + 3.0
while time.time() < verify_end:
    xml_verify = dump_ui_xml(self.adb, serial)
    if xml_verify and self._is_in_shared_pictures(xml_verify):
        return True, None  # Verified!
    time.sleep(0.4)
```

---

## Files Modified

| File | Lines Changed | Description |
|------|---------------|-------------|
| reel_poster.py | ~461-515 | Added 2 helper functions |
| reel_poster.py | ~900-1015 | Rewrote SELECT_PICTURES with verification |
| reel_poster.py | ~1017-1030 | Added guard to NAVIGATE_MEDIA |

---

## Edge Cases Handled

1. **Already in Pictures**: Guard detects and returns success immediately
2. **File Manager loses foreground**: Restores FM before attempting folder navigation
3. **Pictures not visible**: Scrolls up to 8 times to find folder row
4. **Scrolling fails**: Falls back to search button approach
5. **Tap doesn't open folder**: Retry logic (up to 3 attempts)
6. **NAVIGATE_MEDIA called when not in Pictures**: Guard calls SELECT_PICTURES automatically

---

## Testing Recommendations

1. **Test when Pictures is visible immediately**: Should tap and verify without scrolling
2. **Test when Pictures is below fold**: Should scroll to find it
3. **Test when folder doesn't open after tap**: Should retry and eventually succeed
4. **Test NAVIGATE_MEDIA guard**: Manually leave Pictures, ensure guard catches it
5. **Test breadcrumb variants**: Ensure "shared" and "Pictures" detection is case-insensitive

---

**Status**: ✅ **COMPLETE AND SYNTAX VERIFIED**
