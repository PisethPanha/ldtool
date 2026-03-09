import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from src.ui.main_window import MainWindow


def _load_stylesheet() -> str:
    """Load global QSS from ui/style.qss (returns empty string if missing)."""
    style_path = Path(__file__).resolve().parent / "ui" / "style.qss"
    try:
        return style_path.read_text(encoding="utf-8")
    except OSError:
        return ""


def main():
    """Create QApplication, show the main window, and start the event loop.

    Any exceptions during initialization or runtime are caught and printed
    to stderr so that developers can diagnose startup failures.
    """

    try:
        app = QApplication(sys.argv)
        app.setApplicationName("LD Automation Tool")
        app.setStyleSheet(_load_stylesheet())

        window = MainWindow()
        window.show()

        sys.exit(app.exec())
    except Exception as exc:  # pylint: disable=broad-except
        # Print the exception to the console; GUI may not be available.
        print("Unhandled exception while running application:\n", exc, file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
