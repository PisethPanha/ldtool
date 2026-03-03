"""Background task runner using PySide6 threading primitives.

This module provides a simple helper for executing functions on a
QThreadPool while exposing Qt signals for logging, progress updates, and
completion.  It is intentionally minimal yet usable for a variety of
operations without blocking the UI.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class TaskRunner(QObject):
    """Manager that dispatches callables to a shared thread pool.

    Signals
    -------
    on_log : Signal[str]
        Emitted when a task wishes to report a textual message.
    on_progress : Signal[int, int]
        Emitted with ``instance_id`` and ``percent`` when work progresses.
    on_done : Signal[Any]
        Emitted when a task completes; ``result`` is whatever the callable
        returned or the exception instance if an error occurred.
    """

    on_log = Signal(str)
    on_progress = Signal(int, int)
    on_done = Signal(object)
    on_error = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._pool = QThreadPool.globalInstance()

    def run(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """Execute ``func`` with given arguments on a background thread.

        ``func`` may optionally accept keyword arguments ``log_fn`` and
        ``progress_fn`` which will be passed callbacks bound to the
        appropriate signals.  Any positional/keyword arguments here are
        forwarded to the function before the log/progress callbacks.
        """

        runnable = self._TaskRunnable(func, self, args, kwargs)
        self._pool.start(runnable)

    class _TaskRunnable(QRunnable):
        def __init__(self, func: Callable[..., Any], owner: TaskRunner, args: tuple, kwargs: dict):
            super().__init__()
            self.func = func
            self.owner = owner
            self.args = args
            self.kwargs = kwargs
            # make runnable auto-delete when finished
            self.setAutoDelete(True)

        def run(self) -> None:
            def safe_emit(signal: Signal, *params: Any) -> None:
                try:
                    signal.emit(*params)
                except Exception:  # pragma: no cover - defensive
                    pass

            def log_fn(msg: str) -> None:
                safe_emit(self.owner.on_log, msg)

            def progress_fn(instance_id: int, percent: int) -> None:
                safe_emit(self.owner.on_progress, instance_id, percent)

            try:
                result = self.func(
                    *self.args,
                    **self.kwargs,
                    log_fn=log_fn,
                    progress_fn=progress_fn,
                )
            except Exception as exc:  # pragma: no cover - defensive
                error_msg = f"task execution failed: {type(exc).__name__}: {exc}"
                safe_emit(self.owner.on_error, error_msg)
                safe_emit(self.owner.on_log, error_msg)
                safe_emit(self.owner.on_done, None)
                return

            safe_emit(self.owner.on_done, result)
