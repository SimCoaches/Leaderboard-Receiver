# Multi-Receiver Sender Implementation Instructions

## Overview

This version supports **multiple Leaderboard Receivers** on the same network. Whether you have 1, 2, or 10 receivers running, the sender will:
- Automatically discover ALL receivers
- Send lap times to ALL receivers simultaneously
- Auto-reconnect to any receiver that comes back online

## Protocol (Same as Before)

```
1. Sender broadcasts: "LEADERBOARD_DISCOVER" to UDP port 5001
2. ALL Receivers respond: "LEADERBOARD_SERVER:192.168.0.xxx:5000"
3. Sender collects ALL responses (waits 2 seconds)
4. Sender sends lap times to ALL discovered receivers
```

---

## Complete Implementation

### sender_discovery.py (Multi-Receiver Version)

```python
"""
LEADERBOARD SENDER - MULTI-RECEIVER AUTO-DISCOVERY
===================================================

Supports multiple receivers on the same network.
Automatically discovers and sends to ALL receivers.
"""

import socket
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import requests
except ImportError:
    print("ERROR: 'requests' library required. Install with: pip install requests")
    raise


# =============================================================================
# DISCOVERY CONSTANTS
# =============================================================================

DISCOVERY_PORT = 5001
DISCOVERY_REQUEST = b"LEADERBOARD_DISCOVER"
DISCOVERY_RESPONSE_PREFIX = "LEADERBOARD_SERVER:"
DISCOVERY_WAIT_TIME = 2  # Wait 2 seconds to collect ALL responses


# =============================================================================
# MULTI-RECEIVER DISCOVERY FUNCTION
# =============================================================================

def discover_all_leaderboard_servers():
    """
    Broadcast on the local network to find ALL Leaderboard Receivers.
    Waits DISCOVERY_WAIT_TIME seconds to collect all responses.

    Returns:
        list: List of server URLs (e.g., ["http://192.168.0.10:5000", "http://192.168.0.11:5000"])
              Empty list if no receivers found
    """
    servers = []
    seen_addresses = set()  # Avoid duplicates

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.5)  # Short timeout for each recv attempt

    try:
        # Send broadcast to all devices on network
        sock.sendto(DISCOVERY_REQUEST, ('<broadcast>', DISCOVERY_PORT))

        # Collect responses for DISCOVERY_WAIT_TIME seconds
        end_time = time.time() + DISCOVERY_WAIT_TIME

        while time.time() < end_time:
            try:
                data, addr = sock.recvfrom(1024)
                response = data.decode()

                # Parse response: "LEADERBOARD_SERVER:192.168.0.248:5000"
                if response.startswith(DISCOVERY_RESPONSE_PREFIX):
                    server_info = response.replace(DISCOVERY_RESPONSE_PREFIX, "")

                    # Avoid duplicates (same server responding multiple times)
                    if server_info not in seen_addresses:
                        seen_addresses.add(server_info)
                        ip, port = server_info.rsplit(":", 1)
                        url = f"http://{ip}:{port}"
                        servers.append(url)
                        print(f"[DISCOVERY] Found receiver: {url}")

            except socket.timeout:
                continue  # Keep waiting until end_time

    except Exception as e:
        print(f"[DISCOVERY] Error: {e}")
    finally:
        sock.close()

    print(f"[DISCOVERY] Found {len(servers)} receiver(s) total")
    return servers


# =============================================================================
# MULTI-RECEIVER CONNECTION MANAGER
# =============================================================================

class MultiReceiverConnection:
    """
    Manages connections to MULTIPLE Leaderboard Receivers.

    Features:
    - Discovers ALL receivers on the network automatically
    - Sends lap times to ALL receivers simultaneously
    - Auto-reconnects if receivers go offline/online
    - Manual backup URLs (just in case)
    """

    def __init__(self):
        self.server_urls = []            # List of discovered server URLs
        self.manual_urls = []            # Manual backup URLs
        self.last_discovery_time = 0
        self.connected_count = 0
        self._lock = threading.Lock()

        # Callbacks for UI updates (optional)
        self.on_status_change = None     # Called with status string

    def add_manual_backup(self, url):
        """
        Add a manual backup URL (just in case auto-discovery fails).

        Args:
            url: Full URL or IP:port, e.g., "192.168.0.248:5000"
        """
        if url and not url.startswith("http"):
            url = f"http://{url}"
        if url and url not in self.manual_urls:
            self.manual_urls.append(url)

    def set_manual_backups(self, urls):
        """
        Set multiple manual backup URLs at once.

        Args:
            urls: List of URLs or IP:port strings
        """
        self.manual_urls = []
        for url in urls:
            self.add_manual_backup(url)

    def discover(self):
        """
        Discover ALL Leaderboard Receivers on the network.

        Returns:
            int: Number of receivers found
        """
        with self._lock:
            self._update_status("Searching for receivers...")

            discovered = discover_all_leaderboard_servers()

            if discovered:
                self.server_urls = discovered
                self.connected_count = len(discovered)
                self.last_discovery_time = time.time()
                self._update_status(f"Found {len(discovered)} receiver(s)")
                return len(discovered)
            else:
                # Fall back to manual URLs if no discovery
                if self.manual_urls:
                    self.server_urls = self.manual_urls.copy()
                    self._update_status(f"Using {len(self.manual_urls)} manual URL(s)")
                    return len(self.manual_urls)
                else:
                    self._update_status("No receivers found")
                    return 0

    def ensure_connected(self):
        """
        Make sure we have at least one server URL.

        Returns:
            bool: True if we have URLs to send to
        """
        if self.server_urls:
            return True

        return self.discover() > 0

    def send_lap_time(self, simulator_id, driver_name, lap_time, email=""):
        """
        Send a lap time to ALL Leaderboard Receivers.
        Sends in parallel for speed.

        Args:
            simulator_id: Unique ID for this simulator
            driver_name: Driver's name
            lap_time: Lap time in seconds (float)
            email: Optional email address

        Returns:
            dict: Results for each server {"url": True/False, ...}
        """
        if not self.ensure_connected():
            return {}

        data = {
            "simulator_id": simulator_id,
            "driver_name": driver_name,
            "lap_time": lap_time,
            "email": email
        }

        results = {}
        failed_urls = []

        # Send to all servers in parallel
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {
                executor.submit(self._send_to_server, url, data): url
                for url in self.server_urls
            }

            for future in as_completed(futures, timeout=10):
                url = futures[future]
                try:
                    success = future.result()
                    results[url] = success
                    if not success:
                        failed_urls.append(url)
                except Exception as e:
                    results[url] = False
                    failed_urls.append(url)
                    print(f"[SEND] Error sending to {url}: {e}")

        # Update connected count
        self.connected_count = sum(1 for v in results.values() if v)

        # Log results
        success_count = sum(1 for v in results.values() if v)
        print(f"[SEND] Sent to {success_count}/{len(results)} receivers")

        # If some failed, try re-discovery next time
        if failed_urls:
            self._update_status(f"Connected to {self.connected_count} receiver(s)")

        return results

    def _send_to_server(self, url, data):
        """Send data to a single server. Returns True on success."""
        try:
            response = requests.post(url, json=data, timeout=5)
            return response.status_code == 200
        except:
            return False

    def _update_status(self, status):
        """Update status and trigger callback."""
        print(f"[STATUS] {status}")
        if self.on_status_change:
            self.on_status_change(status)

    def get_status(self):
        """Get current status string for display."""
        if not self.server_urls:
            return "No receivers found"
        elif self.connected_count == len(self.server_urls):
            return f"Connected to {self.connected_count} receiver(s)"
        else:
            return f"Connected to {self.connected_count}/{len(self.server_urls)} receiver(s)"

    def get_server_list(self):
        """Get list of currently known server URLs."""
        return self.server_urls.copy()


# =============================================================================
# BACKGROUND DISCOVERY THREAD
# =============================================================================

class BackgroundDiscovery(threading.Thread):
    """
    Runs discovery in background, keeps connections alive.
    Periodically re-discovers to find new receivers or reconnect to lost ones.
    """

    def __init__(self, connection: MultiReceiverConnection):
        super().__init__(daemon=True)
        self.connection = connection
        self.running = True
        self.check_interval = 15  # Re-discover every 15 seconds

    def run(self):
        # Discover immediately on start
        self.connection.discover()

        while self.running:
            time.sleep(self.check_interval)

            # Re-discover periodically to find new receivers
            # or reconnect to ones that went offline
            self.connection.discover()

    def stop(self):
        """Stop the background discovery thread."""
        self.running = False


# =============================================================================
# USAGE EXAMPLE
# =============================================================================

def example_usage():
    """Complete example with multiple receivers."""

    # 1. Create multi-receiver connection manager
    leaderboard = MultiReceiverConnection()

    # 2. Optional: Add manual backup URLs (the old IP entries)
    leaderboard.add_manual_backup("192.168.0.10:5000")
    leaderboard.add_manual_backup("192.168.0.11:5000")

    # 3. Optional: Set up status callback for UI
    def on_status_change(status):
        print(f"UI UPDATE: {status}")
        # Update your UI label here

    leaderboard.on_status_change = on_status_change

    # 4. Start background discovery
    discovery = BackgroundDiscovery(leaderboard)
    discovery.start()

    # 5. Wait for initial discovery
    time.sleep(3)

    # 6. Send a lap time (goes to ALL receivers)
    results = leaderboard.send_lap_time(
        simulator_id="sim_001",
        driver_name="Test Driver",
        lap_time=123.456
    )

    print(f"Results: {results}")
    print(f"Status: {leaderboard.get_status()}")
    print(f"Servers: {leaderboard.get_server_list()}")

    # 7. Cleanup
    discovery.stop()


# =============================================================================
# QUICK START
# =============================================================================

"""
QUICK START - Minimal code for multiple receivers:

    from sender_discovery import MultiReceiverConnection, BackgroundDiscovery

    # Setup (once at app start)
    leaderboard = MultiReceiverConnection()

    # Optional: Add manual backups
    leaderboard.add_manual_backup("192.168.0.10:5000")
    leaderboard.add_manual_backup("192.168.0.11:5000")

    # Start background discovery
    discovery = BackgroundDiscovery(leaderboard)
    discovery.start()

    # Send lap times (automatically goes to ALL receivers)
    leaderboard.send_lap_time("sim_001", "Driver Name", 125.432)

    # On exit
    discovery.stop()
"""


if __name__ == "__main__":
    print("=" * 60)
    print("MULTI-RECEIVER DISCOVERY TEST")
    print("=" * 60)
    print()
    example_usage()
```

