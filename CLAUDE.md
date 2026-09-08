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

Each mirror window can pin its own board with a `mode` query parameter, so a
venue can run both at once from one receiver:

- `/leaderboard?mode=distance` - sector challenge board (13 rows + Distance)
- `/leaderboard?mode=lap_time` - classic fastest-lap top 10
- `/leaderboard?mode=cars_passed` - flexible manual top 10; ranking and visible
  columns follow the Receiver's manual controls
- `/leaderboard` - follows whatever the desktop leaderboard is set to

The page passes its pinned mode to `/api/leaderboard` and
`/api/leaderboard/display-config` (`?mode=...`); both endpoints also report
the `ranking_mode` they used. Invalid values fall back to the configured mode.

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
- `config.json` - User settings (display, server, theme configuration). Includes `ranking_mode`: `"lap_time"` (rank by fastest lap), `"distance"` (sector challenge: rank by highest distance_pct, tie-broken by lap time), or `"cars_passed"` (flexible manual top 10). Manual mode also uses `manual_rank_by` and the three `manual_show_*` flags.
- `cars_passed.csv` - Manual event results (`driver_name`, `cars_passed`, `finishing_position`, `lap_time`, `updated_at`). Kept separate from simulator lap/distance data. The historical filename is retained for upgrade compatibility.
- `queue.json` - Queue state (auto-created)
- `sms_config.json` - SMS provider config (Textbelt or Twilio)

### Network Ports

- **5000** - HTTP server for lap times and queue API
- **5001** - UDP discovery broadcasts

### Threading Model

Uses PyQt6 signal/slot pattern for thread-safe UI updates. Network operations run in background threads. Never block the main Qt thread.
