"""
LEADERBOARD SENDER - AUTO-DISCOVERY MODULE
===========================================

Add this code to your Sender/Simulator application to enable:
- Automatic discovery of the Leaderboard Receiver (no manual IP entry)
- Auto-reconnect if connection is lost mid-event
- Manual backup URL (just in case)

USAGE:
------
1. Copy this entire file to your Sender project
2. Import and use like this:

    from sender_discovery import LeaderboardConnection, BackgroundDiscovery

    # Create connection (do this once at app startup)
    leaderboard = LeaderboardConnection()

    # Optional: Set manual backup URL
    leaderboard.set_manual_backup("http://192.168.0.248:5000")

    # Start background discovery (recommended)
    discovery_thread = BackgroundDiscovery(leaderboard)
    discovery_thread.start()

    # Send lap times - auto-discovers and auto-reconnects!
    success = leaderboard.send_lap_time(
        simulator_id="sim_001",
        driver_name="John Doe",
        lap_time=125.432,
        email="john@example.com"  # optional
    )

PROTOCOL:
---------
- Sender broadcasts "LEADERBOARD_DISCOVER" on UDP port 5001
- Receiver responds with "LEADERBOARD_SERVER:192.168.0.248:5000"
- Sender extracts IP:port and connects via HTTP POST
- Works even when IPs change - devices just need same network
"""

import socket
import time
import threading

# Try to import requests, provide helpful error if missing
try:
    import requests
except ImportError:
    print("ERROR: 'requests' library required. Install with: pip install requests")
    raise


# =============================================================================
# DISCOVERY CONSTANTS - Must match the Receiver
# =============================================================================

DISCOVERY_PORT = 5001
DISCOVERY_REQUEST = b"LEADERBOARD_DISCOVER"
DISCOVERY_RESPONSE_PREFIX = "LEADERBOARD_SERVER:"
DISCOVERY_TIMEOUT = 3  # seconds to wait for response


# =============================================================================
# DISCOVERY FUNCTION
# =============================================================================