---

## Integration Changes

### If You Already Have the Single-Receiver Version

Replace these:

| Old (Single) | New (Multi) |
|--------------|-------------|
| `LeaderboardConnection` | `MultiReceiverConnection` |
| `set_manual_backup(url)` | `add_manual_backup(url)` |
| `discover_leaderboard_server()` | `discover_all_leaderboard_servers()` |

### The send_lap_time() Call Stays the Same

```python
# This works for 1 receiver or 100 receivers
leaderboard.send_lap_time("sim_001", "John Doe", 125.432)
```

---

## How It Works

```
                    ┌─────────────────┐
                    │     SENDER      │
                    │  (Simulator)    │
                    └────────┬────────┘
                             │
                             │ Broadcast: "LEADERBOARD_DISCOVER"
                             │ (UDP port 5001)
                             ▼
        ┌────────────────────────────────────────┐
        │              NETWORK                    │
        └────────────────────────────────────────┘
           │              │              │
           ▼              ▼              ▼
    ┌──────────┐   ┌──────────┐   ┌──────────┐
    │RECEIVER 1│   │RECEIVER 2│   │RECEIVER 3│
    │ :5000    │   │ :5000    │   │ :5000    │
    └────┬─────┘   └────┬─────┘   └────┬─────┘
         │              │              │
         │   Response   │   Response   │   Response
         │              │              │
         └──────────────┴──────────────┘
                        │
                        ▼
              Sender collects ALL
              responses (waits 2 sec)
                        │
                        ▼
              Sends lap time to ALL
              receivers in parallel
```

