import sys
import json
import os
import csv
import shutil
from datetime import datetime
import requests
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import socket
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                            QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                            QTabWidget, QFileDialog, QMessageBox, QGridLayout,
                            QFrame, QScrollArea, QSizePolicy)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QPixmap, QPalette, QColor, QFont, QImage, QCursor, QIcon

# Reduce logging to only warnings and errors
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# Track connected simulators globally
connected_simulators = {}  # {simulator_id: {'ip': ip, 'last_seen': timestamp, 'driver': name}}

# Queue system storage
queue_data = {
    "queue": [],  # [{id, name, phone, joined_at, assigned_to}]
    "session_history": [],  # Last 50 completed sessions [{simulator_ip, duration_seconds, ended_at}]
    "active_sessions": {},  # {simulator_ip: {started_at, driver_name}}
    "last_updated": 0
}
QUEUE_FILE = "queue.json"
MAX_SESSION_HISTORY = 50

# SMS configuration (supports Textbelt or Twilio)
SMS_CONFIG_FILE = "sms_config.json"
sms_config = {
    "enabled": False,
    "provider": "textbelt",  # "textbelt" or "twilio"
    "textbelt_key": "",  # Your Textbelt API key
    "twilio_account_sid": "",
    "twilio_auth_token": "",
    "twilio_from_number": "",
    "message_template": "Hi {name}! It's your turn to race at {simulator}. Head over now!"
}

def load_sms_config():
    """Load SMS configuration from file"""
    global sms_config
    if os.path.exists(SMS_CONFIG_FILE):
        try:
            with open(SMS_CONFIG_FILE, 'r') as f:
                loaded = json.load(f)
                sms_config.update(loaded)

                # Check if properly configured
                if sms_config.get("provider") == "textbelt" and sms_config.get("textbelt_key"):
                    sms_config["enabled"] = True
                    print("[SMS] Textbelt notifications enabled")
                elif sms_config.get("provider") == "twilio" and sms_config.get("twilio_account_sid"):
                    sms_config["enabled"] = True
                    print("[SMS] Twilio notifications enabled")
                else:
                    print("[SMS] SMS disabled - missing credentials")
        except Exception as e:
            logger.warning(f"Error loading SMS config: {e}")
    else:
        # Create default config file
        save_sms_config()
        print(f"[SMS] Created config file: {SMS_CONFIG_FILE}")
        print("[SMS] Edit this file with your Textbelt API key to enable SMS")

def save_sms_config():
    """Save SMS configuration to file"""
    try:
        with open(SMS_CONFIG_FILE, 'w') as f:
            json.dump(sms_config, f, indent=2)
    except Exception as e:
        logger.warning(f"Error saving SMS config: {e}")

def send_sms_notification(phone_number, driver_name, simulator_name="your simulator"):
    """Send SMS notification via configured provider"""
    global sms_config

    if not sms_config.get("enabled"):
        print(f"[SMS] SMS disabled - skipping notification to {phone_number}")
        return False

    if not phone_number or len(phone_number) < 10:
        print(f"[SMS] Invalid phone number: {phone_number}")
        return False

    # Clean up phone number
    clean_phone = ''.join(filter(str.isdigit, phone_number))
    if len(clean_phone) == 10:
        clean_phone = "1" + clean_phone  # Add US country code

    # Format message
    message = sms_config.get("message_template", "It's your turn to race!").format(
        name=driver_name,
        simulator=simulator_name
    )

    provider = sms_config.get("provider", "textbelt")

    if provider == "textbelt":
        return send_sms_textbelt(clean_phone, message)
    elif provider == "twilio":
        return send_sms_twilio(clean_phone, message)
    else:
        print(f"[SMS] Unknown provider: {provider}")
        return False

def send_sms_textbelt(phone_number, message):
    """Send SMS via Textbelt API"""
    try:
        data = {
            "phone": phone_number,
            "message": message,
            "key": sms_config["textbelt_key"]
        }

        # Add sender name for regulatory compliance
        sender = sms_config.get("textbelt_sender", "")
        if sender:
            data["sender"] = sender

        response = requests.post(
            "https://textbelt.com/text",
            data=data,
            timeout=10
        )

        result = response.json()
        if result.get("success"):
            print(f"[SMS] Textbelt sent to {phone_number}: {message}")
            print(f"[SMS] Quota remaining: {result.get('quotaRemaining', 'unknown')}")
            return True
        else:
            print(f"[SMS] Textbelt failed: {result.get('error', 'Unknown error')}")
            return False

    except Exception as e:
        print(f"[SMS] Textbelt error: {e}")
        return False

def send_sms_twilio(phone_number, message):
    """Send SMS via Twilio API"""
    try:
        # Ensure phone has + prefix for Twilio
        if not phone_number.startswith("+"):
            phone_number = "+" + phone_number

        account_sid = sms_config["twilio_account_sid"]
        auth_token = sms_config["twilio_auth_token"]
        from_number = sms_config["twilio_from_number"]

        url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"

        response = requests.post(
            url,
            auth=(account_sid, auth_token),
            data={
                "To": phone_number,
                "From": from_number,
                "Body": message
            },
            timeout=10
        )

        if response.status_code in [200, 201]:
            print(f"[SMS] Twilio sent to {phone_number}: {message}")
            return True
        else:
            print(f"[SMS] Twilio failed: {response.status_code} - {response.text}")
            return False

    except Exception as e:
        print(f"[SMS] Twilio error: {e}")
        return False

def load_queue_data():
    """Load queue data from JSON file"""
    global queue_data
    if os.path.exists(QUEUE_FILE):
        try:
            with open(QUEUE_FILE, 'r') as f:
                loaded = json.load(f)
                queue_data.update(loaded)
                # Clean up stale entries (>12 hours old)
                current_time = int(datetime.now().timestamp())
                twelve_hours = 12 * 60 * 60
                queue_data["queue"] = [
                    entry for entry in queue_data.get("queue", [])
                    if current_time - entry.get("joined_at", 0) < twelve_hours
                ]
        except Exception as e:
            logger.warning(f"Error loading queue data: {e}")
    return queue_data

def save_queue_data():
    """Save queue data to JSON file"""
    global queue_data
    queue_data["last_updated"] = int(datetime.now().timestamp())
    try:
        with open(QUEUE_FILE, 'w') as f:
            json.dump(queue_data, f, indent=2)
    except Exception as e:
        logger.warning(f"Error saving queue data: {e}")

def generate_queue_id():
    """Generate a short unique ID for queue entries"""
    import random
    import string
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))

def calculate_wait_time(queue_position):
    """Calculate estimated wait time for a queue position"""
    global queue_data
    session_history = queue_data.get("session_history", [])
    active_sessions = queue_data.get("active_sessions", {})

    # Need at least 3 sessions for estimation
    if len(session_history) < 3:
        return None

    # Get number of active simulators (simulators that have had sessions)
    all_sim_ips = set()
    for session in session_history:
        all_sim_ips.add(session.get("simulator_ip", ""))
    for ip in active_sessions.keys():
        all_sim_ips.add(ip)
    num_simulators = max(1, len(all_sim_ips))

    # Rolling average of last 20 sessions (or all if fewer)
    recent = session_history[-20:] if len(session_history) > 20 else session_history
    avg_duration = sum(s.get("duration_seconds", 300) for s in recent) / len(recent)

    # How many people ahead in queue
    people_ahead = queue_position - 1

    # Estimate: each "round" serves num_simulators people
    rounds_to_wait = people_ahead / num_simulators if num_simulators > 0 else people_ahead

    # Base wait time
    wait_seconds = rounds_to_wait * avg_duration

    # Subtract average time already elapsed in active sessions
    if active_sessions:
        current_time = int(datetime.now().timestamp())
        total_elapsed = 0
        for session in active_sessions.values():
            started_at = session.get("started_at", current_time)
            total_elapsed += current_time - started_at
        avg_elapsed = total_elapsed / len(active_sessions)
        wait_seconds -= avg_elapsed / num_simulators

    return max(0, int(wait_seconds))

def format_wait_time(seconds):
    """Format wait time for display"""
    if seconds is None:
        return "Calculating..."

    if seconds < 300:  # < 5 min
        return "Less than 5 minutes"
    elif seconds < 3600:  # < 1 hour
        minutes = round(seconds / 300) * 5  # Round to nearest 5 min
        return f"~{minutes} minutes"
    else:
        hours = seconds // 3600
        remaining_minutes = round((seconds % 3600) / 600) * 10  # Round to nearest 10 min
        if remaining_minutes == 0:
            return f"~{hours} hour{'s' if hours > 1 else ''}"
        return f"~{hours} hour{'s' if hours > 1 else ''} {remaining_minutes} minutes"

def get_queue_with_estimates():
    """Get queue entries with wait time estimates"""
    global queue_data
    result = []
    for i, entry in enumerate(queue_data.get("queue", [])):
        position = i + 1
        wait_seconds = calculate_wait_time(position)
        result.append({
            **entry,
            "position": position,
            "wait_seconds": wait_seconds,
            "wait_display": format_wait_time(wait_seconds)
        })
    return result

# Discovery protocol constants
DISCOVERY_PORT = 5001  # UDP port for discovery broadcasts
DISCOVERY_REQUEST = b"LEADERBOARD_DISCOVER"
DISCOVERY_RESPONSE_PREFIX = "LEADERBOARD_SERVER:"


