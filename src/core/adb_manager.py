"""Wrapper around adbutils for managing Android devices.

Provides listing, connecting, shell execution and readiness checks with
built-in error handling and timeouts.
"""

from __future__ import annotations

import time
from typing import Callable, List, Optional


class ADBManager:
    """Helper wrapper around `adbutils` to interact with devices.

    Timeouts and errors are caught and surfaced via return values.  A
    logging callback may be passed but is not strictly required.
    """

    def __init__(self, adb_path: str, log_fn: Callable[[str], None] = lambda msg: None):
        self._adb_path = adb_path
        self._log = log_fn

        # configure adbutils to use the given binary
        import adbutils

        adbutils.ADB_PATH = adb_path
        self._adb = adbutils.AdbClient()

    def list_devices(self) -> List[str]:
        try:
            return [d.serial for d in self._adb.device_list()]
        except Exception as exc:  # pragma: no cover - defensive
            self._log(f"error listing devices: {exc}")
            return []

    def connect_localhost_ports(self, ports: List[int]) -> List[str]:
        connected: List[str] = []
        for port in ports:
            target = f"127.0.0.1:{port}"
            if self.connect_host(target):
                connected.append(target)
        return connected

    def connect_host(self, serial: str, timeout: int = 5) -> bool:
        """Connect to a remote device and poll until it appears in device list.

        Calls adb.connect(serial, timeout=timeout) and then polls
        device_list() for up to ``timeout`` seconds until the serial appears.

        Returns ``True`` if the device appears in the list, ``False`` otherwise.
        """

        try:
            self._adb.connect(serial, timeout=timeout)
        except Exception as exc:
            self._log(f"connect failed for {serial}: {exc}")
            return False

        # Poll until device appears or timeout
        elapsed = 0
        interval = 0.5
        while elapsed < timeout:
            try:
                devices = self.list_devices()
                if serial in devices:
                    self._log(f"device {serial} connected and listed")
                    return True
            except Exception as exc:
                self._log(f"error polling device list: {exc}")
            time.sleep(interval)
            elapsed += interval

        self._log(f"timeout waiting for device {serial} to appear in list")
        return False

    def wait_for_new_device(self, before: set, timeout_s: int = 30) -> Optional[str]:
        """Poll for a new device that is not in the ``before`` set.

        Polls adb devices every 0.5 seconds for up to ``timeout_s`` seconds.
        Returns the first newly-detected device serial, or ``None`` on timeout.
        
        This is useful after launching an LDPlayer instance: pass the devices
        that existed before launch, then detect which new serial appears.
        """

        elapsed = 0
        interval = 0.5
        while elapsed < timeout_s:
            try:
                current = set(self.list_devices())
                new_serials = current - before
                if new_serials:
                    serial = new_serials.pop()
                    self._log(f"detected new device: {serial}")
                    return serial
            except Exception as exc:
                self._log(f"error polling for new device: {exc}")
            time.sleep(interval)
            elapsed += interval

        self._log(f"timeout: no new device appeared within {timeout_s}s")
        return None
    def shell(self, serial: str, cmd: str, max_retries: int = 3) -> str:
        """Execute a shell command on the device with retry logic.

        Returns the command output as a string, or an empty string on error.
        Attempts to re-detect the device if it temporarily disconnects.
        
        Args:
            serial: device serial
            cmd: shell command to execute
            max_retries: maximum number of retry attempts (default 3)
        """
        retry_count = 0
        while retry_count < max_retries:
            try:
                # Check if device exists
                devices = self.list_devices()
                if serial not in devices:
                    if retry_count < max_retries - 1:
                        self._log(f"device '{serial}' not found, retrying... (attempt {retry_count + 1}/{max_retries})")
                        time.sleep(0.5)
                        retry_count += 1
                        continue
                    else:
                        self._log(f"shell error: device '{serial}' not found after {max_retries} attempts")
                        return ""
                
                # Device found, execute command
                dev = self._adb.device(serial=serial)
                result = dev.shell(cmd, timeout=10)
                return result
            except Exception as exc:
                if retry_count < max_retries - 1:
                    self._log(f"shell error on {serial}: {exc}, retrying... (attempt {retry_count + 1}/{max_retries})")
                    time.sleep(0.5)
                    retry_count += 1
                else:
                    self._log(f"shell error on {serial} after {max_retries} attempts: {exc}")
                    return ""
        
        return ""

    def is_device_ready(self, serial: str) -> bool:
        # check property sys.boot_completed
        val = self.shell(serial, "getprop sys.boot_completed")
        return val.strip() == "1"

    def launch_app(self, serial: str, package: str, activity: Optional[str] = None) -> bool:
        """Launch an app on the device.

        If ``activity`` is provided, uses ``am start package/activity``.
        Otherwise, uses ``monkey -p package -c android.intent.category.LAUNCHER 1``
        to launch the app's default main activity.

        Returns ``True`` on success, ``False`` if the command failed.
        """

        if activity:
            cmd = f"am start -n {package}/{activity}"
        else:
            cmd = f"monkey -p {package} -c android.intent.category.LAUNCHER 1"

        try:
            output = self.shell(serial, cmd)
            if "error" in output.lower() or "exception" in output.lower():
                self._log(f"launch_app {package} on {serial} returned error: {output}")
                return False
            self._log(f"successfully launched {package} on {serial}")
            return True
        except Exception as exc:
            self._log(f"error launching {package} on {serial}: {exc}")
            return False

    def force_stop_app(self, serial: str, package: str) -> bool:
        """Force-stop an application on the device.

        Returns ``True`` if the command succeeded, ``False`` otherwise.
        """

        cmd = f"am force-stop {package}"
        try:
            self.shell(serial, cmd)
            self._log(f"force-stopped {package} on {serial}")
            return True
        except Exception as exc:
            self._log(f"error force-stopping {package} on {serial}: {exc}")
            return False
