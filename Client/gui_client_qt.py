import sys
import json
import os
import csv
from datetime import datetime
import requests
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import socket
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                            QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                            QTabWidget, QFileDialog, QMessageBox, QGridLayout,
                            QFrame, QScrollArea)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QPixmap, QPalette, QColor, QFont, QImage, QCursor, QIcon

# Reduce logging to only warnings and errors
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

def get_local_ip():
    """Get local IP address"""
    try:
        # Use socket to get the local IP address
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"  # Fallback to localhost

def hex_to_rgb(hex_color):
    """Convert hex color string or color name to RGB values for Qt stylesheet"""
    # Color name mapping to hex values
    color_map = {
        'gold': '#FFD700',
        'silver': '#C0C0C0',
        '#cd7f32': '#CD7F32'  # bronze
    }
    
    # Convert color name to hex if it's in our map
    if hex_color in color_map:
        hex_color = color_map[hex_color]
    
    # Process hex color
    hex_color = hex_color.lstrip('#')
    if len(hex_color) == 6:
        try:
            r = int(hex_color[0:2], 16)
            g = int(hex_color[2:4], 16)
            b = int(hex_color[4:6], 16)
            return f"{r}, {g}, {b}"
        except ValueError:
            return "255, 255, 255"  # Default to white on error
    
    return "255, 255, 255"  # Default white if invalid hex

class NetworkThread(QThread):
    leaderboard_updated = pyqtSignal(list)
    server_status_updated = pyqtSignal(str)
    
    def __init__(self, config):
        super().__init__()
        self.config = config
        self._running = True
        self._last_data = []  # Store the last data to avoid unnecessary updates
        
    def run(self):
        while self._running:
            try:
                # Read lap times from CSV
                lap_times = self.read_lap_times()
                
                # Only emit update signal if the data has actually changed
                if self._data_changed(lap_times):
                    self._last_data = lap_times.copy()  # Store a copy of current data
                    self.leaderboard_updated.emit(lap_times)
                    self.server_status_updated.emit("Local leaderboard loaded")
            except Exception as e:
                self.server_status_updated.emit(f"Error reading leaderboard: {str(e)}")
            
            # Sleep for the configured interval
            self.msleep(self.config.get('refresh_interval', 5) * 1000)
    
    def _data_changed(self, new_data):
        """Check if the data has meaningfully changed to avoid unnecessary updates"""
        if len(new_data) != len(self._last_data):
            return True
            
        for i, (old_entry, new_entry) in enumerate(zip(self._last_data, new_data)):
            # Check if position, driver or lap time changed
            if (old_entry.get('driver_name') != new_entry.get('driver_name') or
                abs(old_entry.get('lap_time', 0) - new_entry.get('lap_time', 0)) > 0.001):
                return True
                
        return False
    
    def read_lap_times(self):
        """Read and sort lap times from CSV file, keeping only fastest lap per driver"""
        lap_times = {}  # Dictionary to store fastest lap per driver
        csv_file = 'lap_times.csv'
        
        if os.path.exists(csv_file):
            try:
                with open(csv_file, 'r', newline='') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        try:
                            driver_name = str(row.get('driver_name', ''))
                            lap_time = float(row.get('lap_time', 0))
                            
                            # Only keep the fastest lap time for each driver
                            if driver_name not in lap_times or lap_time < lap_times[driver_name]['lap_time']:
                                lap_times[driver_name] = {
                                    'simulator_id': str(row.get('simulator_id', '1')),
                                    'driver_name': driver_name,
                                    'lap_time': lap_time,
                                    'email': str(row.get('email', '')),
                                    'timestamp': str(row.get('timestamp', ''))
                                }
                        except ValueError as e:
                            logger.warning(f"Error processing row {row}: {str(e)}")
                            continue
                        except Exception as e:
                            logger.warning(f"Unexpected error processing row {row}: {str(e)}")
                            continue
                    
                    # Convert dictionary to list and sort by lap time
                    sorted_times = sorted(lap_times.values(), key=lambda x: x['lap_time'])
                    # Return only top 10 fastest drivers
                    return sorted_times[:10]
            except Exception as e:
                logger.error(f"Error reading CSV file: {str(e)}")
        
        return []
            
    def stop(self):
        self._running = False
        self.wait()