class DiscoveryResponder(threading.Thread):
    """UDP Discovery Responder - allows simulators to find this receiver automatically.

    Protocol:
    - Listens for UDP broadcasts on DISCOVERY_PORT (5001)
    - When it receives DISCOVERY_REQUEST ("LEADERBOARD_DISCOVER")
    - Responds with "LEADERBOARD_SERVER:{ip}:{port}" to the sender

    This allows simulators to find the leaderboard server without manual IP entry.
    Works even when IPs change - as long as devices are on the same network.
    """

    def __init__(self, http_port, status_callback=None):
        super().__init__(daemon=True)
        self.http_port = http_port
        self.status_callback = status_callback
        self.running = False
        self.socket = None

    def run(self):
        self.running = True
        try:
            # Create UDP socket
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            # Try to set SO_REUSEPORT if available (Linux/Mac)
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except AttributeError:
                pass  # Windows doesn't have SO_REUSEPORT

            # Bind to discovery port on all interfaces
            self.socket.bind(('', DISCOVERY_PORT))
            self.socket.settimeout(1.0)  # 1 second timeout for clean shutdown

            logger.warning(f"Discovery responder started on UDP port {DISCOVERY_PORT}")
            if self.status_callback:
                self.status_callback(f"Discovery active on port {DISCOVERY_PORT}")

            while self.running:
                try:
                    data, addr = self.socket.recvfrom(1024)

                    if data == DISCOVERY_REQUEST:
                        # Get current IP and respond
                        local_ip = get_local_ip()
                        response = f"{DISCOVERY_RESPONSE_PREFIX}{local_ip}:{self.http_port}"
                        self.socket.sendto(response.encode(), addr)
                        logger.warning(f"Discovery: Responded to {addr[0]} with {response}")

                except socket.timeout:
                    continue  # Normal timeout, just loop again
                except Exception as e:
                    if self.running:  # Only log if we're still supposed to be running
                        logger.warning(f"Discovery responder error: {e}")

        except Exception as e:
            logger.warning(f"Failed to start discovery responder: {e}")
            if self.status_callback:
                self.status_callback(f"Discovery failed: {e}")
        finally:
            if self.socket:
                self.socket.close()

    def stop(self):
        self.running = False
        if self.socket:
            try:
                self.socket.close()
            except:
                pass

    def update_port(self, new_port):
        """Update the HTTP port that we advertise"""
        self.http_port = new_port

# Reusable stylesheet constants
CONTROL_BUTTON_STYLE = """
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
"""

