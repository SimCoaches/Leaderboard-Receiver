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

**LeaderboardWindow** - Full-screen leaderboard display showing top 10 drivers sorted by fastest lap (3 columns: Position, Driver, Time). In distance ranking mode it shows 4 columns (Position, Driver, Distance, Time) and the top 13 drivers, with a styled podium block (larger gold/silver/bronze rows, divider) above positions 4-13. Supports horizontal/vertical orientations, position-based font sizing (1st, 2nd, 3rd get larger fonts), draggable positioning, and background images.

**NetworkThread** - QThread that monitors `lap_times.csv` for changes and emits signals to update the leaderboard UI.

**Browser mirror** (`/leaderboard`, backed by `/api/leaderboard` and
`read_leaderboard_entries`) - read-only web copy of the board for phones/extra
screens. It follows the same ranking rules as the desktop window: in distance
mode it picks each driver's furthest run (elapsed time breaks ties), shows 13
entries with the Distance column and podium divider, and sizes the board for
14 rows. `ranking_mode` reaches it through the mirror display config.

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

- `lap_times.csv` - Leaderboard data (CSV with: simulator_id, driver_name, lap_time, email, phone, timestamp, distance_pct). `distance_pct` (0-100, default 100.0) is how far around the track a run got; older CSV layouts are migrated automatically.
- `config.json` - User settings (display, server, theme configuration). Includes `ranking_mode`: `"lap_time"` (default, rank by fastest lap) or `"distance"` (sector challenge: rank by highest distance_pct, tie-broken by lap time; the display adds a Distance column showing the percentage and always shows the elapsed time in the Time column)
- `queue.json` - Queue state (auto-created)
- `sms_config.json` - SMS provider config (Textbelt or Twilio)

### Network Ports

- **5000** - HTTP server for lap times and queue API
- **5001** - UDP discovery broadcasts

### Threading Model

Uses PyQt6 signal/slot pattern for thread-safe UI updates. Network operations run in background threads. Never block the main Qt thread.
