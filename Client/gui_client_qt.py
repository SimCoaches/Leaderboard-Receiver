import sys
import json
import os
import csv
import shutil
import hashlib
import hmac
import uuid
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
    "queue": [],  # [{id, name, email, phone, joined_at, assigned_to}]
    "session_history": [],  # Last 50 completed sessions [{simulator_ip, duration_seconds, ended_at}]
    "active_sessions": {},  # {simulator_ip: {started_at, driver_name}}
    "last_updated": 0
}
QUEUE_FILE = "queue.json"
MAX_SESSION_HISTORY = 50

# Partner integration storage. Disabled by default so the existing leaderboard
# path continues to work even if partner config is missing or broken.
INTEGRATION_CONFIG_FILE = "integration_config.json"
INTEGRATION_LOG_FILE = "integration_events.jsonl"
INTEGRATION_PENDING_FILE = "integration_pending.jsonl"
integration_config = {
    "enabled": False,
    "dry_run": True,
    "webhook_url": "",
    "api_key": "",
    "signing_secret": "",
    "timeout_seconds": 5,
    "demo_url": ""
}

def load_integration_config():
    """Load partner integration settings from disk."""
    global integration_config
    if os.path.exists(INTEGRATION_CONFIG_FILE):
        try:
            with open(INTEGRATION_CONFIG_FILE, 'r') as f:
                loaded = json.load(f)
                integration_config.update(loaded)
        except Exception as e:
            logger.warning(f"Error loading integration config: {e}")
    else:
        save_integration_config()
        print(f"[Integration] Created disabled config file: {INTEGRATION_CONFIG_FILE}")
    return integration_config

def save_integration_config():
    """Save partner integration settings to disk."""
    try:
        safe_config = integration_config.copy()
        with open(INTEGRATION_CONFIG_FILE, 'w') as f:
            json.dump(safe_config, f, indent=2)
    except Exception as e:
        logger.warning(f"Error saving integration config: {e}")

def append_integration_log(record):
    """Append integration delivery records without affecting race flow."""
    try:
        with open(INTEGRATION_LOG_FILE, 'a') as f:
            f.write(json.dumps(record, separators=(',', ':')) + "\n")
    except Exception as e:
        logger.warning(f"Error writing integration log: {e}")

def append_pending_integration_event(event, reason):
    """Persist an event for later retry without blocking the race flow."""
    try:
        record = {
            "queued_at": datetime.now().isoformat(),
            "reason": reason,
            "event": event
        }
        with open(INTEGRATION_PENDING_FILE, 'a') as f:
            f.write(json.dumps(record, separators=(',', ':')) + "\n")
    except Exception as e:
        logger.warning(f"Error writing pending integration event: {e}")