CLOSE_BUTTON_STYLE = """
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
"""

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
        
        # Load offsets based on current orientation
        self._load_offsets_for_orientation()
        
        self.vertical_spacing = self.config.get('vertical_spacing', 4)  # Get spacing between entries
        self.row_height_padding = self.config.get('row_height_padding', 16)  # Get internal row height padding
        self.setup_ui()
        self.hide()
    
    def _load_offsets_for_orientation(self):
        """Load the correct offsets based on current orientation"""
        is_vertical = self.config.get('orientation', 'horizontal') == 'vertical'
        if is_vertical:
            self.horizontal_offset = self.config.get('horizontal_offset_v', 0)
            self.vertical_offset = self.config.get('vertical_offset_v', 0)
        else:
            self.horizontal_offset = self.config.get('horizontal_offset_h', 0)
            self.vertical_offset = self.config.get('vertical_offset_h', 0)
    
    def _save_offsets_for_orientation(self):
        """Save offsets to the correct config keys based on current orientation"""
        is_vertical = self.config.get('orientation', 'horizontal') == 'vertical'
        if is_vertical:
            self.config['horizontal_offset_v'] = self.horizontal_offset
            self.config['vertical_offset_v'] = self.vertical_offset
        else:
            self.config['horizontal_offset_h'] = self.horizontal_offset
            self.config['vertical_offset_h'] = self.vertical_offset
        
        # Also update legacy keys for backward compatibility
        self.config['horizontal_offset'] = self.horizontal_offset
        self.config['vertical_offset'] = self.vertical_offset
        
    def setup_ui(self):
        # Set window flags for both modes
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Create a title bar with minimize and close buttons
        self.title_bar = QWidget(self)
        title_bar_layout = QHBoxLayout(self.title_bar)
        title_bar_layout.setContentsMargins(10, 5, 10, 5)

        # Minimize button
        self.minimize_button = QPushButton("—", self)
        self.minimize_button.setStyleSheet(CONTROL_BUTTON_STYLE)
        self.minimize_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.minimize_button.clicked.connect(self.showMinimized)
        self.minimize_button.setFixedSize(40, 40)

        # Close button
        self.close_button = QPushButton("✕", self)
        self.close_button.setStyleSheet(CLOSE_BUTTON_STYLE)
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.clicked.connect(self.close)
        self.close_button.setFixedSize(40, 40)

        title_bar_layout.addStretch()
        title_bar_layout.addWidget(self.minimize_button)
        title_bar_layout.addWidget(self.close_button)

        self.title_bar.setFixedHeight(50)
        layout.addWidget(self.title_bar)
        
        # Rest of the UI setup remains the same
        self.leaderboard_panel = QWidget(self)
        # Set panel size based on orientation
        is_vertical = self.config.get('orientation', 'horizontal') == 'vertical'
        if is_vertical:
            # 11 rows x 123px = 1353px
            self.leaderboard_panel.setFixedSize(864, 1353)
        else:
            self.leaderboard_panel.setFixedWidth(self.config.get('panel_width', 800))
        self.leaderboard_panel.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        panel_layout = QVBoxLayout(self.leaderboard_panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)
        
        headers = ['Position', 'Driver', 'Time']
        self.header_widget = QWidget()
        self.header_widget.setStyleSheet("""
            QWidget {
                background-color: rgba(50, 50, 50, 220);
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
            }
            QLabel {
                background-color: transparent;
                color: white;
                font-size: 22px;
                font-weight: bold;
                padding: 10px 0px;
            }
        """)

        # Set fixed header height for vertical mode
        is_vertical = self.config.get('orientation', 'horizontal') == 'vertical'
        if is_vertical:
            self.header_widget.setFixedHeight(self.config.get('vertical_row_height', 123))

        header_layout = QHBoxLayout(self.header_widget)
        header_layout.setContentsMargins(30, 10, 30, 4)  # Equal margins left and right
        header_layout.setSpacing(20)  # Reduced spacing between columns
        header_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Balanced widths: Position and Time equal, Driver gets the rest
        widths = [100, 484, 180]  # Position, Driver, Time
        for header, width in zip(headers, widths):
            label = QLabel(header)
            label.setFixedWidth(width)
            label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
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
        self.entries_layout.setContentsMargins(20, 6, 20, 6)  # Match header outer margins
        self.entries_layout.setSpacing(self.vertical_spacing)  # Use configured vertical spacing
        panel_layout.addWidget(self.entries_widget)
        
        layout.addWidget(self.leaderboard_panel)
        
        # Set initial window mode
        self.set_window_mode(True)
    
    def set_window_mode(self, fullscreen):
        self.is_fullscreen = fullscreen
        if fullscreen:
            # Remove borders in fullscreen mode
            self.header_widget.setStyleSheet("""
                QWidget {
                    background-color: rgba(50, 50, 50, 220);
                }
                QLabel {
                    background-color: transparent;
                    color: white;
                    font-size: 22px;
                    font-weight: bold;
                    padding: 10px 0px;
                }
            """)
            self.entries_widget.setStyleSheet("""
                QWidget {
                    background-color: rgba(40, 40, 40, 220);
                }
                QLabel {
                    color: white;
                    font-size: 18px;
                    padding: 8px 0px;
                }
            """)

            # Show fullscreen
            self.showFullScreen()

            # Get actual screen dimensions
            screen = QApplication.primaryScreen().geometry()

            # Remove widgets from layout for absolute positioning
            self.layout().removeWidget(self.title_bar)
            self.layout().removeWidget(self.leaderboard_panel)

            # Position title bar at very top right corner
            self.title_bar.setParent(self)
            self.title_bar.setFixedSize(110, 50)  # Fixed size for the two buttons
            self.title_bar.move(screen.width() - 115, 5)
            self.title_bar.raise_()
            self.title_bar.show()

            # Position the leaderboard panel in the center
            self.leaderboard_panel.setParent(self)
            self.center_leaderboard_with_offset()
            self.leaderboard_panel.raise_()
            
            # Explicitly refresh background when going into fullscreen
            if hasattr(self, 'background_label') and self.config.get('background_image'):
                QTimer.singleShot(50, lambda: self.set_background(self.config['background_image']))
        else:
            # Regular windowed mode - add widgets back to layout
            layout = self.layout()
            # Only add if not already in layout
            if layout.indexOf(self.title_bar) == -1:
                layout.insertWidget(0, self.title_bar)
            if layout.indexOf(self.leaderboard_panel) == -1:
                layout.addWidget(self.leaderboard_panel)

            self.header_widget.setStyleSheet("""
                QWidget {
                    background-color: rgba(50, 50, 50, 220);
                    border-top-left-radius: 10px;
                    border-top-right-radius: 10px;
                }
                QLabel {
                    background-color: transparent;
                    color: white;
                    font-size: 22px;
                    font-weight: bold;
                    padding: 10px 0px;
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
                    padding: 8px 0px;
                }
            """)
            self.showNormal()
            self.resize(self.leaderboard_panel.width() + 40, 800)  # Add padding
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
                    
                    # Check if vertical orientation is selected
                    is_vertical = self.config.get('orientation', 'horizontal') == 'vertical'
                    
                    if self.config.get('fill_screen', False):
                        # Fill entire screen, ignore aspect ratio
                        scaled_image = image.scaled(
                            target_width,
                            target_height,
                            Qt.AspectRatioMode.IgnoreAspectRatio,
                            Qt.TransformationMode.SmoothTransformation
                        )
                        x_offset = 0
                        y_offset = 0
                    else:
                        # Keep aspect ratio but crop to fill screen (no black bars)
                        image_aspect = image.width() / image.height()
                        screen_aspect = target_width / target_height
                        
                        if image_aspect > screen_aspect:
                            # Image is wider - fit height and crop sides
                            scale_height = target_height
                            scale_width = int(target_height * image_aspect)
                        else:
                            # Image is taller - fit width and crop top/bottom
                            scale_width = target_width
                            scale_height = int(target_width / image_aspect)
                        
                        scaled_image = image.scaled(
                            scale_width,
                            scale_height,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation
                        )
                        
                        # Center the oversized image (this creates the crop effect)
                        x_offset = (target_width - scaled_image.width()) // 2
                        y_offset = (target_height - scaled_image.height()) // 2
                    
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
                    
                    # Check if vertical orientation is selected
                    is_vertical = self.config.get('orientation', 'horizontal') == 'vertical'
                    
                    if is_vertical:
                        # Vertical orientation: 1080x1920 aspect ratio (portrait)
                        # In windowed mode, make height taller than width
                        target_width = window_size.width()
                        target_height = window_size.width() * (1920/1080)
                    else:
                        # Horizontal orientation: 1920x1080 aspect ratio
                        target_width = window_size.width()
                        target_height = window_size.width() * (1080/1920)
                    
                    scaled_image = image.scaled(
                        int(target_width),
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
        # Update row height padding and vertical spacing from config
        self.row_height_padding = self.config.get('row_height_padding', 16)
        self.vertical_spacing = self.config.get('vertical_spacing', 4)
        
        # Check orientation for row height
        is_vertical = self.config.get('orientation', 'horizontal') == 'vertical'
        
        if is_vertical:
            # Use fixed spacing of 0 for vertical mode (heights are exact)
            self.entries_layout.setSpacing(0)
            # Update header height for vertical mode
            self.header_widget.setFixedHeight(self.config.get('vertical_row_height', 123))
            # Panel size: 864 width, 11 rows x 123px = 1353px
            self.leaderboard_panel.setFixedSize(864, 1353)
        else:
            self.entries_layout.setSpacing(self.vertical_spacing)
            # Remove fixed height constraint on header for horizontal mode
            self.header_widget.setMinimumHeight(0)
            self.header_widget.setMaximumHeight(16777215)  # Qt's QWIDGETSIZE_MAX
            self.leaderboard_panel.setFixedWidth(self.config.get('panel_width', 800))
        
        # Efficiently handle widget recycling
        if data:
            widths = [
                self.config.get('position_width', 80),
                self.config.get('driver_width', 400),
                self.config.get('time_width', 200)
            ]
            
            # Create additional widgets if we need more than we have
            while len(self.entry_widgets) < len(data):
                entry_widget = self._create_entry_widget(widths)
                self.entry_widgets.append(entry_widget)
                self.entries_layout.addWidget(entry_widget)
            
            # Hide excess widgets if we have more than we need
            for i in range(len(data), len(self.entry_widgets)):
                self.entry_widgets[i].hide()
            
            # Calculate proper row height based on orientation
            if is_vertical:
                row_height = self.config.get('vertical_row_height', 123)
            else:
                row_padding = self.config.get('row_height_padding', 16)
                max_font_size = max(
                    self.config.get('p1_font_size', 24),
                    self.config.get('p2_font_size', 22),
                    self.config.get('p3_font_size', 20),
                    self.config.get('other_font_size', 18)
                )
                vertical_padding = self.config.get('vertical_spacing', 4)
                row_height = max_font_size + row_padding + vertical_padding
            
            # Update the widgets we're using
            for i, entry in enumerate(data):
                # Update row height for each widget
                self.entry_widgets[i].setFixedHeight(row_height)
                self._update_entry_widget(self.entry_widgets[i], entry, i + 1)
                self.entry_widgets[i].show()
        else:
            # Hide all widgets if no data
            for widget in self.entry_widgets:
                widget.hide()
    
    def _create_entry_widget(self, widths):
        """Create a reusable entry widget"""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(10, 0, 10, 0)  # Match header margins
        layout.setSpacing(20)  # Match header spacing

        # Use balanced widths matching header: Position and Time equal, Driver gets the rest
        widths = [100, 484, 180]  # Position, Driver, Time

        # Add labels for position, driver name, and time
        position_label = QLabel()
        position_label.setFixedWidth(widths[0])
        position_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        driver_label = QLabel()
        driver_label.setFixedWidth(widths[1])
        driver_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        time_label = QLabel()
        time_label.setFixedWidth(widths[2])
        time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)  # Center the time
        
        # Add the labels to the layout
        layout.addWidget(position_label)
        layout.addWidget(driver_label)
        layout.addWidget(time_label)
        
        # Check if vertical mode - use fixed row height
        is_vertical = self.config.get('orientation', 'horizontal') == 'vertical'
        
        if is_vertical:
            # Use fixed row height for vertical mode (123px by default)
            row_height = self.config.get('vertical_row_height', 123)
        else:
            # Use dynamic row height based on font size for horizontal mode
            row_padding = self.config.get('row_height_padding', 16)
            max_font_size = max(
                self.config.get('p1_font_size', 24),
                self.config.get('p2_font_size', 22),
                self.config.get('p3_font_size', 20),
                self.config.get('other_font_size', 18)
            )
            vertical_padding = self.config.get('vertical_spacing', 4)
            row_height = max_font_size + row_padding + vertical_padding
        
        widget.setFixedHeight(row_height)
        
        return widget
    
    def _update_entry_widget(self, widget, entry, position):
        """Update an existing entry widget with clean, premium styling"""
        labels = widget.findChildren(QLabel)

        # Use balanced widths matching header: Position and Time equal, Driver gets the rest
        widths = [100, 484, 180]  # Position, Driver, Time
        for label, width in zip(labels, widths):
            label.setFixedWidth(width)

        # Premium color palette
        gold = '#D4AF37'      # Elegant gold
        silver = '#A8A9AD'    # Clean silver
        bronze = '#CD7F32'    # Rich bronze
        text_white = '#FFFFFF'
        text_gray = '#B0B0B0'

        # Font sizes - consistent across all positions for clean look
        base_font_size = self.config.get('other_font_size', 22)

        if position <= 3:
            # Podium positions - clean accent colors, no chunky backgrounds
            accent_colors = [gold, silver, bronze]
            accent = accent_colors[position - 1]

            # Position number - clean white text with colored left border accent
            labels[0].setStyleSheet(f"""
                color: {accent};
                background: transparent;
                font-size: {base_font_size + 2}px;
                font-weight: bold;
                border-left: 4px solid {accent};
                padding-left: 12px;
            """)
            labels[0].setText(str(position))

            # Driver name - white text, clean
            labels[1].setStyleSheet(f"""
                color: {text_white};
                background: transparent;
                font-size: {base_font_size}px;
                font-weight: normal;
            """)

            # Lap time - accent colored
            labels[2].setStyleSheet(f"""
                color: {accent};
                background: transparent;
                font-size: {base_font_size}px;
                font-weight: bold;
            """)

            # Subtle row background for podium
            widget.setStyleSheet(f"""
                background-color: rgba(255, 255, 255, 8);
                border-bottom: 1px solid rgba(255, 255, 255, 15);
            """)
        else:
            # Positions 4-10 - clean, minimal styling
            labels[0].setStyleSheet(f"""
                color: {text_gray};
                background: transparent;
                font-size: {base_font_size}px;
                font-weight: normal;
                border-left: 4px solid transparent;
                padding-left: 12px;
            """)
            labels[0].setText(str(position))

            labels[1].setStyleSheet(f"""
                color: {text_white};
                background: transparent;
                font-size: {base_font_size}px;
                font-weight: normal;
            """)

            labels[2].setStyleSheet(f"""
                color: {text_gray};
                background: transparent;
                font-size: {base_font_size}px;
                font-weight: normal;
            """)

            # Subtle separator line
            widget.setStyleSheet(f"""
                background: transparent;
                border-bottom: 1px solid rgba(255, 255, 255, 10);
            """)

        # Driver name
        labels[1].setText(entry['driver_name'])

        # Time - clean format
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
                background-color: rgba(50, 50, 50, {opacity});
                border-radius: 10px;
            }}
            QLabel {{
                background-color: transparent;
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
        # Use balanced widths: Position and Time equal, Driver gets the rest
        widths = [100, 484, 180]  # Position, Driver, Time

        # Update header widths
        header_labels = self.header_widget.findChildren(QLabel)
        for label, width in zip(header_labels, widths):
            label.setFixedWidth(width)

        # Update entry widths
        for entry_widget in self.entry_widgets:
            entry_labels = entry_widget.findChildren(QLabel)
            for label, width in zip(entry_labels, widths):
                label.setFixedWidth(width)
        
        # Update panel size based on orientation
        is_vertical = self.config.get('orientation', 'horizontal') == 'vertical'
        if is_vertical:
            # Fixed size for vertical orientation: 864 x (11 rows x 123px = 1353)
            self.leaderboard_panel.setFixedSize(864, 1353)
        else:
            # Use configured width for horizontal orientation
            panel_width = self.config.get('panel_width', 800)
            self.leaderboard_panel.setFixedWidth(panel_width)

    # Add new methods for moving the leaderboard
    def move_left(self):
        """Move the leaderboard to the left"""
        self.horizontal_offset -= 50
        self._save_offsets_for_orientation()  # Save to correct config keys
        
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
        self._save_offsets_for_orientation()  # Save to correct config keys
        
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
            # For vertical orientation, use fixed size
            is_vertical = self.config.get('orientation', 'horizontal') == 'vertical'
            if is_vertical:
                # Fixed size for vertical: 864 x (11 rows x 123px = 1353)
                self.leaderboard_panel.setFixedSize(864, 1353)
                panel_width = 864
                panel_height = 1353
            else:
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
        opacity = self.config.get('opacity', 220)
        self.header_widget.setStyleSheet(f"""
            QWidget {{
                background-color: rgba(50, 50, 50, {opacity});
                {'' if self.is_fullscreen else 'border-top-left-radius: 10px; border-top-right-radius: 10px;'}
            }}
            QLabel {{
                background-color: transparent;
                color: white;
                font-size: {header_font_size}px;
                font-weight: bold;
                padding: 10px 0px;
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
                padding: 8px 0px;
            }}
        """)
        
        # If data was provided directly, use it to refresh entries
        if data and self.entry_widgets:
            self.update_entries(data)

    # Add methods for moving the leaderboard vertically
    def move_up(self):
        """Move the leaderboard up"""
        self.vertical_offset -= 50
        self._save_offsets_for_orientation()  # Save to correct config keys
        
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
        self._save_offsets_for_orientation()  # Save to correct config keys
        
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
    def send_json_response(self, data, status=200):
        """Helper to send JSON responses"""
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))

    def do_OPTIONS(self):
        """Handle CORS preflight requests"""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_POST(self):
        global queue_data
        path = self.path.split('?')[0]  # Remove query string

        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length) if content_length > 0 else b'{}'
            data = json.loads(post_data.decode('utf-8')) if post_data else {}

            # Route to appropriate handler
            if path == '/api/queue/join':
                self.handle_queue_join(data)
            elif path == '/api/queue/remove':
                self.handle_queue_remove(data)
            elif path == '/api/queue/assign':
                self.handle_queue_assign(data)
            elif path == '/api/session/started':
                self.handle_session_started(data)
            elif path == '/api/session/ended':
                self.handle_session_ended(data)
            else:
                # Default: handle as lap time submission
                self.handle_lap_time(data)

        except Exception as e:
            print(f"Server error: {str(e)}")
            self.send_json_response({'success': False, 'error': str(e)}, 500)

    def handle_lap_time(self, lap_data):
        """Handle lap time submission (original behavior)"""
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

        # Track connected simulator
        client_ip = self.client_address[0]
        connected_simulators[simulator_id] = {
            'ip': client_ip,
            'last_seen': datetime.now(),
            'driver': driver_name,
            'last_lap': lap_time
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

    def handle_queue_join(self, data):
        """Add a guest to the queue"""
        global queue_data
        name = data.get('name', '').strip()
        phone = data.get('phone', '').strip()

        if not name:
            self.send_json_response({'success': False, 'error': 'Name is required'}, 400)
            return

        # Generate unique ID
        queue_id = generate_queue_id()
        entry = {
            'id': queue_id,
            'name': name,
            'phone': phone,
            'joined_at': int(datetime.now().timestamp()),
            'assigned_to': None
        }

        queue_data['queue'].append(entry)
        save_queue_data()

        position = len(queue_data['queue'])
        wait_seconds = calculate_wait_time(position)

        self.send_json_response({
            'success': True,
            'id': queue_id,
            'position': position,
            'wait_seconds': wait_seconds,
            'wait_display': format_wait_time(wait_seconds)
        })

    def handle_queue_remove(self, data):
        """Remove a guest from the queue"""
        global queue_data
        queue_id = data.get('id', '')

        if not queue_id:
            self.send_json_response({'success': False, 'error': 'ID is required'}, 400)
            return

        original_len = len(queue_data['queue'])
        queue_data['queue'] = [e for e in queue_data['queue'] if e.get('id') != queue_id]

        if len(queue_data['queue']) < original_len:
            save_queue_data()
            self.send_json_response({'success': True})
        else:
            self.send_json_response({'success': False, 'error': 'Entry not found'}, 404)

    def handle_queue_assign(self, data):
        """Assign a queue entry to a simulator"""
        global queue_data
        queue_id = data.get('queue_id', '')
        simulator_ip = data.get('simulator_ip', '')
        simulator_name = data.get('simulator_name', simulator_ip)  # Optional friendly name

        if not queue_id or not simulator_ip:
            self.send_json_response({'success': False, 'error': 'queue_id and simulator_ip are required'}, 400)
            return

        # Find the queue entry
        entry = None
        for e in queue_data['queue']:
            if e.get('id') == queue_id:
                entry = e
                break

        if not entry:
            self.send_json_response({'success': False, 'error': 'Queue entry not found'}, 404)
            return

        # Mark as assigned
        entry['assigned_to'] = simulator_ip
        save_queue_data()

        # Send SMS notification if phone number is available
        sms_sent = False
        phone = entry.get('phone', '')
        if phone:
            sms_sent = send_sms_notification(
                phone_number=phone,
                driver_name=entry.get('name', 'Guest'),
                simulator_name=simulator_name
            )

        self.send_json_response({
            'success': True,
            'name': entry.get('name'),
            'simulator_ip': simulator_ip,
            'sms_sent': sms_sent
        })

    def handle_session_started(self, data):
        """Record that a session has started on a simulator"""
        global queue_data
        simulator_ip = data.get('simulator_ip', '')
        driver_name = data.get('driver_name', '')
        queue_id = data.get('queue_id', '')

        if not simulator_ip:
            self.send_json_response({'success': False, 'error': 'simulator_ip is required'}, 400)
            return

        # Record active session
        queue_data['active_sessions'][simulator_ip] = {
            'started_at': int(datetime.now().timestamp()),
            'driver_name': driver_name
        }

        # Remove from queue if queue_id provided
        if queue_id:
            queue_data['queue'] = [e for e in queue_data['queue'] if e.get('id') != queue_id]

        save_queue_data()
        self.send_json_response({'success': True})

    def handle_session_ended(self, data):
        """Record that a session has ended on a simulator"""
        global queue_data
        simulator_ip = data.get('simulator_ip', '')
        duration_seconds = data.get('duration_seconds', 0)

        if not simulator_ip:
            self.send_json_response({'success': False, 'error': 'simulator_ip is required'}, 400)
            return

        # Remove from active sessions
        if simulator_ip in queue_data['active_sessions']:
            del queue_data['active_sessions'][simulator_ip]

        # Add to session history
        if duration_seconds > 0:
            queue_data['session_history'].append({
                'simulator_ip': simulator_ip,
                'duration_seconds': duration_seconds,
                'ended_at': int(datetime.now().timestamp())
            })

            # Keep only last MAX_SESSION_HISTORY entries
            if len(queue_data['session_history']) > MAX_SESSION_HISTORY:
                queue_data['session_history'] = queue_data['session_history'][-MAX_SESSION_HISTORY:]

        save_queue_data()
        self.send_json_response({'success': True})

    def do_GET(self):
        """Handle GET requests"""
        global queue_data
        path = self.path.split('?')[0]  # Remove query string
        client_ip = self.client_address[0]

        # Route to appropriate handler
        if path == '/api/queue':
            self.handle_get_queue()
            return
        elif path == '/api/queue/stats':
            self.handle_get_stats()
            return
        else:
            # Default: track as connected simulator (ping only)
            sim_id = f"ping_{client_ip.replace('.', '_')}"
            connected_simulators[sim_id] = {
                'ip': client_ip,
                'last_seen': datetime.now(),
                'driver': '(Testing connection)',
                'last_lap': None
            }

            self.send_response(200)
            self.end_headers()

    def handle_get_queue(self):
        """Get the full queue with wait time estimates"""
        global queue_data
        load_queue_data()  # Refresh from file

        queue_with_estimates = get_queue_with_estimates()
        active_sessions = queue_data.get('active_sessions', {})
        session_history = queue_data.get('session_history', [])

        self.send_json_response({
            'success': True,
            'queue': queue_with_estimates,
            'active_sessions': active_sessions,
            'total_in_queue': len(queue_with_estimates),
            'has_enough_history': len(session_history) >= 3
        })

    def handle_get_stats(self):
        """Get queue statistics"""
        global queue_data
        load_queue_data()  # Refresh from file

        session_history = queue_data.get('session_history', [])
        active_sessions = queue_data.get('active_sessions', {})

        # Calculate average session time
        avg_session_time = None
        if len(session_history) >= 3:
            recent = session_history[-20:] if len(session_history) > 20 else session_history
            avg_session_time = sum(s.get('duration_seconds', 300) for s in recent) / len(recent)

        # Get unique simulator count
        all_sim_ips = set()
        for session in session_history:
            all_sim_ips.add(session.get('simulator_ip', ''))
        for ip in active_sessions.keys():
            all_sim_ips.add(ip)

        self.send_json_response({
            'success': True,
            'avg_session_time_seconds': avg_session_time,
            'avg_session_time_display': format_wait_time(int(avg_session_time)) if avg_session_time else None,
            'active_simulator_count': len(active_sessions),
            'total_simulators_seen': len(all_sim_ips),
            'sessions_recorded': len(session_history),
            'queue_length': len(queue_data.get('queue', []))
        })

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
            'entry_font_size': 30,
            'p1_font_size': 30,    # First place font size
            'p2_font_size': 30,    # Second place font size
            'p3_font_size': 30,    # Third place font size
            'other_font_size': 30, # Other positions font size
            'background_image': '',
            'fill_screen': False,   # Whether to fill the entire screen with background
            'position_width': 80,    # Default width for position column
            'driver_width': 400,     # Default width for driver name column
            'time_width': 200,       # Default width for time column
            'panel_width': 800,      # Default width for entire panel
            'horizontal_offset': 0,   # Legacy - kept for migration
            'vertical_offset': 0,   # Legacy - kept for migration
            'horizontal_offset_h': 0,   # Horizontal offset for horizontal mode
            'vertical_offset_h': 0,   # Vertical offset for horizontal mode
            'horizontal_offset_v': 0,   # Horizontal offset for vertical mode
            'vertical_offset_v': 0,   # Vertical offset for vertical mode
            'vertical_spacing': 4,   # Default vertical spacing between entries
            'row_height_padding': 16,  # Default padding for row height
            'orientation': 'horizontal',  # 'horizontal' or 'vertical' display mode
            'vertical_row_height': 123  # Fixed row height for vertical mode (px)
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
                    # Remove deprecated aspect ratio settings
                    if 'use_tall_aspect' in loaded_config:
                        del loaded_config['use_tall_aspect']
                    if 'use_1344_aspect' in loaded_config:
                        del loaded_config['use_1344_aspect']
                    
                    # Migrate legacy offset values to per-mode keys if not already migrated
                    if 'horizontal_offset' in loaded_config and 'horizontal_offset_h' not in loaded_config:
                        # Determine which mode the legacy offsets belong to based on current orientation
                        current_orientation = loaded_config.get('orientation', 'horizontal')
                        if current_orientation == 'vertical':
                            loaded_config['horizontal_offset_v'] = loaded_config.get('horizontal_offset', 0)
                            loaded_config['vertical_offset_v'] = loaded_config.get('vertical_offset', 0)
                            loaded_config['horizontal_offset_h'] = 0
                            loaded_config['vertical_offset_h'] = 0
                        else:
                            loaded_config['horizontal_offset_h'] = loaded_config.get('horizontal_offset', 0)
                            loaded_config['vertical_offset_h'] = loaded_config.get('vertical_offset', 0)
                            loaded_config['horizontal_offset_v'] = 0
                            loaded_config['vertical_offset_v'] = 0
                    
                    # Update config with loaded values, keeping defaults for missing keys
                    self.config.update(loaded_config)
                    
                # Always update server_url with current device's IP
                local_ip = get_local_ip()
                self.config['server_url'] = f'http://{local_ip}:5000'
            except:
                pass  # Keep defaults if loading fails
    
    def setup_ui(self):
        self.setWindowTitle("Leaderboard Control")
        self.setGeometry(100, 100, 680, 920)
        self.setFixedSize(680, 920)

        # Set window icon
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.png')
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        elif os.path.exists('icon.png'):
            self.setWindowIcon(QIcon('icon.png'))

        # Set the application style - dark theme matching Sender
        self.setStyleSheet("""
            QMainWindow {
                background-color: #1e1e1e;
            }
            QWidget {
                background-color: #1e1e1e;
                color: #cccccc;
            }
            QLabel {
                font-size: 14px;
                color: #cccccc;
                background-color: transparent;
            }
            QLineEdit, QComboBox {
                padding: 8px 12px;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
                background-color: #2d2d30;
                color: #cccccc;
                min-height: 32px;
            }
            QLineEdit:focus, QComboBox:focus {
                border-color: #007acc;
            }
            QPushButton {
                padding: 10px 20px;
                background-color: #007acc;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                min-height: 36px;
            }
            QPushButton:hover {
                background-color: #005a9e;
            }
            QTabWidget::pane {
                border: 1px solid #3c3c3c;
                background-color: #252526;
                border-radius: 4px;
            }
            QTabBar::tab {
                background-color: #2d2d30;
                color: #cccccc;
                padding: 10px 20px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                border: 1px solid #3c3c3c;
                border-bottom: none;
            }
            QTabBar::tab:selected {
                background-color: #252526;
                color: #ffffff;
            }
            QTabBar::tab:hover {
                background-color: #3c3c3c;
            }
            QScrollArea {
                border: none;
                background-color: #252526;
            }
            QFrame {
                background-color: #252526;
            }
        """)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        # Tighter top-level spacing and margins
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)
        
        self.show_leaderboard_btn = QPushButton("Show Leaderboard")
        self.show_leaderboard_btn.setStyleSheet("""
            QPushButton {
                background-color: #007acc;
                font-size: 16px;
                padding: 12px 20px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #005a9e;
            }
        """)
        self.show_leaderboard_btn.clicked.connect(self.toggle_leaderboard)
        layout.addWidget(self.show_leaderboard_btn)

        # Event Management Section
        event_frame = QFrame()
        event_frame.setStyleSheet("""
            QFrame {
                background-color: #2d2d30;
                border: 1px solid #3c3c3c;
                border-radius: 6px;
                padding: 8px;
            }
        """)
        event_layout = QHBoxLayout(event_frame)
        event_layout.setSpacing(12)
        event_layout.setContentsMargins(12, 10, 12, 10)

        event_label = QLabel("Event:")
        event_label.setStyleSheet("font-weight: bold; color: #007acc; border: none; background: transparent;")
        event_layout.addWidget(event_label)

        export_btn = QPushButton("Export & End Event")
        export_btn.setStyleSheet("""
            QPushButton {
                background-color: #4ec9b0;
                color: #1e1e1e;
                font-size: 13px;
                padding: 8px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #3cb99f;
            }
        """)
        export_btn.setToolTip("Save lap times to a named file and clear the leaderboard for the next event")
        export_btn.clicked.connect(self.export_and_end_event)
        event_layout.addWidget(export_btn)

        clear_btn = QPushButton("Clear Only")
        clear_btn.setStyleSheet("""
            QPushButton {
                background-color: #f48771;
                color: #1e1e1e;
                font-size: 13px;
                padding: 8px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #d96a56;
            }
        """)
        clear_btn.setToolTip("Clear the leaderboard without saving")
        clear_btn.clicked.connect(self.clear_leaderboard)
        event_layout.addWidget(clear_btn)

        event_layout.addStretch()
        layout.addWidget(event_frame)

        tabs = QTabWidget()
        layout.addWidget(tabs)
        
        # General tab (server and app behavior)
        general_tab = QWidget()
        general_layout = QGridLayout(general_tab)
        general_layout.setHorizontalSpacing(12)
        general_layout.setVerticalSpacing(12)
        general_layout.setContentsMargins(12, 12, 12, 12)
        # Make inputs wider than labels so fields are easy to edit
        general_layout.setColumnStretch(0, 1)
        general_layout.setColumnStretch(1, 4)
        general_layout.setColumnMinimumWidth(0, 160)
        general_layout.setColumnMinimumWidth(1, 320)
        
        row_g = 0
        # Display orientation at the very top - most important setting
        orientation_label = QLabel("Display Orientation:")
        orientation_label.setStyleSheet("font-weight: bold;")
        general_layout.addWidget(orientation_label, row_g, 0)
        orientation_widget_g = QWidget()
        orientation_layout_g = QHBoxLayout(orientation_widget_g)
        orientation_layout_g.setSpacing(10)
        orientation_layout_g.setContentsMargins(0, 0, 0, 0)
        self.orientation_toggle = QPushButton(self.config.get('orientation', 'horizontal').capitalize())
        self.orientation_toggle.setStyleSheet("""
            QPushButton {
                background-color: #007acc;
                padding: 8px 16px;
                color: white;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #005a9e;
            }
        """)
        self.orientation_toggle.setFixedWidth(140)
        self.orientation_toggle.clicked.connect(self.toggle_orientation)
        orientation_layout_g.addWidget(self.orientation_toggle)
        orientation_info = QLabel("(Vertical: 1080x1920, Horizontal: 1920x1080)")
        orientation_info.setStyleSheet("color: #888888; font-style: italic;")
        orientation_layout_g.addWidget(orientation_info)
        orientation_layout_g.addStretch()
        general_layout.addWidget(orientation_widget_g, row_g, 1)
        
        # Separator after orientation
        row_g += 1
        separator_orient = QFrame()
        separator_orient.setFrameShape(QFrame.Shape.HLine)
        separator_orient.setFrameShadow(QFrame.Shadow.Sunken)
        separator_orient.setStyleSheet("background-color: #3c3c3c;")
        separator_orient.setFixedHeight(1)
        separator_orient.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        general_layout.addWidget(separator_orient, row_g, 0, 1, 2)
        
        row_g += 1
        # Server URL (read-only)
        url_label = QLabel("Server URL:")
        url_label.setStyleSheet("font-weight: bold;")
        general_layout.addWidget(url_label, row_g, 0)
        self.server_url_entry = QLineEdit(self.config.get('server_url', 'http://localhost:5000'))
        self.server_url_entry.setReadOnly(True)
        self.server_url_entry.setStyleSheet("""
            QLineEdit {
                background-color: #252526;
                color: #888888;
                border: 1px solid #3c3c3c;
            }
        """)
        self.server_url_entry.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        general_layout.addWidget(self.server_url_entry, row_g, 1)
        
        row_g += 1
        self.server_status_label = QLabel("Server Status: Not Running")
        self.server_status_label.setStyleSheet("color: #888888;")
        general_layout.addWidget(self.server_status_label, row_g, 0, 1, 2)

        row_g += 1
        self.discovery_status_label = QLabel("Discovery: Inactive")
        self.discovery_status_label.setStyleSheet("color: #4ec9b0; font-style: italic;")
        general_layout.addWidget(self.discovery_status_label, row_g, 0, 1, 2)

        row_g += 1
        self.server_toggle_btn = QPushButton("Start Server")
        self.server_toggle_btn.clicked.connect(self.toggle_server)
        general_layout.addWidget(self.server_toggle_btn, row_g, 0, 1, 2)
        
        row_g += 1
        general_layout.addWidget(QLabel("Server Port:"), row_g, 0)
        self.server_port_entry = QLineEdit(str(self.config.get('server_port', 5000)))
        self.server_port_entry.setMaxLength(5)
        self.server_port_entry.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        general_layout.addWidget(self.server_port_entry, row_g, 1)
        
        row_g += 1
        server_info = QLabel("The server receives lap times from racing simulators.\nIt must be running to record new lap times.")
        server_info.setStyleSheet("color: #888888; font-style: italic;")
        server_info.setWordWrap(True)
        general_layout.addWidget(server_info, row_g, 0, 1, 2)

        # Separator
        row_g += 1
        separator_g = QFrame()
        separator_g.setFrameShape(QFrame.Shape.HLine)
        separator_g.setFrameShadow(QFrame.Shadow.Sunken)
        separator_g.setStyleSheet("background-color: #3c3c3c;")
        separator_g.setFixedHeight(1)
        separator_g.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        general_layout.addWidget(separator_g, row_g, 0, 1, 2)
        
        row_g += 1
        general_layout.addWidget(QLabel("Refresh Interval:"), row_g, 0)
        self.refresh_interval = QLineEdit(str(self.config.get('refresh_interval', 5)))
        self.refresh_interval.setMaxLength(3)
        self.refresh_interval.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        general_layout.addWidget(self.refresh_interval, row_g, 1)

        # Connected Simulators section
        row_g += 1
        separator_sim = QFrame()
        separator_sim.setFrameShape(QFrame.Shape.HLine)
        separator_sim.setFrameShadow(QFrame.Shadow.Sunken)
        separator_sim.setStyleSheet("background-color: #3c3c3c;")
        separator_sim.setFixedHeight(1)
        general_layout.addWidget(separator_sim, row_g, 0, 1, 2)

        row_g += 1
        # Create horizontal layout for label and Clear All button
        sim_header_layout = QHBoxLayout()
        sim_label = QLabel("Connected Simulators:")
        sim_label.setStyleSheet("font-weight: bold; color: #cccccc;")
        sim_header_layout.addWidget(sim_label)
        sim_header_layout.addStretch()

        clear_sim_btn = QPushButton("Clear All")
        clear_sim_btn.setStyleSheet("""
            QPushButton {
                background-color: #f48771;
                color: #1e1e1e;
                border: none;
                padding: 4px 12px;
                border-radius: 4px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #d96a56;
            }
        """)
        clear_sim_btn.clicked.connect(self.clear_simulators)
        sim_header_layout.addWidget(clear_sim_btn)

        sim_header_widget = QWidget()
        sim_header_widget.setLayout(sim_header_layout)
        general_layout.addWidget(sim_header_widget, row_g, 0, 1, 2)

        row_g += 1
        self.simulators_display = QLabel("No simulators connected")
        self.simulators_display.setStyleSheet("""
            QLabel {
                background-color: #252526;
                color: #4ec9b0;
                font-family: Consolas, monospace;
                font-size: 12px;
                padding: 10px;
                border-radius: 5px;
                border: 1px solid #3c3c3c;
            }
        """)
        self.simulators_display.setWordWrap(True)
        self.simulators_display.setMinimumHeight(80)
        general_layout.addWidget(self.simulators_display, row_g, 0, 1, 2)

        # Timer to refresh connected simulators display
        self.sim_refresh_timer = QTimer()
        self.sim_refresh_timer.timeout.connect(self.update_simulators_display)
        self.sim_refresh_timer.start(2000)  # Update every 2 seconds

        row_g += 1
        save_btn_general = QPushButton("Save Settings")
        save_btn_general.setStyleSheet("""
            QPushButton {
                background-color: #007acc;
                font-size: 14px;
                padding: 10px;
                margin-top: 10px;
            }
            QPushButton:hover {
                background-color: #005a9e;
            }
        """)
        save_btn_general.clicked.connect(self.save_settings)
        general_layout.addWidget(save_btn_general, row_g, 0, 1, 2)
        general_layout.setRowStretch(row_g + 1, 1)
        tabs.addTab(general_tab, "General")

        # Appearance tab (visual controls) - two columns to reduce scrolling
        appearance_tab = QWidget()
        appearance_vlayout = QVBoxLayout(appearance_tab)
        appearance_vlayout.setSpacing(8)
        appearance_vlayout.setContentsMargins(12, 12, 12, 12)

        columns_layout = QHBoxLayout()
        columns_layout.setSpacing(24)

        # Left column
        left_widget = QWidget()
        left_layout = QGridLayout(left_widget)
        left_layout.setHorizontalSpacing(12)
        left_layout.setVerticalSpacing(12)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setColumnStretch(0, 1)
        left_layout.setColumnStretch(1, 3)
        left_layout.setColumnMinimumWidth(0, 160)
        left_layout.setColumnMinimumWidth(1, 320)

        row_l = 0
        left_layout.addWidget(QLabel("Box Opacity (0-255):"), row_l, 0)
        self.opacity_entry = QLineEdit(str(self.config.get('opacity', 220)))
        self.opacity_entry.setMaxLength(3)
        self.opacity_entry.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        left_layout.addWidget(self.opacity_entry, row_l, 1)

        row_l += 1
        left_layout.addWidget(QLabel("Background Image:"), row_l, 0)
        bg_widget = QWidget()
        bg_layout = QHBoxLayout(bg_widget)
        bg_layout.setSpacing(10)
        bg_layout.setContentsMargins(0, 0, 0, 0)
        self.bg_path_entry = QLineEdit(self.config.get('background_image', ''))
        self.bg_path_entry.setReadOnly(True)
        self.bg_path_entry.setMinimumWidth(300)
        self.bg_path_entry.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        bg_layout.addWidget(self.bg_path_entry, 1)
        browse_btn = QPushButton("Browse")
        browse_btn.setStyleSheet("""
            QPushButton {
                background-color: #3c3c3c;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #505050;
            }
        """)
        browse_btn.clicked.connect(self.choose_background)
        browse_btn.setMaximumWidth(120)
        bg_layout.addWidget(browse_btn)
        left_layout.addWidget(bg_widget, row_l, 1)

        # Fill mode toggle
        row_l += 1
        left_layout.addWidget(QLabel("Background Fill Mode:"), row_l, 0)
        fill_widget = QWidget()
        fill_layout = QHBoxLayout(fill_widget)
        fill_layout.setSpacing(10)
        fill_layout.setContentsMargins(0, 0, 0, 0)
        self.fill_toggle = QPushButton(self.config.get('fill_screen', False) and "Fill Screen" or "Keep Aspect Ratio")
        self.fill_toggle.setStyleSheet("""
            QPushButton {
                background-color: #3c3c3c;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #505050;
            }
        """)
        self.fill_toggle.setFixedWidth(140)
        self.fill_toggle.clicked.connect(self.toggle_fill_mode)
        fill_layout.addWidget(self.fill_toggle)
        left_layout.addWidget(fill_widget, row_l, 1)

        # Header/Entry font sizes
        row_l += 1
        sep_l = QFrame()
        sep_l.setFrameShape(QFrame.Shape.HLine)
        sep_l.setFrameShadow(QFrame.Shadow.Sunken)
        sep_l.setStyleSheet("background-color: #3c3c3c;")
        sep_l.setFixedHeight(1)
        sep_l.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        left_layout.addWidget(sep_l, row_l, 0, 1, 2)

        row_l += 1
        left_layout.addWidget(QLabel("Header Font Size:"), row_l, 0)
        self.header_font_size = QLineEdit(str(self.config.get('header_font_size', 24)))
        self.header_font_size.setMaxLength(2)
        self.header_font_size.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        left_layout.addWidget(self.header_font_size, row_l, 1)

        row_l += 1
        left_layout.addWidget(QLabel("Entry Font Size:"), row_l, 0)
        self.entry_font_size = QLineEdit(str(self.config.get('entry_font_size', 20)))
        self.entry_font_size.setMaxLength(2)
        self.entry_font_size.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        left_layout.addWidget(self.entry_font_size, row_l, 1)

        # Right column
        right_widget = QWidget()
        right_layout = QGridLayout(right_widget)
        right_layout.setHorizontalSpacing(12)
        right_layout.setVerticalSpacing(12)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setColumnStretch(0, 1)
        right_layout.setColumnStretch(1, 3)
        right_layout.setColumnMinimumWidth(0, 160)
        right_layout.setColumnMinimumWidth(1, 320)

        row_r = 0
        right_layout.addWidget(QLabel("1st Place Font Size:"), row_r, 0)
        self.p1_font_size = QLineEdit(str(self.config.get('p1_font_size', 24)))
        self.p1_font_size.setMaxLength(2)
        self.p1_font_size.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        right_layout.addWidget(self.p1_font_size, row_r, 1)

        row_r += 1
        right_layout.addWidget(QLabel("2nd Place Font Size:"), row_r, 0)
        self.p2_font_size = QLineEdit(str(self.config.get('p2_font_size', 22)))
        self.p2_font_size.setMaxLength(2)
        self.p2_font_size.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        right_layout.addWidget(self.p2_font_size, row_r, 1)

        row_r += 1
        right_layout.addWidget(QLabel("3rd Place Font Size:"), row_r, 0)
        self.p3_font_size = QLineEdit(str(self.config.get('p3_font_size', 20)))
        self.p3_font_size.setMaxLength(2)
        self.p3_font_size.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        right_layout.addWidget(self.p3_font_size, row_r, 1)

        row_r += 1
        right_layout.addWidget(QLabel("Other Positions Font Size:"), row_r, 0)
        self.other_font_size = QLineEdit(str(self.config.get('other_font_size', 18)))
        self.other_font_size.setMaxLength(2)
        self.other_font_size.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        right_layout.addWidget(self.other_font_size, row_r, 1)

        # Separator
        row_r += 1
        sep_r1 = QFrame()
        sep_r1.setFrameShape(QFrame.Shape.HLine)
        sep_r1.setFrameShadow(QFrame.Shadow.Sunken)
        sep_r1.setStyleSheet("background-color: #3c3c3c;")
        sep_r1.setFixedHeight(1)
        sep_r1.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        right_layout.addWidget(sep_r1, row_r, 0, 1, 2)

        # Column widths
        row_r += 1
        right_layout.addWidget(QLabel("Position Column Width:"), row_r, 0)
        self.position_width = QLineEdit(str(self.config.get('position_width', 80)))
        self.position_width.setMaxLength(3)
        self.position_width.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        right_layout.addWidget(self.position_width, row_r, 1)

        row_r += 1
        right_layout.addWidget(QLabel("Driver Column Width:"), row_r, 0)
        self.driver_width = QLineEdit(str(self.config.get('driver_width', 400)))
        self.driver_width.setMaxLength(3)
        self.driver_width.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        right_layout.addWidget(self.driver_width, row_r, 1)

        row_r += 1
        right_layout.addWidget(QLabel("Time Column Width:"), row_r, 0)
        self.time_width = QLineEdit(str(self.config.get('time_width', 200)))
        self.time_width.setMaxLength(3)
        self.time_width.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        right_layout.addWidget(self.time_width, row_r, 1)

        row_r += 1
        right_layout.addWidget(QLabel("Overall Panel Width:"), row_r, 0)
        self.panel_width = QLineEdit(str(self.config.get('panel_width', 800)))
        self.panel_width.setMaxLength(4)
        self.panel_width.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        right_layout.addWidget(self.panel_width, row_r, 1)

        # Separator
        row_r += 1
        sep_r2 = QFrame()
        sep_r2.setFrameShape(QFrame.Shape.HLine)
        sep_r2.setFrameShadow(QFrame.Shadow.Sunken)
        sep_r2.setStyleSheet("background-color: #3c3c3c;")
        sep_r2.setFixedHeight(1)
        sep_r2.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        right_layout.addWidget(sep_r2, row_r, 0, 1, 2)

        # Spacing and padding
        row_r += 1
        spacing_label = QLabel("Vertical Spacing:")
        spacing_label.setToolTip("Controls the gap between each position row")
        right_layout.addWidget(spacing_label, row_r, 0)
        self.vertical_spacing = QLineEdit(str(self.config.get('vertical_spacing', 4)))
        self.vertical_spacing.setMaxLength(2)
        self.vertical_spacing.setToolTip("Controls the gap between each position row")
        self.vertical_spacing.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        right_layout.addWidget(self.vertical_spacing, row_r, 1)

        row_r += 1
        padding_label = QLabel("Row Height Padding:")
        padding_label.setToolTip("Controls the internal height of each position row")
        right_layout.addWidget(padding_label, row_r, 0)
        self.row_height_padding = QLineEdit(str(self.config.get('row_height_padding', 16)))
        self.row_height_padding.setMaxLength(2)
        self.row_height_padding.setToolTip("Controls the internal height of each position row")
        self.row_height_padding.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        right_layout.addWidget(self.row_height_padding, row_r, 1)

        # Assemble columns and add to tab
        columns_layout.addWidget(left_widget)
        columns_layout.addWidget(right_widget)
        appearance_vlayout.addLayout(columns_layout)
        appearance_vlayout.addStretch(1)

        save_btn_appearance = QPushButton("Save Settings")
        save_btn_appearance.setStyleSheet("""
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
        save_btn_appearance.clicked.connect(self.save_settings)
        appearance_vlayout.addWidget(save_btn_appearance)
        tabs.addTab(appearance_tab, "Appearance")
    
    def create_leaderboard_window(self):
        if self.leaderboard_window is None:
            # Create as a separate window without parent
            self.leaderboard_window = LeaderboardWindow(self.config, parent=None)
            if self.config.get('background_image'):
                self.leaderboard_window.set_background(self.config['background_image'])

    def export_and_end_event(self):
        """Export lap times to a named file and clear the leaderboard"""
        csv_file = 'lap_times.csv'

        # Check if there are any lap times to export
        if not os.path.exists(csv_file):
            QMessageBox.information(self, "No Data", "No lap times to export.")
            return

        # Count entries
        try:
            with open(csv_file, 'r') as f:
                reader = csv.reader(f)
                next(reader, None)  # Skip header
                entry_count = sum(1 for row in reader)
            if entry_count == 0:
                QMessageBox.information(self, "No Data", "No lap times to export.")
                return
        except Exception:
            pass

        # Get event name from user
        from PyQt6.QtWidgets import QInputDialog
        event_name, ok = QInputDialog.getText(
            self,
            "Export Event",
            "Enter event name:",
            text=f"Event {datetime.now().strftime('%Y-%m-%d')}"
        )

        if not ok or not event_name.strip():
            return

        # Clean filename
        safe_name = "".join(c for c in event_name.strip() if c.isalnum() or c in (' ', '-', '_')).strip()
        timestamp = datetime.now().strftime('%Y-%m-%d_%H%M')
        export_filename = f"{safe_name}_{timestamp}.csv"

        # Create exports folder if it doesn't exist
        exports_folder = "event_exports"
        os.makedirs(exports_folder, exist_ok=True)
        export_path = os.path.join(exports_folder, export_filename)

        try:
            # Copy the CSV file
            import shutil
            shutil.copy(csv_file, export_path)

            # Clear the leaderboard
            with open(csv_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['simulator_id', 'driver_name', 'lap_time', 'email', 'timestamp'])

            # Refresh the leaderboard display
            if self.leaderboard_window:
                self.leaderboard_window.update_leaderboard([])

            QMessageBox.information(
                self,
                "Event Exported",
                f"Lap times saved to:\n{exports_folder}/{export_filename}\n\nLeaderboard cleared for next event."
            )

            self.statusBar().showMessage(f"Event exported: {export_filename}")

        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export: {str(e)}")

    def clear_leaderboard(self):
        """Clear the leaderboard without exporting"""
        csv_file = 'lap_times.csv'

        # Confirm with user
        reply = QMessageBox.question(
            self,
            "Clear Leaderboard",
            "Are you sure you want to clear all lap times?\n\nThis cannot be undone!",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            # Clear the CSV file (keep header)
            with open(csv_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['simulator_id', 'driver_name', 'lap_time', 'email', 'timestamp'])

            # Refresh the leaderboard display
            if self.leaderboard_window:
                self.leaderboard_window.update_leaderboard([])

            QMessageBox.information(self, "Cleared", "Leaderboard has been cleared.")
            self.statusBar().showMessage("Leaderboard cleared")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to clear leaderboard: {str(e)}")

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
    
    def toggle_fill_mode(self):
        """Toggle between fill screen and keep aspect ratio"""
        self.config['fill_screen'] = not self.config['fill_screen']
        self.fill_toggle.setText(self.config['fill_screen'] and "Fill Screen" or "Keep Aspect Ratio")
        
        # Update the background if the leaderboard is visible
        if self.leaderboard_window and self.config.get('background_image'):
            self.leaderboard_window.set_background(self.config['background_image'])
    
    def toggle_orientation(self):
        """Toggle between horizontal (1920x1080) and vertical (1080x1920) display orientation"""
        if self.config.get('orientation', 'horizontal') == 'horizontal':
            self.config['orientation'] = 'vertical'
            self.orientation_toggle.setText("Vertical")
        else:
            self.config['orientation'] = 'horizontal'
            self.orientation_toggle.setText("Horizontal")

        # Remove old aspect ratio settings
        self.config['use_tall_aspect'] = False
        self.config['use_1344_aspect'] = False

        # Update the leaderboard window config and refresh
        if self.leaderboard_window:
            self.leaderboard_window.config['orientation'] = self.config['orientation']
            self.leaderboard_window.config['use_tall_aspect'] = False
            self.leaderboard_window.config['use_1344_aspect'] = False

            # Load the correct offsets for the new orientation
            self.leaderboard_window._load_offsets_for_orientation()

            # Update panel size based on orientation
            is_vertical = self.config['orientation'] == 'vertical'
            if is_vertical:
                # 11 rows x 123px = 1353px
                self.leaderboard_window.leaderboard_panel.setFixedSize(864, 1353)
            else:
                # Horizontal mode - set width and allow height to be dynamic
                panel_width = self.config.get('panel_width', 800)
                self.leaderboard_window.leaderboard_panel.setMinimumHeight(0)
                self.leaderboard_window.leaderboard_panel.setMaximumHeight(16777215)
                self.leaderboard_window.leaderboard_panel.setFixedWidth(panel_width)

            # Update column widths to reflect new panel size
            self.leaderboard_window.update_column_widths()

            # Force refresh of entries to apply new sizing
            if self.network_thread and hasattr(self.network_thread, '_last_data'):
                self.leaderboard_window.update_entries(self.network_thread._last_data)

            if self.config.get('background_image'):
                self.leaderboard_window.set_background(self.config['background_image'])

            # Re-apply fullscreen mode to re-center everything
            if self.leaderboard_window.is_fullscreen:
                self.leaderboard_window.set_window_mode(True)

        # Save config to persist orientation change
        with open('config.json', 'w') as f:
            json.dump(self.config, f)
    
    def save_settings(self):
        """Save settings to config file"""
        # Always get current IP address
        local_ip = get_local_ip()
        

        
        # Update config
        self.config.update({
            'server_url': f'http://{local_ip}:5000',  # Always use current IP
            'background_image': self.bg_path_entry.text(),
            'refresh_interval': int(self.refresh_interval.text()),
            'opacity': int(self.opacity_entry.text()),
            'header_font_size': int(self.header_font_size.text()),
            'entry_font_size': int(self.entry_font_size.text()),
            'p1_font_size': int(self.p1_font_size.text()),
            'p2_font_size': int(self.p2_font_size.text()),
            'p3_font_size': int(self.p3_font_size.text()),
            'other_font_size': int(self.other_font_size.text()),
            'server_port': int(self.server_port_entry.text()),
            'fill_screen': self.fill_toggle.text() == "Fill Screen",
            'position_width': int(self.position_width.text()),
            'driver_width': int(self.driver_width.text()),
            'time_width': int(self.time_width.text()),
            'panel_width': int(self.panel_width.text()),
            'vertical_spacing': int(self.vertical_spacing.text()),
            'row_height_padding': int(self.row_height_padding.text()),
            'orientation': self.orientation_toggle.text().lower()
        })
        
        # Preserve per-mode offset values
        if self.leaderboard_window:
            is_vertical = self.config.get('orientation', 'horizontal') == 'vertical'
            if is_vertical:
                self.config['horizontal_offset_v'] = self.leaderboard_window.horizontal_offset
                self.config['vertical_offset_v'] = self.leaderboard_window.vertical_offset
            else:
                self.config['horizontal_offset_h'] = self.leaderboard_window.horizontal_offset
                self.config['vertical_offset_h'] = self.leaderboard_window.vertical_offset
            # Also update legacy keys for backward compatibility
            self.config['horizontal_offset'] = self.leaderboard_window.horizontal_offset
            self.config['vertical_offset'] = self.leaderboard_window.vertical_offset
        
        with open('config.json', 'w') as f:
            json.dump(self.config, f)
            
        if self.leaderboard_window:
            # Update the config in the leaderboard window first
            self.leaderboard_window.config = self.config.copy()  # Make a deep copy to ensure it's passed correctly
            
            # Update panel size based on orientation
            is_vertical = self.config.get('orientation', 'horizontal') == 'vertical'
            if is_vertical:
                # 11 rows x 123px = 1353px
                self.leaderboard_window.leaderboard_panel.setFixedSize(864, 1353)
            else:
                panel_width = self.config.get('panel_width', 800)
                self.leaderboard_window.leaderboard_panel.setFixedWidth(panel_width)
            
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

    def update_simulators_display(self):
        """Update the connected simulators display"""
        if not connected_simulators:
            self.simulators_display.setText("No simulators connected")
            self.simulators_display.setStyleSheet("""
                QLabel {
                    background-color: #2d2d2d;
                    color: #888888;
                    font-family: Consolas, monospace;
                    font-size: 12px;
                    padding: 10px;
                    border-radius: 5px;
                    border: 1px solid #444444;
                }
            """)
            return

        # Build display text
        lines = []
        now = datetime.now()
        active_count = 0

        for sim_id, info in connected_simulators.items():
            time_diff = (now - info['last_seen']).total_seconds()

            # Consider simulator "active" if seen in last 60 seconds
            if time_diff < 60:
                status = "ACTIVE"
                color = "#00ff00"
                active_count += 1
            elif time_diff < 300:
                status = "IDLE"
                color = "#ffaa00"
            else:
                status = "OFFLINE"
                color = "#ff4444"

            # Format last lap time (handle None for ping-only connections)
            lap_time = info.get('last_lap')
            if lap_time is not None:
                lap_str = f"{int(lap_time // 60):02d}:{lap_time % 60:06.3f}"
            else:
                lap_str = "None"

            lines.append(f"Sim {sim_id} ({info['ip']}) - {status}")
            lines.append(f"  Driver: {info['driver']}")
            lines.append(f"  Last Lap: {lap_str}")

        display_text = "\n".join(lines)
        self.simulators_display.setText(display_text)

        # Update color based on active simulators
        if active_count > 0:
            self.simulators_display.setStyleSheet("""
                QLabel {
                    background-color: #1a2d1a;
                    color: #00ff00;
                    font-family: Consolas, monospace;
                    font-size: 12px;
                    padding: 10px;
                    border-radius: 5px;
                    border: 1px solid #00aa00;
                }
            """)
        else:
            self.simulators_display.setStyleSheet("""
                QLabel {
                    background-color: #2d2d2d;
                    color: #ffaa00;
                    font-family: Consolas, monospace;
                    font-size: 12px;
                    padding: 10px;
                    border-radius: 5px;
                    border: 1px solid #444444;
                }
            """)

    def clear_simulators(self):
        """Clear all connected simulators from the list"""
        global connected_simulators
        connected_simulators.clear()
        self.update_simulators_display()

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
                # Re-apply background image when showing (ensures it persists after restart)
                if self.config.get('background_image'):
                    QTimer.singleShot(100, lambda: self.leaderboard_window.set_background(self.config['background_image']))
            
    def update_leaderboard(self, data):
        if self.leaderboard_window:
            self.leaderboard_window.update_entries(data)
    
    def toggle_server(self):
        if hasattr(self, 'http_server'):
            # Stop the server
            self.http_server.shutdown()
            self.server_thread.join()
            delattr(self, 'http_server')

            # Stop discovery responder
            if hasattr(self, 'discovery_responder'):
                self.discovery_responder.stop()
                delattr(self, 'discovery_responder')

            self.server_status_label.setText("Server Status: Stopped")
            self.discovery_status_label.setText("Discovery: Inactive")
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

                # Start UDP discovery responder for auto-discovery by simulators
                self.discovery_responder = DiscoveryResponder(port)
                self.discovery_responder.start()

                # Update server URL with new port
                local_ip = get_local_ip()
                self.config['server_url'] = f'http://{local_ip}:{port}'
                self.config['server_port'] = port  # Save port to config
                self.server_url_entry.setText(self.config['server_url'])

                # Save config to persist port change
                with open('config.json', 'w') as f:
                    json.dump(self.config, f)

                self.server_status_label.setText(f"Server Status: Running on port {port}")
                self.discovery_status_label.setText(f"Discovery: Active (UDP {DISCOVERY_PORT})")
                self.server_toggle_btn.setText("Stop Server")
                self.statusBar().showMessage(f"Lap time server started on port {port}")
            except Exception as e:
                self.server_status_label.setText(f"Server Status: Error - {str(e)}")
                self.statusBar().showMessage(f"Error starting lap time server: {str(e)}")

    def start_lap_time_server(self):
        """Start HTTP server to receive lap times"""
        try:
            # Load queue data from file
            load_queue_data()

            # Load SMS configuration (Textbelt or Twilio)
            load_sms_config()

            port = int(self.config.get('server_port', 5000))
            # Update port input field
            self.server_port_entry.setText(str(port))

            server_address = ('', port)
            self.http_server = HTTPServer(server_address, LapTimeHandler)
            self.server_thread = threading.Thread(target=self.http_server.serve_forever)
            self.server_thread.daemon = True
            self.server_thread.start()

            # Start UDP discovery responder for auto-discovery by simulators
            self.discovery_responder = DiscoveryResponder(port)
            self.discovery_responder.start()

            # Update server URL with initial port
            local_ip = get_local_ip()
            self.config['server_url'] = f'http://{local_ip}:{port}'
            self.server_url_entry.setText(self.config['server_url'])

            self.server_status_label.setText(f"Server Status: Running on port {port}")
            self.discovery_status_label.setText(f"Discovery: Active (UDP {DISCOVERY_PORT})")
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
            if hasattr(self, 'discovery_responder'):
                self.discovery_responder.stop()
            if self.leaderboard_window:
                self.leaderboard_window.close()
            event.accept()
        else:
            event.ignore()

def main():
    logger.debug("Starting application")
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # Use Fusion style for a modern look

    # Set application icon
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.png')
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    elif os.path.exists('icon.png'):
        app.setWindowIcon(QIcon('icon.png'))

    logger.debug("QApplication created")
    window = ControlWindow()
    logger.debug("ControlWindow created")
    window.show()
    logger.debug("ControlWindow shown")
    sys.exit(app.exec())

if __name__ == '__main__':
    main() 