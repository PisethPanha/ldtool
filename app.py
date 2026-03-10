import sys
from src.utils.resource_path import resource_path

from PySide6.QtWidgets import QApplication

from src.ui.main_window import MainWindow


def apply_theme(app):
    qss_path = resource_path("assets/styles/dark_theme.qss")
    try:
        with open(qss_path, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())
    except Exception:
        pass


def main():
    """Create QApplication, show the main window, and start the event loop.

    Any exceptions during initialization or runtime are caught and printed
    to stderr so that developers can diagnose startup failures.
    """

    try:
        app = QApplication(sys.argv)
        app.setApplicationName("LD Automation Tool")
        apply_theme(app)

        window = MainWindow()
        window.show()

        sys.exit(app.exec())
    except Exception as exc:  # pylint: disable=broad-except
        # Print the exception to the console; GUI may not be available.
        print("Unhandled exception while running application:\n", exc, file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