class LeaderboardWindow(QWidget):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.background_image = None
        self.entry_widgets = []
        self.is_fullscreen = True
        self.dragging = False
        self.drag_position = None
        self.horizontal_offset = self.config.get('horizontal_offset', 0)  # Load saved horizontal offset
        self.vertical_offset = self.config.get('vertical_offset', 0)  # Load saved vertical offset
        self.vertical_spacing = self.config.get('vertical_spacing', 4)  # Get spacing between entries
        self.row_height_padding = self.config.get('row_height_padding', 16)  # Get internal row height padding
        self.setup_ui()
        self.hide()
        
    def setup_ui(self):
        # Set window flags for both modes
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Create a title bar for draggable mode
        self.title_bar = QWidget(self)
        title_bar_layout = QHBoxLayout(self.title_bar)
        title_bar_layout.setContentsMargins(10, 5, 10, 5)
        
        # Add left/right position controls
        self.left_button = QPushButton("◀", self)
        self.left_button.setStyleSheet("""
            QPushButton {
                background-color: rgba(40, 40, 40, 160);
                color: white;
                border: none;
                border-radius: 20px;
                font-size: 18px;
                font-weight: bold;
                padding: 10px;
            }
            QPushButton:hover {
                background-color: rgba(60, 60, 60, 200);
            }
        """)
        self.left_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.left_button.clicked.connect(self.move_left)
        self.left_button.setFixedSize(40, 40)
        
        self.right_button = QPushButton("▶", self)
        self.right_button.setStyleSheet("""
            QPushButton {
                background-color: rgba(40, 40, 40, 160);
                color: white;
                border: none;
                border-radius: 20px;
                font-size: 18px;
                font-weight: bold;
                padding: 10px;
            }
            QPushButton:hover {
                background-color: rgba(60, 60, 60, 200);
            }
        """)
        self.right_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.right_button.clicked.connect(self.move_right)
        self.right_button.setFixedSize(40, 40)
        
        # Add up and down buttons for vertical positioning
        self.up_button = QPushButton("▲", self)
        self.up_button.setStyleSheet("""
            QPushButton {
                background-color: rgba(40, 40, 40, 160);
                color: white;
                border: none;
                border-radius: 20px;
                font-size: 18px;
                font-weight: bold;
                padding: 10px;
            }
            QPushButton:hover {
                background-color: rgba(60, 60, 60, 200);
            }
        """)
        self.up_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.up_button.clicked.connect(self.move_up)
        self.up_button.setFixedSize(40, 40)
        
        self.down_button = QPushButton("▼", self)
        self.down_button.setStyleSheet("""
            QPushButton {
                background-color: rgba(40, 40, 40, 160);
                color: white;
                border: none;
                border-radius: 20px;
                font-size: 18px;
                font-weight: bold;
                padding: 10px;
            }
            QPushButton:hover {
                background-color: rgba(60, 60, 60, 200);
            }
        """)
        self.down_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.down_button.clicked.connect(self.move_down)
        self.down_button.setFixedSize(40, 40)
        
        # Add mode toggle button
        self.mode_button = QPushButton("🗗", self)
        self.mode_button.setStyleSheet("""
            QPushButton {
                background-color: rgba(40, 40, 40, 160);
                color: white;
                border: none;
                border-radius: 20px;
                font-size: 18px;
                font-weight: bold;
                padding: 10px;
            }
            QPushButton:hover {
                background-color: rgba(60, 60, 60, 200);
            }
        """)
        self.mode_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mode_button.clicked.connect(self.toggle_window_mode)
        self.mode_button.setFixedSize(40, 40)
        
        self.close_button = QPushButton("✕", self)
        self.close_button.setStyleSheet("""
            QPushButton {
                background-color: rgba(40, 40, 40, 160);
                color: white;
                border: none;
                border-radius: 20px;
                font-size: 18px;
                font-weight: bold;
                padding: 10px;
            }
            QPushButton:hover {
                background-color: rgba(255, 0, 0, 200);
            }
        """)
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.clicked.connect(self.close)
        self.close_button.setFixedSize(40, 40)
        
        title_bar_layout.addStretch()
        title_bar_layout.addWidget(self.left_button)
        title_bar_layout.addWidget(self.right_button)
        title_bar_layout.addWidget(self.up_button)
        title_bar_layout.addWidget(self.down_button)
        title_bar_layout.addWidget(self.mode_button)
        title_bar_layout.addWidget(self.close_button)
        
        self.title_bar.setFixedHeight(50)
        layout.addWidget(self.title_bar)
        
        # Rest of the UI setup remains the same
        self.leaderboard_panel = QWidget(self)
        self.leaderboard_panel.setFixedWidth(self.config.get('panel_width', 800))
        self.leaderboard_panel.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        panel_layout = QVBoxLayout(self.leaderboard_panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)
        
        headers = ['Pos', 'Driver', 'Time']
        self.header_widget = QWidget()
        self.header_widget.setStyleSheet("""
            QWidget {
                background-color: rgba(40, 40, 40, 220);
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
            }
            QLabel {
                color: white;
                font-size: 22px;
                font-weight: bold;
                padding: 12px;
            }
        """)
        header_layout = QHBoxLayout(self.header_widget)
        header_layout.setContentsMargins(20, 12, 20, 12)
        header_layout.setSpacing(40)
        
        widths = [
            self.config.get('position_width', 80),
            self.config.get('driver_width', 400),
            self.config.get('time_width', 200)
        ]
        for header, width in zip(headers, widths):
            label = QLabel(header)
            label.setFixedWidth(width)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            header_layout.addWidget(label)
        
        panel_layout.addWidget(self.header_widget)
        
        self.entries_widget = QWidget()
        self.entries_widget.setStyleSheet("""
            QWidget {
                background-color: rgba(40, 40, 40, 220);
                border-bottom-left-radius: 10px;
                border-bottom-right-radius: 10px;
            }
            QLabel {
                color: white;
                font-size: 18px;
                padding: 8px;
            }
        """)
        self.entries_layout = QVBoxLayout(self.entries_widget)
        self.entries_layout.setContentsMargins(20, 6, 20, 6)
        self.entries_layout.setSpacing(self.vertical_spacing)  # Use configured vertical spacing
        panel_layout.addWidget(self.entries_widget)
        
        layout.addWidget(self.leaderboard_panel, alignment=Qt.AlignmentFlag.AlignCenter)
        
        # Set initial window mode
        self.set_window_mode(True)
    
    def set_window_mode(self, fullscreen):
        self.is_fullscreen = fullscreen
        if fullscreen:
            # Remove borders in fullscreen mode
            self.header_widget.setStyleSheet("""
                QWidget {
                    background-color: rgba(40, 40, 40, 220);
                }
                QLabel {
                    color: white;
                    font-size: 22px;
                    font-weight: bold;
                    padding: 12px;
                }
            """)
            self.entries_widget.setStyleSheet("""
                QWidget {
                    background-color: rgba(40, 40, 40, 220);
                }
                QLabel {
                    color: white;
                    font-size: 18px;
                    padding: 8px;
                }
            """)
            
            # Show fullscreen
            self.showFullScreen()
            
            # Get actual screen dimensions
            screen = QApplication.primaryScreen().geometry()
            
            # Position title bar controls at the top right of the actual screen
            self.mode_button.setText("🗗")
            self.title_bar.move(screen.width() - 200, 20)
            
            # Position the leaderboard panel in the center
            self.center_leaderboard_with_offset()
            
            # Explicitly refresh background when going into fullscreen
            if hasattr(self, 'background_label') and self.config.get('background_image'):
                QTimer.singleShot(50, lambda: self.set_background(self.config['background_image']))
        else:
            # Regular windowed mode code remains the same
            self.header_widget.setStyleSheet("""
                QWidget {
                    background-color: rgba(40, 40, 40, 220);
                    border-top-left-radius: 10px;
                    border-top-right-radius: 10px;
                }
                QLabel {
                    color: white;
                    font-size: 22px;
                    font-weight: bold;
                    padding: 12px;
                }
            """)
            self.entries_widget.setStyleSheet("""
                QWidget {
                    background-color: rgba(40, 40, 40, 220);
                    border-bottom-left-radius: 10px;
                    border-bottom-right-radius: 10px;
                }
                QLabel {
                    color: white;
                    font-size: 18px;
                    padding: 8px;
                }
            """)
            self.showNormal()
            self.resize(self.leaderboard_panel.width() + 40, 800)  # Add padding
            self.mode_button.setText("⛶")
            # Center the window on screen
            screen = QApplication.primaryScreen().geometry()
            self.move(
                (screen.width() - self.width()) // 2,
                (screen.height() - self.height()) // 2
            )
            
            # Explicitly refresh background when going into windowed mode
            if hasattr(self, 'background_label') and self.config.get('background_image'):
                self.set_background(self.config['background_image'])
    
    def toggle_window_mode(self):
        # Save current mode
        was_fullscreen = self.is_fullscreen
        # Toggle mode
        self.set_window_mode(not was_fullscreen)
        # Make sure the background is preserved when switching modes
        if hasattr(self, 'background_label') and self.config.get('background_image'):
            QTimer.singleShot(100, lambda: self.set_background(self.config['background_image']))
    
    def mousePressEvent(self, event):
        if not self.is_fullscreen and event.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
    
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = False
            event.accept()
    
    def mouseMoveEvent(self, event):
        if not self.is_fullscreen and self.dragging:
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()
    
    def set_background(self, image_path):
        """Set the background image efficiently"""
        try:
            if not image_path or not os.path.exists(image_path):
                logger.warning(f"Background image not found: {image_path}")
                return
            
            # Create background label if it doesn't exist
            if not hasattr(self, 'background_label'):
                self.background_label = QLabel(self)
                self.background_label.lower()  # Keep it behind all other widgets
            
            # Load and scale image
            image = QImage(image_path)
            if image.isNull():
                logger.warning(f"Failed to load background image: {image_path}")
                return
            
            if self.is_fullscreen:
                try:
                    # Get actual screen size instead of hardcoded values
                    screen = QApplication.primaryScreen().geometry()
                    target_width = screen.width()
                    target_height = screen.height()
                    
                    # Choose aspect ratio mode based on fill_screen setting
                    aspect_mode = Qt.AspectRatioMode.IgnoreAspectRatio if self.config.get('fill_screen', False) else Qt.AspectRatioMode.KeepAspectRatio
                    
                    # Scale image to fit the actual screen
                    scaled_image = image.scaled(
                        target_width,
                        target_height,
                        aspect_mode,
                        Qt.TransformationMode.SmoothTransformation
                    )
                    
                    # Center the image on the screen if keeping aspect ratio
                    if not self.config.get('fill_screen', False):
                        x_offset = (target_width - scaled_image.width()) // 2
                        y_offset = (target_height - scaled_image.height()) // 2
                    else:
                        x_offset = 0
                        y_offset = 0
                    
                    self.background_label.setPixmap(QPixmap.fromImage(scaled_image))
                    self.background_label.setGeometry(x_offset, y_offset, scaled_image.width(), scaled_image.height())
                    
                except Exception as e:
                    logger.error(f"Error scaling fullscreen image: {str(e)}")
                    return
            else:
                try:
                    # Get current window size for windowed mode
                    screen = QApplication.primaryScreen().geometry()
                    window_size = self.size()
                    
                    # Use the selected aspect ratio in windowed mode
                    if self.config.get('use_tall_aspect', False):
                        # 1920x1536 aspect ratio
                        target_height = window_size.width() * (1536/1920)
                    elif self.config.get('use_1344_aspect', False):
                        # 1920x1344 aspect ratio
                        target_height = window_size.width() * (1344/1920)
                    else:
                        # 1920x1080 aspect ratio
                        target_height = window_size.width() * (1080/1920)
                    
                    scaled_image = image.scaled(
                        window_size.width(),
                        int(target_height),
                        Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation
                    )
                    
                    # Center vertically
                    y = (window_size.height() - scaled_image.height()) // 2
                    self.background_label.setPixmap(QPixmap.fromImage(scaled_image))
                    self.background_label.setGeometry(0, y, scaled_image.width(), scaled_image.height())
                except Exception as e:
                    logger.error(f"Error setting windowed background: {str(e)}")
                    return
            
            # Make sure the background is visible by explicitly showing it
            self.background_label.show()
            
            # Ensure background stays at the back
            self.background_label.lower()
        except Exception as e:
            logger.error(f"Unhandled error setting background: {str(e)}")
    
    def resizeEvent(self, event):
        """Handle window resize"""
        super().resizeEvent(event)
        
        # Get actual screen dimensions
        screen = QApplication.primaryScreen().geometry()
        
        # Update close button position based on actual screen size
        if self.is_fullscreen:
            self.close_button.move(screen.width() - 60, 20)
        
        # Update background if exists - do this for both windowed and fullscreen modes
        if hasattr(self, 'background_label') and self.config.get('background_image'):
            # Use a short delay to ensure the window has finished resizing
            QTimer.singleShot(100, lambda: self.set_background(self.config['background_image']))
        
        # Update leaderboard position only if the window size actually changed
        if self.is_fullscreen and (event.oldSize().width() != event.size().width() or 
                                  event.oldSize().height() != event.size().height()):
            # Use a short delay to ensure the window has finished resizing
            QTimer.singleShot(100, self.center_leaderboard_with_offset)
    
    def update_entries(self, data):
        """Update entries using widget recycling"""
        # Store current position and size before doing anything
        if hasattr(self, 'leaderboard_panel'):
            current_pos = (self.leaderboard_panel.x(), self.leaderboard_panel.y())
            current_size = (self.leaderboard_panel.width(), self.leaderboard_panel.height())
            logger.warning(f"BEFORE update - Panel pos: {current_pos}, size: {current_size[0]}x{current_size[1]}")
        else:
            current_pos = None
            current_size = None
        
        # Log the config font sizes before updating entries
        logger.warning(f"Config when updating entries: p1={self.config.get('p1_font_size')}, p2={self.config.get('p2_font_size')}, p3={self.config.get('p3_font_size')}, other={self.config.get('other_font_size')}")
        
        # Update row height padding and vertical spacing from config
        self.row_height_padding = self.config.get('row_height_padding', 16)
        self.vertical_spacing = self.config.get('vertical_spacing', 4)
        self.entries_layout.setSpacing(self.vertical_spacing)
        
        # Always set fixed width to ensure panel width doesn't change
        if current_size:
            self.leaderboard_panel.setFixedWidth(current_size[0])
        else:
            self.leaderboard_panel.setFixedWidth(self.config.get('panel_width', 800))
        
        # Hide all existing entry widgets
        for widget in self.entry_widgets:
            widget.hide()
        
        # If row height has changed, recreate all widgets to apply new height
        # This is more thorough than just updating existing ones
        if data:
            # Clear existing widgets
            for widget in self.entry_widgets:
                self.entries_layout.removeWidget(widget)
                widget.deleteLater()
            
            self.entry_widgets = []
            
            # Create new entry widgets with updated dimensions
            widths = [
                self.config.get('position_width', 80),
                self.config.get('driver_width', 400),
                self.config.get('time_width', 200)
            ]
            
            for i, entry in enumerate(data):
                entry_widget = self._create_entry_widget(widths)
                self._update_entry_widget(entry_widget, entry, i + 1)
                self.entry_widgets.append(entry_widget)
                self.entries_layout.addWidget(entry_widget, alignment=Qt.AlignmentFlag.AlignCenter)
                entry_widget.show()
        
        # Ensure size is maintained regardless of content changes    
        if current_size:
            self.leaderboard_panel.setFixedWidth(current_size[0])
        
        # Always restore position for both windowed and fullscreen modes
        if current_pos:
            self.leaderboard_panel.move(current_pos[0], current_pos[1])
        
        # Final verification that nothing changed our panel dimensions
        if hasattr(self, 'leaderboard_panel') and current_size and current_pos:
            new_size = (self.leaderboard_panel.width(), self.leaderboard_panel.height())
            new_pos = (self.leaderboard_panel.x(), self.leaderboard_panel.y())
            
            if new_size != current_size or new_pos != current_pos:
                logger.warning(f"Panel changed during update! Size: {current_size}->{new_size}, Pos: {current_pos}->{new_pos}")
                # Force restoration again
                self.leaderboard_panel.setFixedWidth(current_size[0])
                self.leaderboard_panel.move(current_pos[0], current_pos[1])
    
    def _create_entry_widget(self, widths):
        """Create a reusable entry widget"""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(40)
        
        # Use configured widths
        widths = [
            self.config.get('position_width', 80),
            self.config.get('driver_width', 400),
            self.config.get('time_width', 200)
        ]
        
        # Add labels for position, driver name, and time
        position_label = QLabel()
        position_label.setFixedWidth(widths[0])
        position_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        driver_label = QLabel()
        driver_label.setFixedWidth(widths[1])
        driver_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        
        time_label = QLabel()
        time_label.setFixedWidth(widths[2])
        time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        
        # Add the labels to the layout
        layout.addWidget(position_label)
        layout.addWidget(driver_label)
        layout.addWidget(time_label)
        
        # Use row height padding from config instead of fixed value
        row_padding = self.config.get('row_height_padding', 16)
        
        # Set fixed row height based on the largest font size plus padding
        max_font_size = max(
            self.config.get('p1_font_size', 24),
            self.config.get('p2_font_size', 22),
            self.config.get('p3_font_size', 20),
            self.config.get('other_font_size', 18)
        )
        
        # Add vertical padding based on config
        vertical_padding = self.config.get('vertical_spacing', 4)
        row_height = max_font_size + row_padding + vertical_padding
        widget.setFixedHeight(row_height)
        
        return widget
    
    def _update_entry_widget(self, widget, entry, position):
        """Update an existing entry widget"""
        labels = widget.findChildren(QLabel)
        
        # Use consistent padding regardless of screen size
        fixed_padding = 5  # Consistent padding for all elements
        
        if position <= 3:
            # Special colors for top 3
            pos_colors = ['gold', 'silver', '#cd7f32']
            pos_color = pos_colors[position-1]
            pos_names = ['1ST', '2ND', '3RD']
            
            # Get font sizes from config for positions 1-3
            font_sizes = [
                self.config.get('p1_font_size', 24),
                self.config.get('p2_font_size', 22),
                self.config.get('p3_font_size', 20)
            ]
            
            # Log the font sizes being applied for debugging
            logger.warning(f"Position {position}: Using font size {font_sizes[position-1]} from config")
            
            # Create special position indicator with position number and badge
            position_text = f"{pos_names[position-1]}"
            
            # Apply special styling with colored background
            labels[0].setStyleSheet(f"""
                color: black; 
                background-color: {pos_color}; 
                font-size: {font_sizes[position-1]}px;
                font-weight: bold;
                border-radius: 5px;
                padding: {fixed_padding}px;
            """)
            
            # Apply larger font sizes to driver names for top 3 positions
            labels[1].setStyleSheet(f"""
                color: {pos_color}; 
                font-size: {font_sizes[position-1]}px;
                font-weight: bold;
            """)
            
            # Apply larger font sizes to times for top 3 positions
            labels[2].setStyleSheet(f"""
                color: {pos_color}; 
                font-size: {font_sizes[position-1]}px;
                font-weight: bold;
            """)
            
            # Position text for top 3 with special badges
            labels[0].setText(position_text)
            
            # Add subtle background highlight for the top 3 rows
            bg_opacity = [30, 25, 20][position-1]  # Decreasing opacity for 1st, 2nd, 3rd
            widget.setStyleSheet(f"""
                background-color: rgba({hex_to_rgb(pos_color)}, {bg_opacity});
                border-radius: 8px;
                margin: 1px;
            """)
        else:
            # Regular styling for positions 4-10
            other_font_size = self.config.get('other_font_size', 18)
            
            # Log the font size being applied for debugging
            logger.warning(f"Position {position}: Using other_font_size {other_font_size} from config")
            
            labels[0].setStyleSheet(f"""
                color: white; 
                background: transparent; 
                font-size: {other_font_size}px;
                padding: {fixed_padding}px;
            """)
            labels[1].setStyleSheet(f"color: white; font-size: {other_font_size}px;")
            labels[2].setStyleSheet(f"color: white; font-size: {other_font_size}px;")
            
            # Regular position for 4-10
            labels[0].setText(str(position))
            
            # Clear any background for regular positions
            widget.setStyleSheet("")
        
        # Driver
        labels[1].setText(entry['driver_name'])
        
        # Time
        time_str = f"{int(entry['lap_time'] // 60):02d}:{entry['lap_time'] % 60:06.3f}"
        labels[2].setText(time_str)

    def update_panel_style(self, opacity):
        """Update the panel styling with given opacity"""
        if not hasattr(self, 'header_widget') or not hasattr(self, 'entries_widget'):
            return
        
        # Use fixed padding values rather than dynamic scaling
        fixed_header_padding = 8
        fixed_entry_padding = 5
        
        header_font_size = self.config.get('header_font_size', 24)
        entry_font_size = self.config.get('entry_font_size', 20)
            
        header_style = f"""
            QWidget {{
                background-color: rgba(40, 40, 40, {opacity});
                border-radius: 10px;
            }}
            QLabel {{
                color: white;
                font-size: {header_font_size}px;
                font-weight: bold;
                padding: {fixed_header_padding}px;
            }}
        """
        
        entries_style = f"""
            QWidget {{
                background-color: rgba(40, 40, 40, {opacity});
                border-radius: 10px;
            }}
            QLabel {{
                color: white;
                font-size: {entry_font_size}px;
                padding: {fixed_entry_padding}px;
            }}
        """
        
        self.header_widget.setStyleSheet(header_style)
        self.entries_widget.setStyleSheet(entries_style)
        
        # Update spacing values from config
        self.vertical_spacing = self.config.get('vertical_spacing', 4)
        self.row_height_padding = self.config.get('row_height_padding', 16)
        
        self.entries_layout.setSpacing(self.vertical_spacing)
        fixed_margin = 8  # Fixed margin
        
        self.entries_layout.setContentsMargins(20, fixed_margin, 20, fixed_margin)

    def update_column_widths(self):
        """Update all column widths based on current config"""
        # Update header widths
        header_labels = self.header_widget.findChildren(QLabel)
        widths = [
            self.config.get('position_width', 80),
            self.config.get('driver_width', 400),
            self.config.get('time_width', 200)
        ]
        for label, width in zip(header_labels, widths):
            label.setFixedWidth(width)
        
        # Update entry widths
        for entry_widget in self.entry_widgets:
            entry_labels = entry_widget.findChildren(QLabel)
            for label, width in zip(entry_labels, widths):
                label.setFixedWidth(width)
        
        # Update panel width
        self.leaderboard_panel.setFixedWidth(self.config.get('panel_width', 800))

    # Add new methods for moving the leaderboard
    def move_left(self):
        """Move the leaderboard to the left"""
        self.horizontal_offset -= 50
        self.config['horizontal_offset'] = self.horizontal_offset  # Save to config
        
        # Preserve Y position when moving horizontally
        if self.is_fullscreen and hasattr(self, 'leaderboard_panel'):
            current_y = self.leaderboard_panel.y()
            screen = QApplication.primaryScreen().geometry()
            panel_width = self.leaderboard_panel.width()
            centered_x = (screen.width() - panel_width) // 2 + self.horizontal_offset
            
            # Keep the panel within the screen bounds
            if centered_x < 0:
                centered_x = 0
            elif centered_x + panel_width > screen.width():
                centered_x = screen.width() - panel_width
            
            self.leaderboard_panel.move(centered_x, current_y)
        else:
            self.center_leaderboard_with_offset()
        
        # Save config file to make the position persistent
        with open('config.json', 'w') as f:
            json.dump(self.config, f)
    
    def move_right(self):
        """Move the leaderboard to the right"""
        self.horizontal_offset += 50
        self.config['horizontal_offset'] = self.horizontal_offset  # Save to config
        
        # Preserve Y position when moving horizontally
        if self.is_fullscreen and hasattr(self, 'leaderboard_panel'):
            current_y = self.leaderboard_panel.y()
            screen = QApplication.primaryScreen().geometry()
            panel_width = self.leaderboard_panel.width()
            centered_x = (screen.width() - panel_width) // 2 + self.horizontal_offset
            
            # Keep the panel within the screen bounds
            if centered_x < 0:
                centered_x = 0
            elif centered_x + panel_width > screen.width():
                centered_x = screen.width() - panel_width
            
            self.leaderboard_panel.move(centered_x, current_y)
        else:
            self.center_leaderboard_with_offset()
        
        # Save config file to make the position persistent
        with open('config.json', 'w') as f:
            json.dump(self.config, f)
    
    def center_leaderboard_with_offset(self):
        """Center the leaderboard with the current horizontal and vertical offsets"""
        if self.is_fullscreen:
            # Get actual screen dimensions
            screen = QApplication.primaryScreen().geometry()
            panel_width = self.leaderboard_panel.width()
            panel_height = self.leaderboard_panel.height()
            
            # Center panel horizontally with offset
            centered_x = (screen.width() - panel_width) // 2 + self.horizontal_offset
            # Center panel vertically with offset
            centered_y = (screen.height() - panel_height) // 2 + self.vertical_offset
            
            # Keep the panel within the screen bounds
            if centered_x < 0:
                centered_x = 0
            elif centered_x + panel_width > screen.width():
                centered_x = screen.width() - panel_width
                
            # Ensure the panel isn't positioned too low
            if centered_y < 0:
                centered_y = 0
            elif centered_y + panel_height > screen.height() - 20:
                centered_y = screen.height() - panel_height - 20
            
            self.leaderboard_panel.move(centered_x, centered_y)

    def update_font_sizes(self, data=None):
        """Update all font sizes based on current config"""
        # Update header font sizes
        header_font_size = self.config.get('header_font_size', 24)
        self.header_widget.setStyleSheet(f"""
            QWidget {{
                background-color: rgba(40, 40, 40, {self.config.get('opacity', 220)});
                {'' if self.is_fullscreen else 'border-top-left-radius: 10px; border-top-right-radius: 10px;'}
            }}
            QLabel {{
                color: white;
                font-size: {header_font_size}px;
                font-weight: bold;
                padding: 12px;
            }}
        """)
        
        # Update entries widget font size
        entry_font_size = self.config.get('entry_font_size', 20)
        self.entries_widget.setStyleSheet(f"""
            QWidget {{
                background-color: rgba(40, 40, 40, {self.config.get('opacity', 220)});
                {'' if self.is_fullscreen else 'border-bottom-left-radius: 10px; border-bottom-right-radius: 10px;'}
            }}
            QLabel {{
                color: white;
                font-size: {entry_font_size}px;
                padding: 8px;
            }}
        """)
        
        # If data was provided directly, use it to refresh entries
        if data and self.entry_widgets:
            self.update_entries(data)

    # Add methods for moving the leaderboard vertically
    def move_up(self):
        """Move the leaderboard up"""
        self.vertical_offset -= 50
        self.config['vertical_offset'] = self.vertical_offset  # Save to config
        
        # Preserve X position when moving vertically
        if self.is_fullscreen and hasattr(self, 'leaderboard_panel'):
            current_x = self.leaderboard_panel.x()
            screen = QApplication.primaryScreen().geometry()
            panel_height = self.leaderboard_panel.height()
            centered_y = (screen.height() - panel_height) // 2 + self.vertical_offset
            
            # Keep the panel within the screen bounds
            if centered_y < 0:
                centered_y = 0
            elif centered_y + panel_height > screen.height():
                centered_y = screen.height() - panel_height
            
            self.leaderboard_panel.move(current_x, centered_y)
        else:
            self.center_leaderboard_with_offset()
        
        # Save config file to make the position persistent
        with open('config.json', 'w') as f:
            json.dump(self.config, f)
    
    def move_down(self):
        """Move the leaderboard down"""
        self.vertical_offset += 50
        self.config['vertical_offset'] = self.vertical_offset  # Save to config
        
        # Preserve X position when moving vertically
        if self.is_fullscreen and hasattr(self, 'leaderboard_panel'):
            current_x = self.leaderboard_panel.x()
            screen = QApplication.primaryScreen().geometry()
            panel_height = self.leaderboard_panel.height()
            centered_y = (screen.height() - panel_height) // 2 + self.vertical_offset
            
            # Keep the panel within the screen bounds
            if centered_y < 0:
                centered_y = 0
            elif centered_y + panel_height > screen.height():
                centered_y = screen.height() - panel_height
            
            self.leaderboard_panel.move(current_x, centered_y)
        else:
            self.center_leaderboard_with_offset()
        
        # Save config file to make the position persistent
        with open('config.json', 'w') as f:
            json.dump(self.config, f)

class LapTimeHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            lap_data = json.loads(post_data.decode('utf-8'))
            
            # Print received data for debugging
            print(f"Received data: {lap_data}")
            
            # Validate required fields
            required_fields = ['simulator_id', 'driver_name', 'lap_time']
            if not all(field in lap_data for field in required_fields):
                print(f"Missing required fields. Required: {required_fields}, Received: {lap_data.keys()}")
                self.send_response(400)
                self.end_headers()
                return
            
            # Ensure lap_time is a float
            try:
                lap_time = float(lap_data['lap_time'])
                simulator_id = str(lap_data['simulator_id'])
                driver_name = str(lap_data['driver_name'])
                
                # Get email if present, otherwise empty string
                driver_email = str(lap_data.get('email', ''))
            except (ValueError, TypeError):
                print(f"Invalid data format: {lap_data}")
                self.send_response(400)
                self.end_headers()
                return
                
            # Create clean data entry
            clean_data = {
                'simulator_id': simulator_id,
                'driver_name': driver_name,
                'lap_time': lap_time,
                'email': driver_email,
                'timestamp': datetime.now().isoformat()
            }
            
            # Save to CSV
            csv_file = 'lap_times.csv'
            file_exists = os.path.exists(csv_file)
            
            with open(csv_file, 'a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=['simulator_id', 'driver_name', 'lap_time', 'email', 'timestamp'])
                if not file_exists:
                    writer.writeheader()
                writer.writerow(clean_data)
            
            print(f"Successfully wrote data: {clean_data}")
            self.send_response(200)
            self.end_headers()
            
        except Exception as e:
            print(f"Server error: {str(e)}")
            self.send_response(500)
            self.end_headers()
            
    def log_message(self, format, *args):
        # Suppress logging to keep the console clean
        pass

class ControlWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # Initialize config with default values
        local_ip = get_local_ip()
        self.config = {
            'server_url': f'http://{local_ip}:5000',
            'server_port': 5000,
            'refresh_interval': 5,
            'opacity': 220,
            'header_font_size': 24,
            'entry_font_size': 20,
            'p1_font_size': 24,    # First place font size
            'p2_font_size': 22,    # Second place font size
            'p3_font_size': 20,    # Third place font size
            'other_font_size': 18, # Other positions font size
            'background_image': '',
            'use_tall_aspect': False,
            'use_1344_aspect': False,
            'fill_screen': False,   # Whether to fill the entire screen with background
            'position_width': 80,    # Default width for position column
            'driver_width': 400,     # Default width for driver name column
            'time_width': 200,       # Default width for time column
            'panel_width': 800,      # Default width for entire panel
            'horizontal_offset': 0,   # Default horizontal offset
            'vertical_offset': 0,   # Default vertical offset
            'vertical_spacing': 4,   # Default vertical spacing between entries
            'row_height_padding': 16  # Default padding for row height
        }
        self.leaderboard_window = None
        self.network_thread = None
        self.ensure_csv_exists()
        self.load_config()  # This will override defaults if config file exists
        self.setup_ui()
        self.start_network_thread()
        self.start_lap_time_server()
        
        # Create leaderboard window but keep it hidden
        self.create_leaderboard_window()
        
    def ensure_csv_exists(self):
        """Create CSV file with headers if it doesn't exist"""
        csv_file = 'lap_times.csv'
        if not os.path.exists(csv_file):
            with open(csv_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['simulator_id', 'driver_name', 'lap_time', 'email', 'timestamp'])
    
    def load_config(self):
        """Load configuration from file, keeping defaults if file doesn't exist"""
        if os.path.exists('config.json'):
            try:
                with open('config.json', 'r') as f:
                    loaded_config = json.load(f)
                    # Update config with loaded values, keeping defaults for missing keys
                    self.config.update(loaded_config)
                    
                # Always update server_url with current device's IP
                local_ip = get_local_ip()
                self.config['server_url'] = f'http://{local_ip}:5000'
            except:
                pass  # Keep defaults if loading fails
    
    def setup_ui(self):
        self.setWindowTitle("Leaderboard Control")
        self.setGeometry(100, 100, 450, 600)
        self.setFixedSize(450, 600)
        
        # Set the application style
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f0f0f0;
            }
            QLabel {
                font-size: 14px;
                color: #333333;
            }
            QLineEdit, QComboBox {
                padding: 8px;
                border: 1px solid #cccccc;
                border-radius: 4px;
                background-color: white;
            }
            QPushButton {
                padding: 10px 20px;
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QTabWidget::pane {
                border: 1px solid #cccccc;
                background-color: white;
                border-radius: 4px;
            }
            QTabBar::tab {
                background-color: #e0e0e0;
                padding: 10px 20px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }
            QTabBar::tab:selected {
                background-color: white;
            }
        """)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setSpacing(20)
        layout.setContentsMargins(20, 20, 20, 20)
        
        self.show_leaderboard_btn = QPushButton("Show Leaderboard")
        self.show_leaderboard_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                font-size: 16px;
                padding: 12px;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
        """)
        self.show_leaderboard_btn.clicked.connect(self.toggle_leaderboard)
        layout.addWidget(self.show_leaderboard_btn)
        
        tabs = QTabWidget()
        layout.addWidget(tabs)
        
        # Settings tab with scroll area
        settings_tab = QWidget()
        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setWidget(settings_tab)
        settings_layout = QGridLayout(settings_tab)
        settings_layout.setSpacing(15)
        
        row = 0
        # Server URL at the top (read-only)
        url_label = QLabel("Server URL:")
        url_label.setStyleSheet("font-weight: bold;")
        settings_layout.addWidget(url_label, row, 0)
        self.server_url_entry = QLineEdit(self.config.get('server_url', 'http://localhost:5000'))
        self.server_url_entry.setReadOnly(True)
        self.server_url_entry.setStyleSheet("""
            QLineEdit {
                background-color: #f5f5f5;
                color: #666666;
            }
        """)
        settings_layout.addWidget(self.server_url_entry, row, 1)
        
        row += 1
        self.server_status_label = QLabel("Server Status: Not Running")
        self.server_status_label.setStyleSheet("color: #666666;")
        settings_layout.addWidget(self.server_status_label, row, 0, 1, 2)
        
        row += 1
        self.server_toggle_btn = QPushButton("Start Server")
        self.server_toggle_btn.clicked.connect(self.toggle_server)
        settings_layout.addWidget(self.server_toggle_btn, row, 0, 1, 2)
        
        row += 1
        settings_layout.addWidget(QLabel("Server Port:"), row, 0)
        self.server_port_entry = QLineEdit(str(self.config.get('server_port', 5000)))
        self.server_port_entry.setMaxLength(5)
        settings_layout.addWidget(self.server_port_entry, row, 1)
        
        row += 1
        server_info = QLabel("The server receives lap times from racing simulators.\nIt must be running to record new lap times.")
        server_info.setStyleSheet("color: #666666; font-style: italic;")
        server_info.setWordWrap(True)
        settings_layout.addWidget(server_info, row, 0, 1, 2)
        
        # Add a separator
        row += 1
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        separator.setStyleSheet("background-color: #cccccc;")
        settings_layout.addWidget(separator, row, 0, 1, 2)
        
        row += 1
        settings_layout.addWidget(QLabel("Background Image:"), row, 0)
        bg_widget = QWidget()
        bg_layout = QHBoxLayout(bg_widget)
        bg_layout.setSpacing(10)
        bg_layout.setContentsMargins(0, 0, 0, 0)
        
        self.bg_path_entry = QLineEdit(self.config.get('background_image', ''))
        self.bg_path_entry.setReadOnly(True)
        bg_layout.addWidget(self.bg_path_entry)
        browse_btn = QPushButton("Browse")
        browse_btn.setStyleSheet("""
            QPushButton {
                background-color: #757575;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #616161;
            }
        """)
        browse_btn.clicked.connect(self.choose_background)
        browse_btn.setMaximumWidth(100)
        bg_layout.addWidget(browse_btn)
        settings_layout.addWidget(bg_widget, row, 1)

        # Add aspect ratio toggle
        row += 1
        settings_layout.addWidget(QLabel("Background Aspect:"), row, 0)
        aspect_widget = QWidget()
        aspect_layout = QHBoxLayout(aspect_widget)
        aspect_layout.setSpacing(10)
        aspect_layout.setContentsMargins(0, 0, 0, 0)
        
        self.aspect_toggle = QPushButton(self.config.get('use_tall_aspect', False) and "1920x1536" or "1920x1080")
        self.aspect_toggle.setStyleSheet("""
            QPushButton {
                background-color: #757575;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #616161;
            }
        """)
        self.aspect_toggle.clicked.connect(self.toggle_aspect_ratio)
        aspect_layout.addWidget(self.aspect_toggle)
        settings_layout.addWidget(aspect_widget, row, 1)

        # Add fill screen toggle
        row += 1
        settings_layout.addWidget(QLabel("Background Fill Mode:"), row, 0)
        fill_widget = QWidget()
        fill_layout = QHBoxLayout(fill_widget)
        fill_layout.setSpacing(10)
        fill_layout.setContentsMargins(0, 0, 0, 0)
        
        self.fill_toggle = QPushButton(self.config.get('fill_screen', False) and "Fill Screen" or "Keep Aspect Ratio")
        self.fill_toggle.setStyleSheet("""
            QPushButton {
                background-color: #757575;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #616161;
            }
        """)
        self.fill_toggle.clicked.connect(self.toggle_fill_mode)
        fill_layout.addWidget(self.fill_toggle)
        settings_layout.addWidget(fill_widget, row, 1)

        row += 1
        settings_layout.addWidget(QLabel("Refresh Interval:"), row, 0)
        self.refresh_interval = QLineEdit(str(self.config.get('refresh_interval', 5)))
        self.refresh_interval.setMaxLength(3)
        settings_layout.addWidget(self.refresh_interval, row, 1)
        
        row += 1
        settings_layout.addWidget(QLabel("Box Opacity (0-255):"), row, 0)
        self.opacity_entry = QLineEdit(str(self.config.get('opacity', 220)))
        self.opacity_entry.setMaxLength(3)
        settings_layout.addWidget(self.opacity_entry, row, 1)
        
        row += 1
        settings_layout.addWidget(QLabel("Header Font Size:"), row, 0)
        self.header_font_size = QLineEdit(str(self.config.get('header_font_size', 24)))
        self.header_font_size.setMaxLength(2)
        settings_layout.addWidget(self.header_font_size, row, 1)
        
        row += 1
        settings_layout.addWidget(QLabel("Entry Font Size:"), row, 0)
        self.entry_font_size = QLineEdit(str(self.config.get('entry_font_size', 20)))
        self.entry_font_size.setMaxLength(2)
        settings_layout.addWidget(self.entry_font_size, row, 1)
        
        # Add position-specific font size settings
        row += 1
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        separator.setStyleSheet("background-color: #cccccc;")
        settings_layout.addWidget(separator, row, 0, 1, 2)
        
        row += 1
        settings_layout.addWidget(QLabel("1st Place Font Size:"), row, 0)
        self.p1_font_size = QLineEdit(str(self.config.get('p1_font_size', 24)))
        self.p1_font_size.setMaxLength(2)
        settings_layout.addWidget(self.p1_font_size, row, 1)
        
        row += 1
        settings_layout.addWidget(QLabel("2nd Place Font Size:"), row, 0)
        self.p2_font_size = QLineEdit(str(self.config.get('p2_font_size', 22)))
        self.p2_font_size.setMaxLength(2)
        settings_layout.addWidget(self.p2_font_size, row, 1)
        
        row += 1
        settings_layout.addWidget(QLabel("3rd Place Font Size:"), row, 0)
        self.p3_font_size = QLineEdit(str(self.config.get('p3_font_size', 20)))
        self.p3_font_size.setMaxLength(2)
        settings_layout.addWidget(self.p3_font_size, row, 1)
        
        row += 1
        settings_layout.addWidget(QLabel("Other Positions Font Size:"), row, 0)
        self.other_font_size = QLineEdit(str(self.config.get('other_font_size', 18)))
        self.other_font_size.setMaxLength(2)
        settings_layout.addWidget(self.other_font_size, row, 1)
        
        # Add a separator for column width settings
        row += 1
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        separator.setStyleSheet("background-color: #cccccc;")
        settings_layout.addWidget(separator, row, 0, 1, 2)
        
        # Column width settings
        row += 1
        settings_layout.addWidget(QLabel("Position Column Width:"), row, 0)
        self.position_width = QLineEdit(str(self.config.get('position_width', 80)))
        self.position_width.setMaxLength(3)
        settings_layout.addWidget(self.position_width, row, 1)
        
        row += 1
        settings_layout.addWidget(QLabel("Driver Column Width:"), row, 0)
        self.driver_width = QLineEdit(str(self.config.get('driver_width', 400)))
        self.driver_width.setMaxLength(3)
        settings_layout.addWidget(self.driver_width, row, 1)
        
        row += 1
        settings_layout.addWidget(QLabel("Time Column Width:"), row, 0)
        self.time_width = QLineEdit(str(self.config.get('time_width', 200)))
        self.time_width.setMaxLength(3)
        settings_layout.addWidget(self.time_width, row, 1)
        
        # Add panel width setting after column width settings
        row += 1
        settings_layout.addWidget(QLabel("Overall Panel Width:"), row, 0)
        self.panel_width = QLineEdit(str(self.config.get('panel_width', 800)))
        self.panel_width.setMaxLength(4)
        settings_layout.addWidget(self.panel_width, row, 1)
        
        # Add vertical spacing settings with tooltip
        row += 1
        spacing_label = QLabel("Vertical Spacing:")
        spacing_label.setToolTip("Controls the gap between each position row")
        settings_layout.addWidget(spacing_label, row, 0)
        self.vertical_spacing = QLineEdit(str(self.config.get('vertical_spacing', 4)))
        self.vertical_spacing.setMaxLength(2)
        self.vertical_spacing.setToolTip("Controls the gap between each position row")
        settings_layout.addWidget(self.vertical_spacing, row, 1)
        
        # Add row height padding setting with tooltip
        row += 1
        padding_label = QLabel("Row Height Padding:")
        padding_label.setToolTip("Controls the internal height of each position row")
        settings_layout.addWidget(padding_label, row, 0)
        self.row_height_padding = QLineEdit(str(self.config.get('row_height_padding', 16)))
        self.row_height_padding.setMaxLength(2)
        self.row_height_padding.setToolTip("Controls the internal height of each position row")
        settings_layout.addWidget(self.row_height_padding, row, 1)
        
        row += 1
        save_btn = QPushButton("Save Settings")
        save_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                font-size: 14px;
                padding: 10px;
                margin-top: 10px;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
        """)
        save_btn.clicked.connect(self.save_settings)
        settings_layout.addWidget(save_btn, row, 0, 1, 2)
        
        settings_layout.setRowStretch(row + 1, 1)
        tabs.addTab(settings_scroll, "Settings")
    
    def create_leaderboard_window(self):
        if self.leaderboard_window is None:
            # Create as a separate window without parent
            self.leaderboard_window = LeaderboardWindow(self.config, parent=None)
            if self.config.get('background_image'):
                self.leaderboard_window.set_background(self.config['background_image'])
    
    def choose_background(self):
        """Choose background image efficiently"""
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Select Background Image",
            "",
            "Image Files (*.png *.jpg *.jpeg *.bmp)"
        )
        if file_name:
            self.bg_path_entry.setText(file_name)
            self.save_settings()
    
    def toggle_aspect_ratio(self):
        """Toggle between 16:9, tall, and 1920x1344 aspect ratios"""
        current_text = self.aspect_toggle.text()
        if current_text == "1920x1080":
            self.aspect_toggle.setText("1920x1536")
            self.config['use_tall_aspect'] = True
            self.config['use_1344_aspect'] = False
        elif current_text == "1920x1536":
            self.aspect_toggle.setText("1920x1344")
            self.config['use_tall_aspect'] = False
            self.config['use_1344_aspect'] = True
        else:
            self.aspect_toggle.setText("1920x1080")
            self.config['use_tall_aspect'] = False
            self.config['use_1344_aspect'] = False
        
        # Update the background if it exists
        if self.leaderboard_window and self.config.get('background_image'):
            self.leaderboard_window.set_background(self.config['background_image'])
    
    def toggle_fill_mode(self):
        """Toggle between fill screen and keep aspect ratio"""
        self.config['fill_screen'] = not self.config['fill_screen']
        self.fill_toggle.setText(self.config['fill_screen'] and "Fill Screen" or "Keep Aspect Ratio")
        
        # Update the background if the leaderboard is visible
        if self.leaderboard_window and self.config.get('background_image'):
            self.leaderboard_window.set_background(self.config['background_image'])
    
    def save_settings(self):
        """Save settings to config file"""
        # Always get current IP address
        local_ip = get_local_ip()
        
        # Get the new font size values for debugging
        new_p1_size = int(self.p1_font_size.text())
        new_p2_size = int(self.p2_font_size.text())
        new_p3_size = int(self.p3_font_size.text())
        new_other_size = int(self.other_font_size.text())
        
        # Log the font size changes
        logger.warning(f"Changing font sizes: p1={new_p1_size}, p2={new_p2_size}, p3={new_p3_size}, other={new_other_size}")
        
        # Update config
        self.config.update({
            'server_url': f'http://{local_ip}:5000',  # Always use current IP
            'background_image': self.bg_path_entry.text(),
            'refresh_interval': int(self.refresh_interval.text()),
            'opacity': int(self.opacity_entry.text()),
            'header_font_size': int(self.header_font_size.text()),
            'entry_font_size': int(self.entry_font_size.text()),
            'p1_font_size': new_p1_size,
            'p2_font_size': new_p2_size,
            'p3_font_size': new_p3_size,
            'other_font_size': new_other_size,
            'server_port': int(self.server_port_entry.text()),
            'use_tall_aspect': self.aspect_toggle.text() == "1920x1536",
            'use_1344_aspect': self.aspect_toggle.text() == "1920x1344",
            'fill_screen': self.fill_toggle.text() == "Fill Screen",
            'position_width': int(self.position_width.text()),
            'driver_width': int(self.driver_width.text()),
            'time_width': int(self.time_width.text()),
            'panel_width': int(self.panel_width.text()),
            'horizontal_offset': self.leaderboard_window.horizontal_offset if self.leaderboard_window else 0,
            'vertical_offset': self.leaderboard_window.vertical_offset if self.leaderboard_window else 0,
            'vertical_spacing': int(self.vertical_spacing.text()),
            'row_height_padding': int(self.row_height_padding.text())
        })
        
        with open('config.json', 'w') as f:
            json.dump(self.config, f)
            
        if self.leaderboard_window:
            # Update the config in the leaderboard window first
            self.leaderboard_window.config = self.config.copy()  # Make a deep copy to ensure it's passed correctly
            
            # Log the leaderboard's config to ensure it has the correct values
            logger.warning(f"Leaderboard config after update: p1={self.leaderboard_window.config.get('p1_font_size')}, p2={self.leaderboard_window.config.get('p2_font_size')}, p3={self.leaderboard_window.config.get('p3_font_size')}, other={self.leaderboard_window.config.get('other_font_size')}")
            
            # Update visual elements in order
            self.leaderboard_window.update_column_widths()  # First update column widths
            
            # Use current data to force refresh with new font sizes
            if self.network_thread and hasattr(self.network_thread, '_last_data'):
                current_data = self.network_thread._last_data
                if current_data:
                    # Force immediate update of all entries with new font settings
                    self.leaderboard_window.update_entries(current_data)
                    # Also directly call update_font_sizes with this data to ensure it's used
                    if hasattr(self.leaderboard_window, 'update_font_sizes'):
                        self.leaderboard_window.update_font_sizes(current_data)
            
            if self.config['background_image']:
                self.leaderboard_window.set_background(self.config['background_image'])
        
        # Update the server URL display in the UI
        self.server_url_entry.setText(self.config['server_url'])
        
        self.statusBar().showMessage("Settings saved successfully")
    
    def start_network_thread(self):
        if self.network_thread:
            self.network_thread.stop()
        self.network_thread = NetworkThread(self.config)
        self.network_thread.leaderboard_updated.connect(self.update_leaderboard)
        self.network_thread.server_status_updated.connect(self.statusBar().showMessage)
        self.network_thread.start()
        
    def toggle_leaderboard(self):
        if self.leaderboard_window:
            if self.leaderboard_window.isVisible():
                self.leaderboard_window.hide()
                self.show_leaderboard_btn.setText("Show Leaderboard")
            else:
                self.leaderboard_window.show()
                self.leaderboard_window.activateWindow()
                self.leaderboard_window.raise_()
                self.show_leaderboard_btn.setText("Hide Leaderboard")
            
    def update_leaderboard(self, data):
        if self.leaderboard_window:
            self.leaderboard_window.update_entries(data)
            
            # Ensure background stays correct after update
            if self.config.get('background_image'):
                # Use a small delay to allow layout to stabilize
                QTimer.singleShot(50, lambda: self.leaderboard_window.set_background(self.config['background_image']))
    
    def toggle_server(self):
        if hasattr(self, 'http_server'):
            # Stop the server
            self.http_server.shutdown()
            self.server_thread.join()
            delattr(self, 'http_server')
            self.server_status_label.setText("Server Status: Stopped")
            self.server_toggle_btn.setText("Start Server")
            self.statusBar().showMessage("Lap time server stopped")
        else:
            # Start the server
            try:
                port = int(self.server_port_entry.text())
                server_address = ('', port)
                self.http_server = HTTPServer(server_address, LapTimeHandler)
                self.server_thread = threading.Thread(target=self.http_server.serve_forever)
                self.server_thread.daemon = True
                self.server_thread.start()
                
                # Update server URL with new port
                local_ip = get_local_ip()
                self.config['server_url'] = f'http://{local_ip}:{port}'
                self.config['server_port'] = port  # Save port to config
                self.server_url_entry.setText(self.config['server_url'])
                
                # Save config to persist port change
                with open('config.json', 'w') as f:
                    json.dump(self.config, f)
                
                self.server_status_label.setText(f"Server Status: Running on port {port}")
                self.server_toggle_btn.setText("Stop Server")
                self.statusBar().showMessage(f"Lap time server started on port {port}")
            except Exception as e:
                self.server_status_label.setText(f"Server Status: Error - {str(e)}")
                self.statusBar().showMessage(f"Error starting lap time server: {str(e)}")

    def start_lap_time_server(self):
        """Start HTTP server to receive lap times"""
        try:
            port = int(self.config.get('server_port', 5000))
            # Update port input field
            self.server_port_entry.setText(str(port))
            
            server_address = ('', port)
            self.http_server = HTTPServer(server_address, LapTimeHandler)
            self.server_thread = threading.Thread(target=self.http_server.serve_forever)
            self.server_thread.daemon = True
            self.server_thread.start()
            
            # Update server URL with initial port
            local_ip = get_local_ip()
            self.config['server_url'] = f'http://{local_ip}:{port}'
            self.server_url_entry.setText(self.config['server_url'])
            
            self.server_status_label.setText(f"Server Status: Running on port {port}")
            self.server_toggle_btn.setText("Stop Server")
            self.statusBar().showMessage(f"Lap time server started on port {port}")
        except Exception as e:
            self.server_status_label.setText(f"Server Status: Error - {str(e)}")
            self.statusBar().showMessage(f"Error starting lap time server: {str(e)}")
    
    def closeEvent(self, event):
        reply = QMessageBox.question(
            self, 'Quit',
            "Do you want to quit?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            if self.network_thread:
                self.network_thread.stop()
            if hasattr(self, 'http_server'):
                self.http_server.shutdown()
            if self.leaderboard_window:
                self.leaderboard_window.close()
            event.accept()
        else:
            event.ignore()

def main():
    logger.debug("Starting application")
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # Use Fusion style for a modern look
    logger.debug("QApplication created")
    window = ControlWindow()
    logger.debug("ControlWindow created")
    window.show()
    logger.debug("ControlWindow shown")
    sys.exit(app.exec())

if __name__ == '__main__':
    main() 