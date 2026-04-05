"""Main application window for PyDataVault."""

import subprocess
import platform
from PySide6.QtWidgets import (
    QMainWindow,
    QTabWidget,
    QMessageBox,
)
from PySide6.QtCore import Qt

from . import config
from . import database as db
from .wafer_widget import WaferWidget
from .project_widget import ProjectWidget


class MainWindow(QMainWindow):
    """Main application window with tabs for wafers and projects."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PyDataVault")
        self.resize(1400, 900)

        # Initialize database and widgets
        self.wafer_widget = WaferWidget()
        self.project_widget = ProjectWidget()

        # Create tab widget as central widget
        self.tabs = QTabWidget()
        self.tabs.addTab(self.wafer_widget, "Wafers / Flakes")
        self.tabs.addTab(self.project_widget, "Projects / Devices")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self.tabs)

        # Create menu bar
        self._create_menu_bar()

        # Create status bar
        self.status_bar = self.statusBar()
        self._update_status_bar()

    def _create_menu_bar(self):
        """Create the menu bar with File and Help menus."""
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("File")

        open_db_action = file_menu.addAction("Open Database Folder")
        open_db_action.triggered.connect(self._open_database_folder)

        refresh_action = file_menu.addAction("Refresh All")
        refresh_action.triggered.connect(self._refresh_all)

        file_menu.addSeparator()

        exit_action = file_menu.addAction("Exit")
        exit_action.triggered.connect(self.close)

        # Help menu
        help_menu = menubar.addMenu("Help")

        about_action = help_menu.addAction("About")
        about_action.triggered.connect(self._show_about)

    def _open_database_folder(self):
        """Open the database folder in the system file explorer."""
        try:
            if platform.system() == "Windows":
                subprocess.Popen(f'explorer "{config.ROOT_PATH}"')
            elif platform.system() == "Darwin":  # macOS
                subprocess.Popen(["open", config.ROOT_PATH])
            else:  # Linux
                subprocess.Popen(["xdg-open", config.ROOT_PATH])
        except Exception as e:
            QMessageBox.warning(
                self,
                "Error",
                f"Could not open database folder:\n{str(e)}",
            )

    def _refresh_all(self):
        """Refresh all widgets and update status bar."""
        if hasattr(self.wafer_widget, "refresh"):
            self.wafer_widget.refresh()
        if hasattr(self.project_widget, "refresh"):
            self.project_widget.refresh()
        self._update_status_bar()

    def _show_about(self):
        """Show the about dialog."""
        QMessageBox.about(
            self,
            "About PyDataVault",
            "PyDataVault v0.1.0\n\n"
            "A PySide6 application for managing lab data and experiments.\n\n"
            "Database location: " + config.ROOT_PATH,
        )

    def _update_status_bar(self):
        """Update the status bar with database info."""
        try:
            flakes_count = db.count_flakes()
            devices_count = db.count_devices()
            status_text = (
                f"Database: {config.DB_FILE} | "
                f"Flakes: {flakes_count} | "
                f"Devices: {devices_count}"
            )
            self.status_bar.showMessage(status_text)
        except Exception as e:
            self.status_bar.showMessage(f"Database: {config.DB_FILE} | Error: {str(e)}")

    def _on_tab_changed(self):
        """Handle tab change events."""
        self._update_status_bar()
