# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run from source
cd Client
python gui_client_qt.py

# Build executable
cd Client
python -m PyInstaller --name="Lap Time Receiver" --onefile --noconsole --hidden-import=PyQt6.sip gui_client_qt.py

# Build Windows installer (requires Inno Setup 6)
build_installer.bat
```

## Architecture

This is a PyQt6 desktop application for sim racing leaderboards. The entire application is in `Client/gui_client_qt.py`.

### Core Components

**ControlWindow** - Main control panel with tabbed UI for configuration, queue management, event export/import, and SMS settings.

**LeaderboardWindow** - Full-screen leaderboard display showing top 10 drivers sorted by fastest lap. Supports horizontal/vertical orientations, position-based font sizing (1st, 2nd, 3rd get larger fonts), draggable positioning, and background images.

**NetworkThread** - QThread that monitors `lap_times.csv` for changes and emits signals to update the leaderboard UI.

**LapTimeHandler** - HTTP request handler for the built-in server. Key endpoints:
- `/api/queue/join`, `/api/queue/remove`, `/api/queue/assign` - Queue management
- `/api/session/started`, `/api/session/ended` - Session tracking
- Default POST: Lap time submission

**DiscoveryResponder** - UDP listener on port 5001 that responds to broadcast discovery requests, allowing simulators to auto-discover the leaderboard server.

### Data Flow

```
Simulator → HTTP POST (port 5000) → LapTimeHandler → lap_times.csv → NetworkThread → LeaderboardWindow
                                          ↓
                                    Queue System → SMS Notifications
```

### Key Files

- `lap_times.csv` - Leaderboard data (CSV with: simulator_id, driver_name, lap_time, email, timestamp)
- `config.json` - User settings (display, server, theme configuration)
- `queue.json` - Queue state (auto-created)
- `sms_config.json` - SMS provider config (Textbelt or Twilio)

### Network Ports

- **5000** - HTTP server for lap times and queue API
- **5001** - UDP discovery broadcasts

### Threading Model

Uses PyQt6 signal/slot pattern for thread-safe UI updates. Network operations run in background threads. Never block the main Qt thread.
