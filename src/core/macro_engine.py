import json
import random
import time
from dataclasses import dataclass, field
from threading import Event
from typing import Any, Callable, Dict, List, Tuple, Union

from src.core.adb_manager import ADBManager


# ------------------------------------------------------------------
# Safe feed scroll configuration
# ------------------------------------------------------------------
@dataclass
class SafeFeedScrollConfig:
    """Configuration for safe Facebook-feed scroll mode."""

    enabled: bool = True
    x_mode: str = "right_bias"          # "right_bias" or "center"
    settle_ms: int = 2500               # post-swipe settle delay
    # thresholds for detecting unsafe swipes (720x1280 base)
    max_y_distance: int = 420
    max_duration: int = 300
    max_start_y: int = 980
    min_end_y: int = 300
    # safe swipe presets for 720×1280
    presets_right: List[List[int]] = field(
        default_factory=lambda: [[520, 920, 520, 640, 220]]
    )
    presets_center: List[List[int]] = field(
        default_factory=lambda: [[360, 900, 360, 620, 220]]
    )

    @classmethod
    def from_config(cls, cfg: Dict[str, str]) -> "SafeFeedScrollConfig":
        return cls(
            enabled=cfg.get("safe_feed_scroll_enabled", "true").lower() == "true",
            x_mode=cfg.get("safe_feed_scroll_x_mode", "right_bias"),
            settle_ms=int(cfg.get("safe_feed_scroll_settle_ms", "2500")),
        )


class MacroEngine:
    """Simple engine for running touch macros via ADB.

    A macro is a dictionary containing a ``name`` and a list of ``steps``.
    Each step is itself a single-key dictionary describing an action with
    parameters.  Supported actions are defined in :meth:`_valid_actions`.

    Randomization options allow the caller to introduce a small amount of
    jitter to taps/swipes and to add random delays between steps for
    less robotic behaviour.
    """

    def __init__(
        self,
        pixel_jitter: int = 0,
        delay_jitter_ms: int = 0,
        log_fn=lambda m: None,
        safe_feed_scroll: SafeFeedScrollConfig | None = None,
    ):
        self.pixel_jitter = pixel_jitter
        self.delay_jitter_ms = delay_jitter_ms
        self._log = log_fn
        self._sfs = safe_feed_scroll

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
            if not isinstance(step, dict):
                return False, f"step {idx} must be a dict"
            # allow optional 'safe_feed_scroll' flag alongside the action key
            action_keys = [k for k in step if k != "safe_feed_scroll"]
            if len(action_keys) != 1:
                return False, f"step {idx} must have exactly one action key"
            action = action_keys[0]
            params = step[action]
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
    # safe feed scroll helpers
    # ------------------------------------------------------------------
    def _is_unsafe_swipe(self, x1: int, y1: int, x2: int, y2: int, dur: int) -> Tuple[bool, str]:
        """Return (True, reason) if this swipe is risky for FB feed scrolling."""
        sfs = self._sfs
        if sfs is None or not sfs.enabled:
            return False, ""

        y_dist = abs(y1 - y2)
        if y_dist > sfs.max_y_distance:
            return True, f"Y distance {y_dist}px > {sfs.max_y_distance}px"
        if dur > sfs.max_duration:
            return True, f"duration {dur}ms > {sfs.max_duration}ms"
        if y1 > sfs.max_start_y:
            return True, f"start Y {y1} > {sfs.max_start_y} (too low)"
        if y2 < sfs.min_end_y:
            return True, f"end Y {y2} < {sfs.min_end_y} (too high)"
        return False, ""

    def _get_safe_swipe(self) -> List[int]:
        """Pick a safe swipe preset with small random jitter."""
        sfs = self._sfs or SafeFeedScrollConfig()
        presets = sfs.presets_right if sfs.x_mode == "right_bias" else sfs.presets_center
        base = random.choice(presets)
        # apply small ±5px jitter for naturalness
        return [
            base[0] + random.randint(-5, 5),
            base[1] + random.randint(-8, 8),
            base[2] + random.randint(-5, 5),
            base[3] + random.randint(-8, 8),
            base[4],
        ]

    def _execute_safe_feed_swipe(
        self,
        adb: ADBManager,
        serial: str,
        original: List[int],
        reason: str,
        stop_event: Event,
    ) -> None:
        """Replace an unsafe swipe with a safe one, log, settle."""
        safe = self._get_safe_swipe()
        sfs = self._sfs or SafeFeedScrollConfig()

        self._log(
            f"[safe-scroll] original swipe {original} replaced → {safe}  "
            f"reason: {reason}"
        )

        adb.shell(serial, f"input swipe {safe[0]} {safe[1]} {safe[2]} {safe[3]} {safe[4]}")

        # post-swipe settle delay (randomised ±150 ms)
        settle = sfs.settle_ms + random.randint(-150, 150)
        settle = max(500, settle)
        self._log(f"[safe-scroll] settle delay {settle}ms")

        # sleep in small chunks so stop_event is honoured
        elapsed = 0
        while elapsed < settle:
            if stop_event.is_set():
                break
            chunk = min(100, settle - elapsed)
            time.sleep(chunk / 1000.0)
            elapsed += chunk

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
        pause_event: Event | None = None,
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
                # honour pause: block until resumed or stopped
                if pause_event is not None:
                    while not pause_event.is_set():
                        if stop_event.is_set():
                            break
                        time.sleep(0.1)

                if stop_event.is_set():
                    result["success"] = False
                    result["errors"].append("stopped")
                    break

                action_keys = [k for k in step if k != "safe_feed_scroll"]
                action = action_keys[0]
                params = step[action]
                step_safe_flag = step.get("safe_feed_scroll", False)

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
                        original = [x1, y1, x2, y2, dur]

                        # --- safe feed scroll logic ---
                        use_safe = False
                        reason = ""
                        if step_safe_flag:
                            use_safe = True
                            reason = "safe_feed_scroll flag"
                        elif self._sfs and self._sfs.enabled:
                            use_safe, reason = self._is_unsafe_swipe(x1, y1, x2, y2, dur)

                        if use_safe:
                            self._execute_safe_feed_swipe(
                                adb, serial, original, reason, stop_event,
                            )
                        else:
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