def discover_leaderboard_server():
    """
    Broadcast on the local network to find the Leaderboard Receiver.

    How it works:
    1. Sends UDP broadcast to all devices on the network
    2. Leaderboard Receiver responds with its IP:port
    3. Returns the server URL ready to use

    Returns:
        str: Server URL (e.g., "http://192.168.0.248:5000") if found
        None: If no receiver found on network

    Example:
        url = discover_leaderboard_server()
        if url:
            print(f"Found leaderboard at: {url}")
        else:
            print("No leaderboard found")
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(DISCOVERY_TIMEOUT)

    try:
        # Send broadcast to all devices on network
        sock.sendto(DISCOVERY_REQUEST, ('<broadcast>', DISCOVERY_PORT))

        # Wait for receiver to respond
        data, addr = sock.recvfrom(1024)
        response = data.decode()

        # Parse response: "LEADERBOARD_SERVER:192.168.0.248:5000"
        if response.startswith(DISCOVERY_RESPONSE_PREFIX):
            server_info = response.replace(DISCOVERY_RESPONSE_PREFIX, "")
            ip, port = server_info.rsplit(":", 1)
            return f"http://{ip}:{port}"

    except socket.timeout:
        return None  # No receiver responded
    except Exception as e:
        print(f"[DISCOVERY] Error: {e}")
        return None
    finally:
        sock.close()

    return None


# =============================================================================
# CONNECTION MANAGER CLASS
# =============================================================================

class LeaderboardConnection:
    """
    Manages connection to Leaderboard Receiver with:
    - Automatic discovery (no IP entry needed)
    - Auto-reconnect if connection lost
    - Manual fallback (just in case)

    Example:
        leaderboard = LeaderboardConnection()

        # Optional manual backup
        leaderboard.set_manual_backup("http://192.168.0.248:5000")

        # Send lap time (auto-discovers if needed)
        leaderboard.send_lap_time("sim_001", "John Doe", 125.432)
    """

    def __init__(self):
        self.server_url = None           # Current active URL
        self.manual_url = None           # Manual backup URL (user-entered)
        self.last_discovery_time = 0     # Track when we last discovered
        self.connected = False
        self._lock = threading.Lock()

        # Callbacks for UI updates (optional)
        self.on_connected = None         # Called when connected: on_connected(url)
        self.on_disconnected = None      # Called when disconnected: on_disconnected()
        self.on_searching = None         # Called when searching: on_searching()

    def set_manual_backup(self, url):
        """
        Set a manual backup URL (the old way, just in case).
        This is used if auto-discovery fails.

        Args:
            url: Full URL, e.g., "http://192.168.0.248:5000"
                 Or just IP:port, e.g., "192.168.0.248:5000"
        """
        # Add http:// if not present
        if url and not url.startswith("http"):
            url = f"http://{url}"
        self.manual_url = url

    def discover(self):
        """
        Try to find the Leaderboard Receiver on the network.

        Returns:
            bool: True if found, False otherwise
        """
        with self._lock:
            if self.on_searching:
                self.on_searching()

            discovered_url = discover_leaderboard_server()

            if discovered_url:
                self.server_url = discovered_url
                self.connected = True
                self.last_discovery_time = time.time()
                print(f"[DISCOVERY] Found leaderboard at: {discovered_url}")

                if self.on_connected:
                    self.on_connected(discovered_url)
                return True
            else:
                print("[DISCOVERY] No leaderboard found on network")
                return False

    def ensure_connected(self):
        """
        Make sure we have a valid server URL.
        Tries auto-discovery first, then falls back to manual.

        Returns:
            bool: True if we have a URL to try, False otherwise
        """
        # If we have a working connection, use it
        if self.server_url and self.connected:
            return True

        # Try auto-discovery
        if self.discover():
            return True

        # Fall back to manual URL if set
        if self.manual_url:
            print(f"[FALLBACK] Using manual URL: {self.manual_url}")
            self.server_url = self.manual_url
            return True

        return False

    def send_lap_time(self, simulator_id, driver_name, lap_time, email=""):
        """
        Send a lap time to the Leaderboard Receiver.
        Automatically reconnects if connection is lost.

        Args:
            simulator_id: Unique ID for this simulator (e.g., "sim_001")
            driver_name: Driver's name
            lap_time: Lap time in seconds (float)
            email: Optional email address

        Returns:
            bool: True if sent successfully, False otherwise
        """
        data = {
            "simulator_id": simulator_id,
            "driver_name": driver_name,
            "lap_time": lap_time,
            "email": email
        }

        # Try up to 3 times with auto-reconnect
        for attempt in range(3):
            # Make sure we have a connection
            if not self.ensure_connected():
                print(f"[SEND] Attempt {attempt + 1}: No server available")
                time.sleep(2)
                continue

            try:
                response = requests.post(
                    self.server_url,
                    json=data,
                    timeout=5
                )

                if response.status_code == 200:
                    self.connected = True
                    print(f"[SEND] Lap time sent successfully: {driver_name} - {lap_time}s")
                    return True
                else:
                    print(f"[SEND] Server returned status {response.status_code}")

            except requests.exceptions.ConnectionError:
                print(f"[SEND] Connection failed, will retry...")
                self._mark_disconnected()

            except requests.exceptions.Timeout:
                print(f"[SEND] Request timed out, will retry...")
                self._mark_disconnected()

            except Exception as e:
                print(f"[SEND] Error: {e}")
                self._mark_disconnected()

            # Wait before retry
            time.sleep(1)

        return False

    def _mark_disconnected(self):
        """Mark as disconnected and trigger callback."""
        self.connected = False
        self.server_url = None  # Force re-discovery
        if self.on_disconnected:
            self.on_disconnected()

    def test_connection(self):
        """
        Test if we can reach the server (sends a GET ping).

        Returns:
            bool: True if server responds, False otherwise
        """
        if not self.ensure_connected():
            return False

        try:
            response = requests.get(self.server_url, timeout=3)
            if response.status_code == 200:
                self.connected = True
                if self.on_connected:
                    self.on_connected(self.server_url)
                return True
        except:
            self._mark_disconnected()

        return False

    def get_status(self):
        """
        Get current connection status as a string for display.

        Returns:
            str: Status message
        """
        if self.connected and self.server_url:
            return f"Connected: {self.server_url}"
        elif self.server_url:
            return f"Connecting to: {self.server_url}"
        else:
            return "Searching for leaderboard..."


# =============================================================================
# BACKGROUND DISCOVERY THREAD
# =============================================================================

class BackgroundDiscovery(threading.Thread):
    """
    Runs discovery in background, keeps connection alive.
    Automatically re-discovers if connection is lost.

    Example:
        leaderboard = LeaderboardConnection()
        discovery = BackgroundDiscovery(leaderboard)
        discovery.start()

        # ... your app runs ...

        # On exit:
        discovery.stop()
    """

    def __init__(self, connection: LeaderboardConnection):
        super().__init__(daemon=True)
        self.connection = connection
        self.running = True
        self.check_interval = 10  # Check every 10 seconds

    def run(self):
        # Try to discover immediately on start
        self.connection.discover()

        while self.running:
            time.sleep(self.check_interval)

            # If not connected, try to discover
            if not self.connection.connected:
                self.connection.discover()

    def stop(self):
        """Stop the background discovery thread."""
        self.running = False


# =============================================================================
# COMPLETE EXAMPLE - HOW TO USE EVERYTHING TOGETHER
# =============================================================================

def example_usage():
    """
    Complete example showing how to use the auto-discovery system.
    """

    # 1. Create the connection manager
    leaderboard = LeaderboardConnection()

    # 2. Optional: Set up callbacks for UI updates
    def on_connected(url):
        print(f"UI UPDATE: Connected to {url}")
        # Update your UI here, e.g.:
        # status_label.setText(f"Connected: {url}")
        # status_label.setStyleSheet("color: green;")

    def on_disconnected():
        print("UI UPDATE: Disconnected")
        # Update your UI here, e.g.:
        # status_label.setText("Disconnected - searching...")
        # status_label.setStyleSheet("color: orange;")

    def on_searching():
        print("UI UPDATE: Searching...")
        # Update your UI here

    leaderboard.on_connected = on_connected
    leaderboard.on_disconnected = on_disconnected
    leaderboard.on_searching = on_searching

    # 3. Optional: Set manual backup URL (just in case)
    # leaderboard.set_manual_backup("192.168.0.248:5000")

    # 4. Start background discovery
    discovery_thread = BackgroundDiscovery(leaderboard)
    discovery_thread.start()

    # 5. Wait a moment for initial discovery
    time.sleep(2)

    # 6. Send a test lap time
    success = leaderboard.send_lap_time(
        simulator_id="sim_001",
        driver_name="Test Driver",
        lap_time=123.456,
        email="test@example.com"
    )

    if success:
        print("Lap time sent successfully!")
    else:
        print("Failed to send lap time")

    # 7. Check status
    print(f"Status: {leaderboard.get_status()}")

    # 8. Cleanup on exit
    discovery_thread.stop()


# =============================================================================
# QUICK START - MINIMAL CODE NEEDED
# =============================================================================

"""
QUICK START - Copy this minimal code to get started:

    from sender_discovery import LeaderboardConnection, BackgroundDiscovery

    # Setup (do once at app start)
    leaderboard = LeaderboardConnection()
    discovery = BackgroundDiscovery(leaderboard)
    discovery.start()

    # Send lap times (do this whenever you have a new lap time)
    leaderboard.send_lap_time("sim_001", "Driver Name", 125.432)

    # Cleanup (do on app exit)
    discovery.stop()

That's it! The system will:
- Automatically find the Leaderboard Receiver on your network
- Automatically reconnect if the connection is lost
- Retry failed sends up to 3 times
- Keep searching in the background if no receiver is found
"""


# =============================================================================
# RUN EXAMPLE IF EXECUTED DIRECTLY
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("LEADERBOARD SENDER - AUTO-DISCOVERY TEST")
    print("=" * 60)
    print()
    print("This will test the auto-discovery system.")
    print("Make sure the Leaderboard Receiver is running on the same network.")
    print()

    # Test discovery
    print("Searching for Leaderboard Receiver...")
    url = discover_leaderboard_server()

    if url:
        print(f"SUCCESS! Found receiver at: {url}")
        print()
        print("Running full example...")
        example_usage()
    else:
        print("No receiver found on network.")
        print()
        print("Troubleshooting:")
        print("1. Make sure the Receiver app is running")
        print("2. Check that 'Discovery: Active' shows in the Receiver UI")
        print("3. Make sure both devices are on the same WiFi/network")
        print("4. Check firewall isn't blocking UDP port 5001")
