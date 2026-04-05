"""Entry point for PyDataVault application."""

import sys
from PySide6.QtWidgets import QApplication

from . import config
from . import database as db
from .main_window import MainWindow


def main():
    """Initialize and run the PyDataVault application."""
    # Ensure all necessary directories exist
    config.ensure_dirs()

    # Initialize the database
    db.init_db()

    # Create the application
    app = QApplication(sys.argv)

    # Create and show the main window
    window = MainWindow()
    window.show()

    # Run the event loop
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