---

## UI Elements for Multiple Receivers

```python
# Status label - shows how many receivers connected
status_label = QLabel("Searching for receivers...")

# Receivers list - shows all discovered receivers
receivers_list = QListWidget()

# Manual URL input - can add multiple
manual_url_input = QLineEdit()
manual_url_input.setPlaceholderText("Add manual IP:port")

add_manual_btn = QPushButton("Add Backup")
add_manual_btn.clicked.connect(lambda: (
    leaderboard.add_manual_backup(manual_url_input.text()),
    manual_url_input.clear()
))

# Update UI with callbacks
def on_status_change(status):
    status_label.setText(status)
    receivers_list.clear()
    for url in leaderboard.get_server_list():
        receivers_list.addItem(url)

leaderboard.on_status_change = on_status_change
```

---

## Testing Multiple Receivers

1. Start Receiver #1 on one computer
2. Start Receiver #2 on another computer (or same computer, different port)
3. Run this test on the Sender:

```python
from sender_discovery import discover_all_leaderboard_servers

servers = discover_all_leaderboard_servers()
print(f"Found {len(servers)} receivers:")
for url in servers:
    print(f"  - {url}")
```

Should output:
```
Found 2 receivers:
  - http://192.168.0.10:5000
  - http://192.168.0.11:5000
```

---

## What Happens in Each Scenario

| Scenario | Behavior |
|----------|----------|
| 1 sender, 2 receivers | Lap times appear on both leaderboards |
| 20 senders, 2 receivers | All senders discover both, all times on both |
| Receiver goes offline | Sender keeps sending to others, re-discovers when it's back |
| New receiver added | Discovered within 15 seconds automatically |
| All receivers offline | Sender keeps searching, reconnects when any comes back |
