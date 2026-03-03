import json
import random
import time
from threading import Event
from typing import Any, Dict, Tuple, Union

from src.core.adb_manager import ADBManager


class MacroEngine:
    """Simple engine for running touch macros via ADB.

    A macro is a dictionary containing a ``name`` and a list of ``steps``.
    Each step is itself a single-key dictionary describing an action with
    parameters.  Supported actions are defined in :meth:`_valid_actions`.

    Randomization options allow the caller to introduce a small amount of
    jitter to taps/swipes and to add random delays between steps for
    less robotic behaviour.
    """

    def __init__(self, pixel_jitter: int = 0, delay_jitter_ms: int = 0, log_fn=lambda m: None):
        self.pixel_jitter = pixel_jitter
        self.delay_jitter_ms = delay_jitter_ms
        self._log = log_fn

    # ------------------------------------------------------------------
    # loading / validation
    # ------------------------------------------------------------------
    def load_macro(self, path: str) -> Dict[str, Any]:
        """Load a macro from a JSON file and return the raw dict."""
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def validate_macro(self, macro: Dict[str, Any]) -> Tuple[bool, str]:
        """Check that ``macro`` has a valid structure.

        Returns ``(True, "")`` if everything is fine; otherwise returns
        ``(False, error_message)`` describing the first problem encountered.
        """
        if not isinstance(macro, dict):
            return False, "macro must be a dictionary"
        name = macro.get("name")
        if not isinstance(name, str):
            return False, "macro missing 'name' string"
        steps = macro.get("steps")
        if not isinstance(steps, list):
            return False, "macro 'steps' must be a list"

        for idx, step in enumerate(steps, start=1):
            if not isinstance(step, dict) or len(step) != 1:
                return False, f"step {idx} must be a single-key dict"
            action, params = next(iter(step.items()))
            if action not in {"wait", "tap", "swipe", "text", "keyevent"}:
                return False, f"step {idx} has unknown action '{action}'"
            # parameter validation
            if action == "wait":
                # allow numeric values or numeric strings
                if isinstance(params, str):
                    try:
                        float(params)
                    except ValueError:
                        return False, f"step {idx} wait time '{params}' is not a valid number"
                elif not isinstance(params, (int, float)):
                    return False, f"step {idx} wait time must be a number or numeric string"
            elif action == "tap":
                if (not isinstance(params, (list, tuple)) or len(params) != 2
                        or not all(isinstance(v, (int, float)) for v in params)):
                    return False, f"step {idx} tap requires [x, y]"
            elif action == "swipe":
                if (not isinstance(params, (list, tuple)) or len(params) != 5
                        or not all(isinstance(v, (int, float)) for v in params)):
                    return False, f"step {idx} swipe requires [x1,y1,x2,y2,duration]"
            elif action == "text":
                if not isinstance(params, str):
                    return False, f"step {idx} text value must be a string"
            elif action == "keyevent":
                if not isinstance(params, (str, int)):
                    return False, f"step {idx} keyevent value must be string or int"
        return True, ""

    # ------------------------------------------------------------------
    # execution
    # ------------------------------------------------------------------
    def run_macro_on_device(
        self,
        adb: ADBManager,
        serial: str,
        macro: Dict[str, Any],
        stop_event: Event,
        progress_fn: Callable[[int, int], None] | None = None,
        instance_id: int | None = None,
    ) -> Dict[str, Any]:
        """Execute ``macro`` on the device identified by ``serial``.

        ``stop_event`` may be a ``threading.Event`` which, if set, causes
        the engine to abort as soon as possible.  ``progress_fn`` if supplied
        is called with ``(instance_id, percent)`` after each step completes
        (``instance_id`` may be ``None``).  The return value is a dictionary
        containing ``'success'`` (bool) and ``'errors'`` list.
        """
        result: Dict[str, Any] = {"success": True, "errors": []}
        
        try:
            steps = macro.get("steps", [])
            total = len(steps)

            for idx, step in enumerate(steps, start=1):
                if stop_event.is_set():
                    result["success"] = False
                    result["errors"].append("stopped")
                    break

                action, params = next(iter(step.items()))

                # execute step
                try:
                    if action == "wait":
                        # ensure we have an integer millisecond value
                        try:
                            ms = int(float(params))
                        except Exception:
                            raise ValueError(f"invalid wait parameter '{params}'")
                        if self.delay_jitter_ms:
                            ms += random.randint(-self.delay_jitter_ms, self.delay_jitter_ms)
                            ms = max(0, ms)
                        time.sleep(ms / 1000.0)
                    elif action == "tap":
                        x, y = int(params[0]), int(params[1])
                        if self.pixel_jitter:
                            x += random.randint(-self.pixel_jitter, self.pixel_jitter)
                            y += random.randint(-self.pixel_jitter, self.pixel_jitter)
                        adb.shell(serial, f"input tap {x} {y}")
                    elif action == "swipe":
                        x1, y1, x2, y2, dur = map(int, params)
                        if self.pixel_jitter:
                            x1 += random.randint(-self.pixel_jitter, self.pixel_jitter)
                            y1 += random.randint(-self.pixel_jitter, self.pixel_jitter)
                            x2 += random.randint(-self.pixel_jitter, self.pixel_jitter)
                            y2 += random.randint(-self.pixel_jitter, self.pixel_jitter)
                        adb.shell(serial, f"input swipe {x1} {y1} {x2} {y2} {dur}")
                    elif action == "text":
                        text = str(params)
                        # escape spaces for shell
                        text = text.replace(" ", "%s")
                        adb.shell(serial, f"input text {text}")
                    elif action == "keyevent":
                        adb.shell(serial, f"input keyevent {params}")
                except Exception as exc:  # pragma: no cover - defensive
                    msg = f"error performing {action}: {exc}"
                    self._log(msg)
                    result["success"] = False
                    result["errors"].append(msg)

                # inter-step delay jitter
                if self.delay_jitter_ms and action != "wait":
                    extra = random.randint(0, self.delay_jitter_ms)
                    time.sleep(extra / 1000.0)

                # emit progress if requested
                if progress_fn and instance_id is not None and total:
                    try:
                        pct = int(idx * 100 / total)
                        progress_fn(instance_id, pct)
                    except Exception as exc:  # pragma: no cover - defensive
                        self._log(f"error emitting progress: {exc}")
        except Exception as exc:  # pragma: no cover - catch-all for catastrophic failures
            msg = f"macro execution failed: {type(exc).__name__}: {exc}"
            self._log(msg)
            result["success"] = False
            result["errors"].append(msg)

        return result