def load_pending_integration_events():
    """Load pending partner events from disk."""
    if not os.path.exists(INTEGRATION_PENDING_FILE):
        return []

    pending = []
    try:
        with open(INTEGRATION_PENDING_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    event = record.get("event")
                    if event:
                        pending.append(record)
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        logger.warning(f"Error loading pending integration events: {e}")
    return pending

def rewrite_pending_integration_events(records):
    """Replace pending event queue with remaining undelivered records."""
    try:
        if not records:
            if os.path.exists(INTEGRATION_PENDING_FILE):
                os.remove(INTEGRATION_PENDING_FILE)
            return
        with open(INTEGRATION_PENDING_FILE, 'w') as f:
            for record in records:
                f.write(json.dumps(record, separators=(',', ':')) + "\n")
    except Exception as e:
        logger.warning(f"Error rewriting pending integration events: {e}")

def split_driver_name(driver_name):
    """Best-effort split for partner payloads while preserving full name."""
    parts = str(driver_name or '').strip().split()
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        return parts[0], "", parts[0]
    return parts[0], " ".join(parts[1:]), " ".join(parts)

def format_lap_time(seconds):
    """Format seconds as mm:ss.xxx for partner payloads."""
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        seconds = 0
    minutes = int(seconds // 60)
    remaining = seconds % 60
    return f"{minutes:02d}:{remaining:06.3f}"

def build_race_completed_event(lap_data):
    """Create the stable partner event payload from a saved lap row."""
    first_name, last_name, full_name = split_driver_name(lap_data.get('driver_name', ''))
    race_time = float(lap_data.get('lap_time', 0))
    session_id = str(lap_data.get('session_id') or f"legacy-{uuid.uuid4()}")
    completed_at = lap_data.get('timestamp') or datetime.now().isoformat()

    return {
        "event_type": "race.completed",
        "event_id": str(uuid.uuid4()),
        "session_id": session_id,
        "racer": {
            "first_name": first_name,
            "last_name": last_name,
            "full_name": full_name,
            "email": str(lap_data.get('email', '')),
            "phone": str(lap_data.get('phone', ''))
        },
        "race_time_seconds": race_time,
        "formatted_race_time": format_lap_time(race_time),
        "simulator_id": str(lap_data.get('simulator_id', '')),
        "completed_at": completed_at,
        "demo_url": integration_config.get("demo_url", "")
    }

def sign_integration_payload(payload_body, secret):
    """Return an HMAC signature for partner webhook verification."""
    return hmac.new(
        secret.encode('utf-8'),
        payload_body.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

def attempt_integration_delivery(event):
    """Attempt partner delivery once and return a delivery record."""
    config = load_integration_config()
    record = {
        "event_id": event.get("event_id"),
        "event_type": event.get("event_type"),
        "created_at": datetime.now().isoformat(),
        "enabled": bool(config.get("enabled")),
        "dry_run": bool(config.get("dry_run")),
        "delivered": False,
        "status_code": None,
        "error": None
    }

    if not config.get("enabled") or config.get("dry_run"):
        record["delivered"] = True
        record["error"] = "dry_run" if config.get("dry_run") else "disabled"
        return record

    webhook_url = config.get("webhook_url", "").strip()
    if not webhook_url:
        record["error"] = "missing_webhook_url"
        return record

    try:
        body = json.dumps(event, separators=(',', ':'), sort_keys=True)
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "SimCoaches-Leaderboard-Receiver"
        }
        if config.get("api_key"):
            headers["Authorization"] = f"Bearer {config['api_key']}"
        if config.get("signing_secret"):
            headers["X-SimCoaches-Signature"] = sign_integration_payload(body, config["signing_secret"])

        response = requests.post(
            webhook_url,
            data=body,
            headers=headers,
            timeout=int(config.get("timeout_seconds", 5))
        )
        record["status_code"] = response.status_code
        record["delivered"] = 200 <= response.status_code < 300
        if not record["delivered"]:
            record["error"] = response.text[:500]
    except Exception as e:
        record["error"] = str(e)

    return record

def deliver_integration_event(event, queue_on_failure=True):
    """Deliver a partner event; failures are logged and never raised."""
    record = attempt_integration_delivery(event)
    if record.get("error") in ("dry_run", "disabled"):
        append_integration_log({**record, "event": event})
    else:
        append_integration_log(record)
    if queue_on_failure and not record["delivered"]:
        append_pending_integration_event(event, record["error"] or f"HTTP {record['status_code']}")

def retry_pending_integration_events(limit=25):
    """Retry pending partner events and keep failures queued."""
    pending = load_pending_integration_events()
    if not pending:
        return {"attempted": 0, "delivered": 0, "remaining": 0}

    attempted = 0
    delivered = 0
    remaining = []

    for record in pending:
        if attempted >= limit:
            remaining.append(record)
            continue

        event = record.get("event")
        if not event:
            continue

        attempted += 1
        delivery_record = attempt_integration_delivery(event)
        append_integration_log(delivery_record)
        if delivery_record["delivered"]:
            delivered += 1
        else:
            record["reason"] = delivery_record["error"] or f"HTTP {delivery_record['status_code']}"
            remaining.append(record)

    rewrite_pending_integration_events(remaining)
    return {"attempted": attempted, "delivered": delivered, "remaining": len(remaining)}

def emit_integration_event(event):
    """Send partner work in the background so racing stays responsive."""
    thread = threading.Thread(target=deliver_integration_event, args=(event,), daemon=True)
    thread.start()

def read_leaderboard_entries(limit=10):
    """Read fastest lap per driver from the existing CSV store."""
    lap_times = {}
    csv_file = 'lap_times.csv'
    if not os.path.exists(csv_file):
        return []

    try:
        with open(csv_file, 'r', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    driver_name = str(row.get('driver_name', '')).strip()
                    if not driver_name:
                        continue
                    lap_time = float(row.get('lap_time', 0))
                    if driver_name not in lap_times or lap_time < lap_times[driver_name]['lap_time']:
                        lap_times[driver_name] = {
                            'simulator_id': str(row.get('simulator_id', '')),
                            'driver_name': driver_name,
                            'lap_time': lap_time,
                            'formatted_lap_time': format_lap_time(lap_time),
                            'email': str(row.get('email', '')),
                            'timestamp': str(row.get('timestamp', ''))
                        }
                except (ValueError, TypeError):
                    continue
    except Exception as e:
        logger.warning(f"Error reading leaderboard entries: {e}")
        return []

    return sorted(lap_times.values(), key=lambda x: x['lap_time'])[:limit]

LEADERBOARD_HTML_CONTENT = r"""
<!DOCTYPE html>
<html>
<head>
    <title>Sim Coaches Leaderboard</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { box-sizing: border-box; }
        body {
            margin: 0;
            min-height: 100vh;
            background: #050505;
            color: #ffffff;
            font-family: Arial, sans-serif;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 32px;
        }
        main { width: min(1100px, 100%); }
        h1 {
            margin: 0 0 24px;
            font-size: 42px;
            font-weight: 800;
            letter-spacing: 0;
        }
        .row {
            display: grid;
            grid-template-columns: 96px 1fr 220px;
            gap: 18px;
            align-items: center;
            min-height: 72px;
            border-bottom: 1px solid rgba(255,255,255,0.14);
            font-size: 30px;
        }
        .header {
            min-height: 46px;
            color: #9ca3af;
            font-size: 16px;
            text-transform: uppercase;
            letter-spacing: 0;
        }
        .position { color: #d4af37; font-weight: 800; text-align: center; }
        .driver { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .time { color: #d4af37; font-weight: 800; text-align: right; font-variant-numeric: tabular-nums; }
        .empty { color: #9ca3af; font-size: 24px; padding: 48px 0; }
        @media (max-width: 700px) {
            body { padding: 18px; }
            h1 { font-size: 30px; }
            .row { grid-template-columns: 56px 1fr 130px; gap: 10px; min-height: 58px; font-size: 20px; }
            .header { font-size: 12px; }
        }
    </style>
</head>
<body>
    <main>
        <h1>Leaderboard</h1>
        <section class="row header">
            <div>Pos</div><div>Driver</div><div style="text-align:right;">Time</div>
        </section>
        <section id="entries"><div class="empty">Waiting for lap times...</div></section>
    </main>
    <script>
        async function refreshLeaderboard() {
            try {
                const response = await fetch('/api/leaderboard');
                const data = await response.json();
                const entries = data.entries || [];
                const container = document.getElementById('entries');
                if (!entries.length) {
                    container.innerHTML = '<div class="empty">Waiting for lap times...</div>';
                    return;
                }
                container.innerHTML = entries.map((entry, index) => `
                    <div class="row">
                        <div class="position">${index + 1}</div>
                        <div class="driver">${escapeHtml(entry.driver_name)}</div>
                        <div class="time">${escapeHtml(entry.formatted_lap_time)}</div>
                    </div>
                `).join('');
            } catch (error) {
                console.error(error);
            }
        }
        function escapeHtml(value) {
            const div = document.createElement('div');
            div.textContent = value || '';
            return div.innerHTML;
        }
        refreshLeaderboard();
        setInterval(refreshLeaderboard, 2000);
    </script>
</body>
</html>
"""

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

                        # Track as discovered simulator
                        sim_ip = addr[0]
                        sim_id = f"discovered_{sim_ip.replace('.', '_')}"
                        connected_simulators[sim_id] = {
                            'ip': sim_ip,
                            'last_seen': datetime.now(),
                            'driver': '(Discovered - awaiting data)',
                            'last_lap': None
                        }
                        print(f"[DISCOVERY] Simulator at {sim_ip} discovered and tracked")

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
            # Horizontal mode: wider panel for 1920x1080 landscape screens
            # 11 rows x 55px = 605px height, width from config (default 1200)
            panel_width = self.config.get('panel_width', 1200)
            self.leaderboard_panel.setFixedSize(panel_width, 660)
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

        # Set fixed header height based on orientation
        is_vertical = self.config.get('orientation', 'horizontal') == 'vertical'
        if is_vertical:
            self.header_widget.setFixedHeight(self.config.get('vertical_row_height', 123))
        else:
            # Horizontal mode: 660px total / 11 rows = 60px per row
            self.header_widget.setFixedHeight(60)

        header_layout = QHBoxLayout(self.header_widget)
        header_layout.setContentsMargins(30, 0, 30, 0)  # No vertical margins
        header_layout.setSpacing(20)
        header_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # Balanced widths: Position and Time equal, Driver gets the rest
        widths = [100, 484, 180]  # Position, Driver, Time
        for header, width in zip(headers, widths):
            label = QLabel(header)
            label.setFixedWidth(width)
            label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
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
        self.entries_layout.setContentsMargins(20, 0, 20, 0)  # No vertical margins for tight fit
        self.entries_layout.setSpacing(0)  # No spacing - heights are exact
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

            # Get actual screen dimensions
            screen = QApplication.primaryScreen().geometry()

            # Show fullscreen - set geometry explicitly to cover taskbar on Windows
            self.setWindowState(Qt.WindowState.WindowFullScreen)
            self.setGeometry(screen)
            self.showFullScreen()
            self.raise_()
            self.activateWindow()

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
            # Horizontal mode: 660px / 11 rows = 60px per row, no spacing
            self.entries_layout.setSpacing(0)
            self.header_widget.setFixedHeight(60)
            panel_width = self.config.get('panel_width', 1200)
            self.leaderboard_panel.setFixedSize(panel_width, 660)
        
        # Efficiently handle widget recycling
        if data:
            widths = [
                self.config.get('position_width', 120),
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
                # Horizontal mode: 660px / 11 rows = 60px per row
                row_height = 60
            
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
            # Horizontal mode: 660px / 11 rows = 60px per row
            row_height = 60

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
        
        header_font_size = self.config.get('header_font_size', 30)
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
            panel_width = self.config.get('panel_width', 1200)
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
                # Horizontal mode: wider panel for landscape screens
                panel_width = self.config.get('panel_width', 1200)
                panel_height = 660
                self.leaderboard_panel.setFixedSize(panel_width, panel_height)
            
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
        header_font_size = self.config.get('header_font_size', 30)
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
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-SimCoaches-Signature')
        self.end_headers()

    def do_POST(self):
        global queue_data
        path = self.path.split('?')[0]  # Remove query string
        print(f"[HTTP POST] Request from {self.client_address[0]} to {path}")

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
            elif path == '/api/integration/test-event':
                self.handle_integration_test_event(data)
            elif path == '/api/integration/retry-pending':
                self.handle_retry_pending_integration_events(data)
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
            driver_phone = str(lap_data.get('phone', ''))
            session_id = str(lap_data.get('session_id', ''))
        except (ValueError, TypeError):
            print(f"Invalid data format: {lap_data}")
            self.send_response(400)
            self.end_headers()
            return

        # Enrich legacy lap posts from active session tracking when available.
        client_ip = self.client_address[0]
        active_session = queue_data.get('active_sessions', {}).get(client_ip, {})
        if not session_id:
            session_id = active_session.get('session_id', '')
        if not driver_phone:
            driver_phone = active_session.get('phone', '')
        if not driver_email:
            driver_email = active_session.get('email', '')

        # Create clean data entry
        clean_data = {
            'simulator_id': simulator_id,
            'driver_name': driver_name,
            'lap_time': lap_time,
            'email': driver_email,
            'phone': driver_phone,
            'session_id': session_id,
            'timestamp': datetime.now().isoformat()
        }

        # Track connected simulator
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
            writer.writerow({
                'simulator_id': clean_data['simulator_id'],
                'driver_name': clean_data['driver_name'],
                'lap_time': clean_data['lap_time'],
                'email': clean_data['email'],
                'timestamp': clean_data['timestamp']
            })

        print(f"Successfully wrote data: {clean_data}")
        if active_session:
            best_lap = active_session.get('best_lap_time')
            if best_lap is None or lap_time < float(best_lap):
                active_session['best_lap_time'] = lap_time
                active_session['best_lap_timestamp'] = clean_data['timestamp']
                active_session['simulator_id'] = simulator_id
                active_session['email'] = driver_email
                active_session['phone'] = driver_phone
                active_session['session_id'] = session_id
                active_session['driver_name'] = driver_name
                save_queue_data()
        else:
            # Legacy direct lap submissions have no explicit session lifecycle,
            # so keep emitting a partner event per accepted lap.
            emit_integration_event(build_race_completed_event(clean_data))
        self.send_response(200)
        self.end_headers()

    def handle_integration_test_event(self, data):
        """Emit a sample partner event without touching leaderboard data."""
        sample = {
            'simulator_id': str(data.get('simulator_id', '1')),
            'driver_name': str(data.get('driver_name', 'Test Racer')),
            'lap_time': float(data.get('lap_time', 83.456)),
            'email': str(data.get('email', 'test@example.com')),
            'phone': str(data.get('phone', '+17025550123')),
            'session_id': str(data.get('session_id', f"test-{uuid.uuid4()}")),
            'timestamp': datetime.now().isoformat()
        }
        event = build_race_completed_event(sample)
        emit_integration_event(event)
        self.send_json_response({'success': True, 'event': event})

    def handle_retry_pending_integration_events(self, data):
        """Retry pending partner webhook deliveries."""
        try:
            limit = int(data.get('limit', 25))
        except (TypeError, ValueError):
            limit = 25
        result = retry_pending_integration_events(max(1, min(limit, 100)))
        self.send_json_response({'success': True, **result})

    def handle_queue_join(self, data):
        """Add a guest to the queue"""
        global queue_data
        name = str(data.get('name') or '').strip()
        email = str(data.get('email') or '').strip()
        phone = str(data.get('phone') or '').strip()

        if not name:
            self.send_json_response({'success': False, 'error': 'Name is required'}, 400)
            return

        # Generate unique ID
        queue_id = generate_queue_id()
        entry = {
            'id': queue_id,
            'name': name,
            'email': email,
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

        # Return contact info so Sender can start the simulator session with it.
        self.send_json_response({
            'success': True,
            'name': entry.get('name'),
            'email': entry.get('email', ''),
            'phone': entry.get('phone', ''),
            'simulator_ip': simulator_ip
        })

    def handle_session_started(self, data):
        """Record that a session has started on a simulator"""
        global queue_data
        simulator_ip = data.get('simulator_ip', '')
        driver_name = data.get('driver_name', '')
        queue_id = data.get('queue_id', '')
        session_id = data.get('session_id', '')
        phone = data.get('phone', '')
        email = data.get('email', '')

        if not simulator_ip:
            self.send_json_response({'success': False, 'error': 'simulator_ip is required'}, 400)
            return

        # Record active session
        queue_data['active_sessions'][simulator_ip] = {
            'started_at': int(datetime.now().timestamp()),
            'driver_name': driver_name,
            'session_id': session_id,
            'phone': phone,
            'email': email,
            'best_lap_time': None,
            'best_lap_timestamp': None,
            'simulator_id': ''
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
        session = None
        if simulator_ip in queue_data['active_sessions']:
            session = queue_data['active_sessions'].pop(simulator_ip)

        if session and session.get('best_lap_time') is not None:
            event_data = {
                'simulator_id': session.get('simulator_id', ''),
                'driver_name': session.get('driver_name', ''),
                'lap_time': session.get('best_lap_time'),
                'email': session.get('email', ''),
                'phone': session.get('phone', ''),
                'session_id': session.get('session_id', ''),
                'timestamp': session.get('best_lap_timestamp') or datetime.now().isoformat()
            }
            emit_integration_event(build_race_completed_event(event_data))

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
        print(f"[HTTP GET] Request from {client_ip} to {path}")

        # Route to appropriate handler
        if path == '/api/queue':
            self.handle_get_queue()
            return
        elif path == '/api/queue/stats':
            self.handle_get_stats()
            return
        elif path == '/api/integration/config':
            self.handle_get_integration_config()
            return
        elif path == '/api/leaderboard':
            self.handle_get_leaderboard()
            return
        elif path == '/leaderboard':
            self.handle_leaderboard_page()
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

    def handle_get_leaderboard(self):
        """Return read-only leaderboard data for browser displays."""
        self.send_json_response({
            'success': True,
            'entries': read_leaderboard_entries()
        })

    def handle_leaderboard_page(self):
        """Serve a browser-friendly mirror of the local leaderboard."""
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(LEADERBOARD_HTML_CONTENT.encode('utf-8'))

    def handle_get_integration_config(self):
        """Return non-secret partner integration status."""
        config = load_integration_config()
        pending_count = len(load_pending_integration_events())
        self.send_json_response({
            'success': True,
            'enabled': bool(config.get('enabled')),
            'dry_run': bool(config.get('dry_run')),
            'webhook_configured': bool(config.get('webhook_url')),
            'api_key_configured': bool(config.get('api_key')),
            'signing_secret_configured': bool(config.get('signing_secret')),
            'demo_url': config.get('demo_url', ''),
            'pending_events': pending_count
        })

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
            'header_font_size': 30,
            'entry_font_size': 30,
            'p1_font_size': 30,    # First place font size
            'p2_font_size': 30,    # Second place font size
            'p3_font_size': 30,    # Third place font size
            'other_font_size': 30, # Other positions font size
            'background_image': '',
            'fill_screen': False,   # Whether to fill the entire screen with background
            'position_width': 120,    # Default width for position column
            'driver_width': 400,     # Default width for driver name column
            'time_width': 200,       # Default width for time column
            'panel_width': 1200,     # Default width for horizontal mode panel
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
        load_integration_config()
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
        self.setGeometry(100, 100, 720, 700)
        self.setFixedSize(720, 700)

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
                font-size: 13px;
                color: #cccccc;
                background-color: transparent;
            }
            QLineEdit, QComboBox {
                padding: 4px 8px;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
                background-color: #2d2d30;
                color: #cccccc;
                min-height: 20px;
            }
            QLineEdit:focus, QComboBox:focus {
                border-color: #007acc;
            }
            QPushButton {
                padding: 8px 16px;
                background-color: #007acc;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 13px;
                min-height: 32px;
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
                padding: 8px 16px;
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
        """)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        # Card styling
        CARD_STYLE = """
            QFrame {
                background-color: #252526;
                border: 1px solid #3c3c3c;
                border-radius: 8px;
            }
        """
        CARD_HEADER_STYLE = "font-weight: bold; color: #007acc; font-size: 12px; background: transparent; border: none;"

        # Show Leaderboard button - prominent, full width
        self.show_leaderboard_btn = QPushButton("Show Leaderboard")
        self.show_leaderboard_btn.setStyleSheet("""
            QPushButton {
                background-color: #007acc;
                font-size: 16px;
                padding: 14px 20px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #005a9e;
            }
        """)
        self.show_leaderboard_btn.clicked.connect(self.toggle_leaderboard)
        layout.addWidget(self.show_leaderboard_btn)

        # Cards row - horizontal layout
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(10)

        # === SERVER CARD ===
        server_card = QFrame()
        server_card.setStyleSheet(CARD_STYLE)
        server_card_layout = QVBoxLayout(server_card)
        server_card_layout.setContentsMargins(12, 10, 12, 10)
        server_card_layout.setSpacing(6)

        server_header = QLabel("SERVER")
        server_header.setStyleSheet(CARD_HEADER_STYLE)
        server_card_layout.addWidget(server_header)

        # Status with indicator dot
        status_row = QHBoxLayout()
        status_row.setSpacing(6)
        self.server_status_dot = QLabel("\u25CF")  # Unicode circle
        self.server_status_dot.setStyleSheet("color: #888888; font-size: 10px; background: transparent; border: none;")
        status_row.addWidget(self.server_status_dot)
        self.server_status_label = QLabel("Stopped")
        self.server_status_label.setStyleSheet("color: #888888; font-size: 12px; background: transparent; border: none;")
        status_row.addWidget(self.server_status_label)
        status_row.addStretch()
        server_card_layout.addLayout(status_row)

        # Discovery status
        self.discovery_status_label = QLabel("Discovery: Inactive")
        self.discovery_status_label.setStyleSheet("color: #4ec9b0; font-size: 11px; font-style: italic; background: transparent; border: none;")
        server_card_layout.addWidget(self.discovery_status_label)

        # Port display
        port_row = QHBoxLayout()
        port_row.setSpacing(4)
        port_label = QLabel("Port:")
        port_label.setStyleSheet("color: #888888; font-size: 11px; background: transparent; border: none;")
        port_row.addWidget(port_label)
        self.server_port_display = QLabel(str(self.config.get('server_port', 5000)))
        self.server_port_display.setStyleSheet("color: #cccccc; font-size: 11px; background: transparent; border: none;")
        port_row.addWidget(self.server_port_display)
        port_row.addStretch()
        server_card_layout.addLayout(port_row)

        server_card_layout.addStretch()

        # Start/Stop button
        self.server_toggle_btn = QPushButton("Start Server")
        self.server_toggle_btn.setStyleSheet("""
            QPushButton {
                background-color: #4ec9b0;
                color: #1e1e1e;
                font-size: 12px;
                padding: 6px 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #3cb99f;
            }
        """)
        self.server_toggle_btn.clicked.connect(self.toggle_server)
        server_card_layout.addWidget(self.server_toggle_btn)

        cards_layout.addWidget(server_card)

        # === EVENT CARD ===
        event_card = QFrame()
        event_card.setStyleSheet(CARD_STYLE)
        event_card_layout = QVBoxLayout(event_card)
        event_card_layout.setContentsMargins(12, 10, 12, 10)
        event_card_layout.setSpacing(6)

        event_header = QLabel("EVENT")
        event_header.setStyleSheet(CARD_HEADER_STYLE)
        event_card_layout.addWidget(event_header)

        event_card_layout.addStretch()

        # Export & End button
        export_btn = QPushButton("Export && End Event")
        export_btn.setStyleSheet("""
            QPushButton {
                background-color: #4ec9b0;
                color: #1e1e1e;
                font-size: 12px;
                padding: 6px 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #3cb99f;
            }
        """)
        export_btn.setToolTip("Save lap times to a named file and clear the leaderboard")
        export_btn.clicked.connect(self.export_and_end_event)
        event_card_layout.addWidget(export_btn)

        # Clear Only button
        clear_btn = QPushButton("Clear Only")
        clear_btn.setStyleSheet("""
            QPushButton {
                background-color: #f48771;
                color: #1e1e1e;
                font-size: 12px;
                padding: 6px 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #d96a56;
            }
        """)
        clear_btn.setToolTip("Clear the leaderboard without saving")
        clear_btn.clicked.connect(self.clear_leaderboard)
        event_card_layout.addWidget(clear_btn)

        cards_layout.addWidget(event_card)

        # === DISPLAY CARD ===
        display_card = QFrame()
        display_card.setStyleSheet(CARD_STYLE)
        display_card_layout = QVBoxLayout(display_card)
        display_card_layout.setContentsMargins(12, 10, 12, 10)
        display_card_layout.setSpacing(6)

        display_header = QLabel("DISPLAY")
        display_header.setStyleSheet(CARD_HEADER_STYLE)
        display_card_layout.addWidget(display_header)

        # Resolution info
        orientation = self.config.get('orientation', 'horizontal')
        res_text = "1920x1080" if orientation == 'horizontal' else "1080x1920"
        self.resolution_label = QLabel(res_text)
        self.resolution_label.setStyleSheet("color: #888888; font-size: 11px; background: transparent; border: none;")
        display_card_layout.addWidget(self.resolution_label)

        # Position controls - arrow buttons
        position_label = QLabel("Position:")
        position_label.setStyleSheet("color: #888888; font-size: 10px; margin-top: 4px; background: transparent; border: none;")
        display_card_layout.addWidget(position_label)

        # Arrow button style
        arrow_btn_style = """
            QPushButton {
                background-color: #3c3c3c;
                color: white;
                font-size: 14px;
                font-weight: bold;
                padding: 2px;
                min-width: 28px;
                max-width: 28px;
                min-height: 22px;
                max-height: 22px;
            }
            QPushButton:hover {
                background-color: #505050;
            }
        """

        # Top row: Up button centered
        top_arrow_row = QHBoxLayout()
        top_arrow_row.setSpacing(2)
        top_arrow_row.addStretch()
        self.move_up_btn = QPushButton("▲")
        self.move_up_btn.setStyleSheet(arrow_btn_style)
        self.move_up_btn.clicked.connect(self.move_leaderboard_up)
        top_arrow_row.addWidget(self.move_up_btn)
        top_arrow_row.addStretch()
        display_card_layout.addLayout(top_arrow_row)

        # Middle row: Left and Right buttons
        mid_arrow_row = QHBoxLayout()
        mid_arrow_row.setSpacing(2)
        mid_arrow_row.addStretch()
        self.move_left_btn = QPushButton("◀")
        self.move_left_btn.setStyleSheet(arrow_btn_style)
        self.move_left_btn.clicked.connect(self.move_leaderboard_left)
        mid_arrow_row.addWidget(self.move_left_btn)
        self.move_right_btn = QPushButton("▶")
        self.move_right_btn.setStyleSheet(arrow_btn_style)
        self.move_right_btn.clicked.connect(self.move_leaderboard_right)
        mid_arrow_row.addWidget(self.move_right_btn)
        mid_arrow_row.addStretch()
        display_card_layout.addLayout(mid_arrow_row)

        # Bottom row: Down button centered
        bottom_arrow_row = QHBoxLayout()
        bottom_arrow_row.setSpacing(2)
        bottom_arrow_row.addStretch()
        self.move_down_btn = QPushButton("▼")
        self.move_down_btn.setStyleSheet(arrow_btn_style)
        self.move_down_btn.clicked.connect(self.move_leaderboard_down)
        bottom_arrow_row.addWidget(self.move_down_btn)
        bottom_arrow_row.addStretch()
        display_card_layout.addLayout(bottom_arrow_row)

        display_card_layout.addStretch()

        # Orientation toggle button
        self.orientation_toggle = QPushButton(orientation.capitalize())
        self.orientation_toggle.setStyleSheet("""
            QPushButton {
                background-color: #007acc;
                padding: 6px 12px;
                color: white;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #005a9e;
            }
        """)
        self.orientation_toggle.clicked.connect(self.toggle_orientation)
        display_card_layout.addWidget(self.orientation_toggle)

        cards_layout.addWidget(display_card)

        layout.addLayout(cards_layout)

        # Hidden server URL entry (for compatibility)
        self.server_url_entry = QLineEdit(self.config.get('server_url', 'http://localhost:5000'))
        self.server_url_entry.setVisible(False)
        layout.addWidget(self.server_url_entry)

        # Hidden server port entry (for compatibility with save_settings)
        self.server_port_entry = QLineEdit(str(self.config.get('server_port', 5000)))
        self.server_port_entry.setVisible(False)
        layout.addWidget(self.server_port_entry)

        # Tabs for settings
        tabs = QTabWidget()
        layout.addWidget(tabs)

        # === GENERAL TAB ===
        general_tab = QWidget()
        general_layout = QVBoxLayout(general_tab)
        general_layout.setSpacing(8)
        general_layout.setContentsMargins(12, 12, 12, 12)

        # Refresh interval row
        refresh_row = QHBoxLayout()
        refresh_row.setSpacing(8)
        refresh_label = QLabel("Refresh Interval (seconds):")
        refresh_row.addWidget(refresh_label)
        self.refresh_interval = QLineEdit(str(self.config.get('refresh_interval', 5)))
        self.refresh_interval.setMaxLength(3)
        self.refresh_interval.setFixedWidth(60)
        refresh_row.addWidget(self.refresh_interval)
        refresh_row.addStretch()
        general_layout.addLayout(refresh_row)

        # Connected Simulators section - always visible
        sim_header_row = QHBoxLayout()
        sim_header_row.setSpacing(8)
        sim_label = QLabel("Connected Devices:")
        sim_label.setStyleSheet("font-weight: bold; color: #007acc;")
        sim_header_row.addWidget(sim_label)
        sim_header_row.addStretch()

        clear_sim_btn = QPushButton("Clear All")
        clear_sim_btn.setStyleSheet("""
            QPushButton {
                background-color: #f48771;
                color: #1e1e1e;
                border: none;
                padding: 4px 12px;
                border-radius: 3px;
                font-size: 11px;
                font-weight: bold;
                min-height: 24px;
            }
            QPushButton:hover {
                background-color: #d96a56;
            }
        """)
        clear_sim_btn.clicked.connect(self.clear_simulators)
        sim_header_row.addWidget(clear_sim_btn)
        general_layout.addLayout(sim_header_row)

        # Simulators display - always visible
        self.simulators_display = QLabel("No devices connected\n\nDevices will appear here when they connect to the server.")
        self.simulators_display.setStyleSheet("""
            QLabel {
                background-color: #252526;
                color: #888888;
                font-family: Consolas, monospace;
                font-size: 12px;
                padding: 12px;
                border-radius: 4px;
                border: 1px solid #3c3c3c;
            }
        """)
        self.simulators_display.setWordWrap(True)
        self.simulators_display.setMinimumHeight(100)
        general_layout.addWidget(self.simulators_display, 1)  # stretch factor 1 to fill space

        # Timer to refresh connected simulators display
        self.sim_refresh_timer = QTimer()
        self.sim_refresh_timer.timeout.connect(self.update_simulators_display)
        self.sim_refresh_timer.start(2000)

        tabs.addTab(general_tab, "General")

        # === APPEARANCE TAB ===
        appearance_tab = QWidget()
        appearance_main = QVBoxLayout(appearance_tab)
        appearance_main.setSpacing(8)
        appearance_main.setContentsMargins(12, 10, 12, 10)

        # Background row (full width)
        bg_row = QHBoxLayout()
        bg_row.setSpacing(8)
        bg_label = QLabel("Background:")
        bg_label.setFixedWidth(90)
        bg_row.addWidget(bg_label)
        self.bg_path_entry = QLineEdit(self.config.get('background_image', ''))
        self.bg_path_entry.setReadOnly(True)
        self.bg_path_entry.setPlaceholderText("No image selected")
        bg_row.addWidget(self.bg_path_entry, 1)
        browse_btn = QPushButton("Browse")
        browse_btn.setStyleSheet("""
            QPushButton {
                background-color: #3c3c3c;
                padding: 4px 12px;
                min-height: 26px;
            }
            QPushButton:hover {
                background-color: #505050;
            }
        """)
        browse_btn.clicked.connect(self.choose_background)
        browse_btn.setFixedWidth(70)
        bg_row.addWidget(browse_btn)
        appearance_main.addLayout(bg_row)

        # Two columns layout
        columns = QHBoxLayout()
        columns.setSpacing(20)

        # Helper function to create a setting row
        def make_row(label_text, widget, label_width=100):
            row = QHBoxLayout()
            row.setSpacing(6)
            label = QLabel(label_text)
            label.setFixedWidth(label_width)
            row.addWidget(label)
            row.addWidget(widget)
            row.addStretch()
            return row

        # Left column
        left_col = QVBoxLayout()
        left_col.setSpacing(12)

        self.fill_toggle = QPushButton(self.config.get('fill_screen', False) and "Fill Screen" or "Keep Aspect")
        self.fill_toggle.setStyleSheet("""
            QPushButton {
                background-color: #3c3c3c;
                padding: 4px 10px;
                min-height: 26px;
            }
            QPushButton:hover {
                background-color: #505050;
            }
        """)
        self.fill_toggle.setFixedWidth(100)
        self.fill_toggle.clicked.connect(self.toggle_fill_mode)
        left_col.addLayout(make_row("Fill Mode:", self.fill_toggle))

        self.opacity_entry = QLineEdit(str(self.config.get('opacity', 220)))
        self.opacity_entry.setMaxLength(3)
        self.opacity_entry.setFixedWidth(60)
        left_col.addLayout(make_row("Box Opacity:", self.opacity_entry))

        self.header_font_size = QLineEdit(str(self.config.get('header_font_size', 30)))
        self.header_font_size.setMaxLength(2)
        self.header_font_size.setFixedWidth(60)
        left_col.addLayout(make_row("Header Font:", self.header_font_size))

        self.entry_font_size = QLineEdit(str(self.config.get('entry_font_size', 30)))
        self.entry_font_size.setMaxLength(2)
        self.entry_font_size.setFixedWidth(60)
        left_col.addLayout(make_row("Entry Font:", self.entry_font_size))

        self.p1_font_size = QLineEdit(str(self.config.get('p1_font_size', 30)))
        self.p1_font_size.setMaxLength(2)
        self.p1_font_size.setFixedWidth(60)
        left_col.addLayout(make_row("1st Place:", self.p1_font_size))

        self.p2_font_size = QLineEdit(str(self.config.get('p2_font_size', 30)))
        self.p2_font_size.setMaxLength(2)
        self.p2_font_size.setFixedWidth(60)
        left_col.addLayout(make_row("2nd Place:", self.p2_font_size))

        self.p3_font_size = QLineEdit(str(self.config.get('p3_font_size', 30)))
        self.p3_font_size.setMaxLength(2)
        self.p3_font_size.setFixedWidth(60)
        left_col.addLayout(make_row("3rd Place:", self.p3_font_size))

        left_col.addStretch()
        columns.addLayout(left_col)

        # Right column
        right_col = QVBoxLayout()
        right_col.setSpacing(12)

        self.other_font_size = QLineEdit(str(self.config.get('other_font_size', 30)))
        self.other_font_size.setMaxLength(2)
        self.other_font_size.setFixedWidth(60)
        right_col.addLayout(make_row("Other Font:", self.other_font_size))

        self.position_width = QLineEdit(str(self.config.get('position_width', 120)))
        self.position_width.setMaxLength(3)
        self.position_width.setFixedWidth(60)
        right_col.addLayout(make_row("Position Width:", self.position_width))

        self.driver_width = QLineEdit(str(self.config.get('driver_width', 400)))
        self.driver_width.setMaxLength(3)
        self.driver_width.setFixedWidth(60)
        right_col.addLayout(make_row("Driver Width:", self.driver_width))

        self.time_width = QLineEdit(str(self.config.get('time_width', 200)))
        self.time_width.setMaxLength(3)
        self.time_width.setFixedWidth(60)
        right_col.addLayout(make_row("Time Width:", self.time_width))

        self.panel_width = QLineEdit(str(self.config.get('panel_width', 1200)))
        self.panel_width.setMaxLength(4)
        self.panel_width.setFixedWidth(60)
        right_col.addLayout(make_row("Panel Width:", self.panel_width))

        self.vertical_spacing = QLineEdit(str(self.config.get('vertical_spacing', 4)))
        self.vertical_spacing.setMaxLength(2)
        self.vertical_spacing.setFixedWidth(60)
        right_col.addLayout(make_row("Vert. Spacing:", self.vertical_spacing))

        self.row_height_padding = QLineEdit(str(self.config.get('row_height_padding', 16)))
        self.row_height_padding.setMaxLength(2)
        self.row_height_padding.setFixedWidth(60)
        right_col.addLayout(make_row("Row Padding:", self.row_height_padding))

        right_col.addStretch()
        columns.addLayout(right_col)

        appearance_main.addLayout(columns)
        appearance_main.addStretch()

        tabs.addTab(appearance_tab, "Appearance")

        # Save Settings button - outside tabs
        save_btn = QPushButton("Save Settings")
        save_btn.setStyleSheet("""
            QPushButton {
                background-color: #007acc;
                font-size: 14px;
                padding: 10px;
            }
            QPushButton:hover {
                background-color: #005a9e;
            }
        """)
        save_btn.clicked.connect(self.save_settings)
        layout.addWidget(save_btn)
    
    def create_leaderboard_window(self):
        if self.leaderboard_window is None:
            # Create as a separate window without parent
            self.leaderboard_window = LeaderboardWindow(self.config, parent=None)
            # Apply background image with delay to ensure window is fully initialized
            if self.config.get('background_image'):
                QTimer.singleShot(200, lambda: self._apply_background_image())

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
                self.leaderboard_window.update_entries([])

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
                self.leaderboard_window.update_entries([])

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
            self.resolution_label.setText("1080x1920")
        else:
            self.config['orientation'] = 'horizontal'
            self.orientation_toggle.setText("Horizontal")
            self.resolution_label.setText("1920x1080")

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
                # Horizontal mode: wider panel for 1920x1080 landscape screens
                panel_width = self.config.get('panel_width', 1200)
                self.leaderboard_window.leaderboard_panel.setFixedSize(panel_width, 660)

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

    def move_leaderboard_up(self):
        """Move the leaderboard panel up"""
        if self.leaderboard_window:
            self.leaderboard_window.move_up()

    def move_leaderboard_down(self):
        """Move the leaderboard panel down"""
        if self.leaderboard_window:
            self.leaderboard_window.move_down()

    def move_leaderboard_left(self):
        """Move the leaderboard panel left"""
        if self.leaderboard_window:
            self.leaderboard_window.move_left()

    def move_leaderboard_right(self):
        """Move the leaderboard panel right"""
        if self.leaderboard_window:
            self.leaderboard_window.move_right()

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
                panel_width = self.config.get('panel_width', 1200)
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
        # Remove offline simulators (not seen for more than 5 minutes)
        now = datetime.now()
        offline_sims = [sim_id for sim_id, info in connected_simulators.items()
                       if (now - info['last_seen']).total_seconds() > 300]
        for sim_id in offline_sims:
            del connected_simulators[sim_id]

        if not connected_simulators:
            self.simulators_display.setText("No devices connected\n\nDevices will appear here when they connect to the server.")
            self.simulators_display.setStyleSheet("""
                QLabel {
                    background-color: #252526;
                    color: #888888;
                    font-family: Consolas, monospace;
                    font-size: 12px;
                    padding: 12px;
                    border-radius: 4px;
                    border: 1px solid #3c3c3c;
                }
            """)
            return

        # Build display text
        lines = []
        active_count = 0

        for sim_id, info in connected_simulators.items():
            time_diff = (now - info['last_seen']).total_seconds()

            # Consider simulator "active" if seen in last 60 seconds
            if time_diff < 60:
                status = "\u25CF ACTIVE"  # Green dot
                active_count += 1
            elif time_diff < 300:
                status = "\u25CB IDLE"    # White circle

            # Format last lap time (handle None for ping-only connections)
            lap_time = info.get('last_lap')
            if lap_time is not None:
                lap_str = f"{int(lap_time // 60):02d}:{lap_time % 60:06.3f}"
            else:
                lap_str = "--:--"

            driver = info.get('driver', 'Unknown')
            lines.append(f"{status}  {driver}")
            lines.append(f"      Sim {sim_id} ({info['ip']})  |  Last: {lap_str}")

        display_text = "\n".join(lines)
        self.simulators_display.setText(display_text)

        # Update color based on active simulators
        if active_count > 0:
            self.simulators_display.setStyleSheet("""
                QLabel {
                    background-color: #1a2d1a;
                    color: #4ec9b0;
                    font-family: Consolas, monospace;
                    font-size: 12px;
                    padding: 12px;
                    border-radius: 4px;
                    border: 1px solid #2d5a2d;
                }
            """)
        else:
            self.simulators_display.setStyleSheet("""
                QLabel {
                    background-color: #2d2d2d;
                    color: #d4a54a;
                    font-family: Consolas, monospace;
                    font-size: 12px;
                    padding: 12px;
                    border-radius: 4px;
                    border: 1px solid #3c3c3c;
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
                # Restore control window when hiding leaderboard
                self.showNormal()
                self.activateWindow()
            else:
                self.leaderboard_window.show()
                self.leaderboard_window.activateWindow()
                self.leaderboard_window.raise_()
                self.show_leaderboard_btn.setText("Hide Leaderboard")
                # Re-apply background image when showing (ensures it persists after restart)
                # Use multiple attempts to ensure it loads reliably
                if self.config.get('background_image'):
                    QTimer.singleShot(100, self._apply_background_image)
                    QTimer.singleShot(500, self._apply_background_image)
            
    def update_leaderboard(self, data):
        if self.leaderboard_window:
            self.leaderboard_window.update_entries(data)

    def _apply_background_image(self):
        """Apply background image to leaderboard window - helper for reliable loading"""
        if self.leaderboard_window and self.config.get('background_image'):
            bg_path = self.config['background_image']
            if os.path.exists(bg_path):
                self.leaderboard_window.set_background(bg_path)
                print(f"[BACKGROUND] Applied: {bg_path}")

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

            self.server_status_label.setText("Stopped")
            self.server_status_label.setStyleSheet("color: #888888; font-size: 12px; background: transparent; border: none;")
            self.server_status_dot.setStyleSheet("color: #888888; font-size: 10px; background: transparent; border: none;")
            self.discovery_status_label.setText("Discovery: Inactive")
            self.server_toggle_btn.setText("Start Server")
            self.server_toggle_btn.setStyleSheet("""
                QPushButton {
                    background-color: #4ec9b0;
                    color: #1e1e1e;
                    font-size: 12px;
                    padding: 6px 12px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #3cb99f;
                }
            """)
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
                self.server_port_display.setText(str(port))

                # Save config to persist port change
                with open('config.json', 'w') as f:
                    json.dump(self.config, f)

                self.server_status_label.setText("Running")
                self.server_status_label.setStyleSheet("color: #4ec9b0; font-size: 12px; background: transparent; border: none;")
                self.server_status_dot.setStyleSheet("color: #4ec9b0; font-size: 10px; background: transparent; border: none;")
                self.discovery_status_label.setText(f"Discovery: Active (UDP {DISCOVERY_PORT})")
                self.server_toggle_btn.setText("Stop Server")
                self.server_toggle_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #f48771;
                        color: #1e1e1e;
                        font-size: 12px;
                        padding: 6px 12px;
                        font-weight: bold;
                    }
                    QPushButton:hover {
                        background-color: #d96a56;
                    }
                """)
                self.statusBar().showMessage(f"Lap time server started on port {port}")
            except Exception as e:
                self.server_status_label.setText(f"Error: {str(e)[:20]}")
                self.server_status_label.setStyleSheet("color: #f48771; font-size: 12px; background: transparent; border: none;")
                self.server_status_dot.setStyleSheet("color: #f48771; font-size: 10px; background: transparent; border: none;")
                self.statusBar().showMessage(f"Error starting lap time server: {str(e)}")

    def start_lap_time_server(self):
        """Start HTTP server to receive lap times"""
        try:
            # Load queue data from file
            load_queue_data()

            # SMS is now handled by Sender - no need to load SMS config here

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

            self.server_status_label.setText("Running")
            self.server_status_label.setStyleSheet("color: #4ec9b0; font-size: 12px; background: transparent; border: none;")
            self.server_status_dot.setStyleSheet("color: #4ec9b0; font-size: 10px; background: transparent; border: none;")
            self.server_port_display.setText(str(port))
            self.discovery_status_label.setText(f"Discovery: Active (UDP {DISCOVERY_PORT})")
            self.server_toggle_btn.setText("Stop Server")
            self.server_toggle_btn.setStyleSheet("""
                QPushButton {
                    background-color: #f48771;
                    color: #1e1e1e;
                    font-size: 12px;
                    padding: 6px 12px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #d96a56;
                }
            """)
            self.statusBar().showMessage(f"Lap time server started on port {port}")
        except Exception as e:
            self.server_status_label.setText(f"Error: {str(e)[:20]}")
            self.server_status_label.setStyleSheet("color: #f48771; font-size: 12px; background: transparent; border: none;")
            self.server_status_dot.setStyleSheet("color: #f48771; font-size: 10px; background: transparent; border: none;")
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
