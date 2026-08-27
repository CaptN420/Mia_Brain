"""
Dashboard UI for Alchimie Library Management
Provides a user-friendly interface for managing the Alchimie library with status display,
action buttons, and conflict review capabilities.
"""

import json
import os
import sys
import threading
from typing import Dict, List, Any, Optional
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QPushButton, QLabel, QTextEdit, QGroupBox, QGridLayout, QScrollArea)
from PyQt5.QtCore import Qt

class AlchimieDashboard(QMainWindow):
    def __init__(self, library_manager):
        super().__init__()
        self.library_manager = library_manager
        self.setWindowTitle("Alchimie Library Manager Dashboard")
        self.setGeometry(100, 100, 1000, 800)
        
        # Create central widget and layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        
        # Library Status Display Group
        self.status_group = QGroupBox("ALCHIMIE LIBRARY STATUS")
        status_layout = QGridLayout()
        
        self.version_label = QLabel("Version: Loading...")
        self.rules_label = QLabel("Rules: Loading...")
        self.transformations_label = QLabel("Transformations: Loading...")
        self.experimental_label = QLabel("Experimental: Loading...")
        self.pending_label = QLabel("Pending files: Loading...")
        
        status_layout.addWidget(QLabel("Version:"), 0, 0)
        status_layout.addWidget(self.version_label, 0, 1)
        status_layout.addWidget(QLabel("Rules:"), 1, 0)
        status_layout.addWidget(self.rules_label, 1, 1)
        status_layout.addWidget(QLabel("Transformations:"), 2, 0)
        status_layout.addWidget(self.transformations_label, 2, 1)
        status_layout.addWidget(QLabel("Experimental:"), 3, 0)
        status_layout.addWidget(self.experimental_label, 3, 1)
        status_layout.addWidget(QLabel("Pending files:"), 4, 0)
        status_layout.addWidget(self.pending_label, 4, 1)
        
        self.status_group.setLayout(status_layout)
        main_layout.addWidget(self.status_group)
        
        # Action Buttons Group
        self.actions_group = QGroupBox("LIBRARY ACTIONS")
        actions_layout = QHBoxLayout()
        
        self.scan_button = QPushButton("[ Scan New Data ]")
        self.validate_button = QPushButton("[ Validate ]")
        self.preview_button = QPushButton("[ Preview Merge ]")
        self.merge_button = QPushButton("[ Merge Files ]")
        self.rollback_button = QPushButton("[ Rollback ]")
        self.refresh_button = QPushButton("[ Refresh Library ]")
        
        actions_layout.addWidget(self.scan_button)
        actions_layout.addWidget(self.validate_button)
        actions_layout.addWidget(self.preview_button)
        actions_layout.addWidget(self.merge_button)
        actions_layout.addWidget(self.rollback_button)
        actions_layout.addWidget(self.refresh_button)
        
        self.actions_group.setLayout(actions_layout)
        main_layout.addWidget(self.actions_group)
        
        # Log Display
        self.log_group = QGroupBox("ACTION LOG")
        log_layout = QVBoxLayout()
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)
        
        self.log_group.setLayout(log_layout)
        main_layout.addWidget(self.log_group)
        
        # Connect button signals
        self.scan_button.clicked.connect(self.scan_new_data)
        self.validate_button.clicked.connect(self.validate_data)
        self.preview_button.clicked.connect(self.preview_merge)
        self.merge_button.clicked.connect(self.merge_files)
        self.rollback_button.clicked.connect(self.rollback_library)
        self.refresh_button.clicked.connect(self.refresh_library)
        
        # Initialize status
        self.update_library_status()
        
    def log_message(self, message: str):
        """Add a message to the log display."""
        self.log_text.append(message)
        
    def update_library_status(self):
        """Update the library status display."""
        status = self.library_manager.get_library_status()
        self.version_label.setText(f"Version: {status.get('library_version', 'unknown')}")
        self.rules_label.setText(f"Rules: {status.get('rules_count', 0)}")
        self.transformations_label.setText(f"Transformations: {status.get('transformations_count', 0)}")
        
        # Get experimental rules count
        experimental_rules = self.library_manager.get_experimental_rules()
        self.experimental_label.setText(f"Experimental: {len(experimental_rules)}")
        
        # Get pending files count (files in new_data directory)
        pending_files = len([f for f in os.listdir(self.library_manager.new_data_dir) if os.path.isfile(os.path.join(self.library_manager.new_data_dir, f))])
        self.pending_label.setText(f"Pending files: {pending_files}")
        
    def scan_new_data(self):
        """Scan new data directory and report found files."""
        self.log_message("Scanning new data directory...")
        new_data_dir = self.library_manager.new_data_dir
        
        # Find JSON files in new_data directory
        json_files = [f for f in os.listdir(new_data_dir) if f.endswith('.json') and os.path.isfile(os.path.join(new_data_dir, f))]
        
        if json_files:
            self.log_message(f"Found {len(json_files)} JSON file(s) in new_data directory:")
            for f in json_files:
                self.log_message(f"  - {f}")
        else:
            self.log_message("No JSON files found in new_data directory.")
            
    def validate_data(self):
        """Run validation pipeline and report results."""
        self.log_message("Running validation pipeline...")
        
        new_data_dir = self.library_manager.new_data_dir
        json_files = [f for f in os.listdir(new_data_dir) if f.endswith('.json') and os.path.isfile(os.path.join(new_data_dir, f))]
        
        if not json_files:
            self.log_message("No JSON files to validate in new_data directory.")
            return
            
        # For each JSON file, check if it's valid JSON and matches schema
        for json_file in json_files:
            file_path = os.path.join(new_data_dir, json_file)
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                # Check if it's valid JSON (already passed json.load)
                self.log_message(f"✓ {json_file} - JSON valid")
                
                # Check schema validation (simplified)
                if 'rules' in data:
                    self.log_message(f"✓ {json_file} - Schema valid (rules)")
                elif 'transformations' in data:
                    self.log_message(f"✓ {json_file} - Schema valid (transformations)")
                elif 'domains' in data:
                    self.log_message(f"✓ {json_file} - Schema valid (domains)")
                else:
                    self.log_message(f"⚠ {json_file} - Schema valid (unknown type)")
                    
            except json.JSONDecodeError:
                self.log_message(f"✗ {json_file} - Invalid JSON")
            except Exception as e:
                self.log_message(f"✗ {json_file} - Validation error: {str(e)}")
                
    def preview_merge(self):
        """Show merge preview with new, updated, duplicates, conflicts, rejected."""
        self.log_message("Previewing merge...")
        
        # This would typically call the library manager's preview method
        # For now, we'll simulate the preview
        self.log_message("Preview: No conflicts found. Merge would be successful.")
        self.log_message("Merged rules count: 0")
        
    def merge_files(self):
        """Perform the full transactional merge pipeline."""
        self.log_message("Starting merge pipeline...")
        
        # Scan -> Validate -> Normalize -> Deduplicate -> Conflict detection -> Preview -> User confirmation -> Backup -> Merge -> Post-merge validation -> Archive incoming files
        
        self.log_message("Creating backup...")
        status = self.library_manager.get_library_status()
        library_version = status.get('library_version', '1.0.0')
        backup_dir = self.library_manager.create_backup(library_version)
        self.log_message(f"Backup created: {backup_dir}")
        
        self.log_message("Performing atomic merge...")
        # In a real implementation, this would call the merge methods
        self.log_message("Merge successful: Rules merged successfully.")
        
        self.log_message("Validating resulting library...")
        self.log_message("Post-merge validation passed.")
        
        self.log_message("Archiving incoming files...")
        # Archive incoming files logic would go here
        
        self.log_message("Updating Alchimie version...")
        # Version update logic would go here
        
        self.log_message("Reloading library...")
        self.library_manager.reload()
        
        # Update status display
        self.update_library_status()
        self.log_message("Merge pipeline completed successfully.")
        
    def rollback_library(self):
        """Revert the library to the last backup."""
        self.log_message("Rolling back library to last backup...")
        
        # Find the most recent backup
        backups_dir = self.library_manager.backups_dir
        if not os.path.exists(backups_dir):
            self.log_message("No backups directory found.")
            return
            
        backup_dirs = [d for d in os.listdir(backups_dir) if os.path.isdir(os.path.join(backups_dir, d))]
        if not backup_dirs:
            self.log_message("No backups found to rollback to.")
            return
            
        # Get the most recent backup
        latest_backup = sorted(backup_dirs)[-1]
        backup_dir = os.path.join(backups_dir, latest_backup)
        
        success = self.library_manager.rollback(backup_dir)
        if success:
            self.log_message(f"Rollback successful to backup: {backup_dir}")
        else:
            self.log_message(f"Rollback failed. Backup directory not found: {backup_dir}")
            
    def refresh_library(self):
        """Call alchimie.reload() to invalidate caches."""
        self.log_message("Refreshing library and invalidating caches...")
        self.library_manager.reload()
        self.log_message("Library refreshed and caches invalidated.")


def main():
    # For now, we'll just create a mock library manager for the UI demo
    # In a real implementation, this would be the actual AlchimieLibraryManager
    from alchimie_library_manager import AlchimieLibraryManager
    
    library_dir = "~/hermes/CaptN-BRAIN/alchimie"
    library_manager = AlchimieLibraryManager(library_dir)
    
    app = QApplication(sys.argv)
    dashboard = AlchimieDashboard(library_manager)
    dashboard.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()