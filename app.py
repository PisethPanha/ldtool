import sys

from PySide6.QtWidgets import QApplication

from src.ui.main_window import MainWindow


def main():
    """Create QApplication, show the main window, and start the event loop.

    Any exceptions during initialization or runtime are caught and printed
    to stderr so that developers can diagnose startup failures.
    """

    try:
        app = QApplication(sys.argv)
        app.setApplicationName("LD Automation Tool")

        window = MainWindow()
        window.show()

        sys.exit(app.exec())
    except Exception as exc:  # pylint: disable=broad-except
        # Print the exception to the console; GUI may not be available.
        print("Unhandled exception while running application:\n", exc, file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
