# SimRacing Leaderboard

A simple and efficient leaderboard system for sim racing that can receive lap times from any racing simulator.

## Directory Structure
```
.
├── Client/                 # Main leaderboard application
│   ├── dist/              # Contains the leaderboard executable
│   ├── gui_client_qt.py   # Source code for the leaderboard
│   └── config.json        # Client configuration
├── config.json            # Root configuration
├── lap_times.csv          # Lap times database
└── start_leaderboard.bat  # Easy startup script
```

## Quick Start
1. Install and open Lap Time Receiver.
2. Start the Receiver server.
3. Use the Lap Time Sender application on the simulator PCs to submit results.

## Nurburgring 4K Portrait Display

- The sector challenge defaults to a `2160 x 3840` portrait canvas.
- The leaderboard is `1728 x 3136`: one 224-pixel header plus 13 result rows.
- Distance mode ranks the furthest distance first and uses the faster elapsed time to break an equal-distance tie.
- Lap-time mode remains a fastest-lap top 10.
- Use **Copy Challenge URL** to open `/leaderboard?mode=distance` on the display computer.
- For a manually scored event, select **Manual Top 10**. Enter a participant's
  name plus Cars Passed, Finishing Position, and/or Lap Time, then choose
  **Add / Update**. Lap Time accepts seconds or `M:SS.mmm`.
- Use the **Rank by** radio buttons to switch live between Cars Passed (higher
  wins), Finishing Position (lower wins), and Lap Time (lower wins). Use the
  **Show columns** checkboxes to show or hide any optional result column. The
  active ranking column is always shown; `POSITION` and `NAME` never disappear.
- The manual leaderboard is always a top 10. Re-entering the same name updates
  its entered values while preserving blank optional values; typing a name and
  choosing **Remove** deletes that participant.
- Staff can perform the same scoring from Sender's iPad admin portal using
  **Score Top 10**. The iPad posts directly to Receiver's manual-result API;
  no simulator telemetry is required.
- Use **Copy Manual Top 10 URL** to open `/leaderboard?mode=cars_passed` on
  another display. Manual results are stored separately in `cars_passed.csv`;
  simulator lap-time and Nürburgring sector-challenge data are not changed.
- Use **Copy Display Settings URL** to open `/leaderboard/settings` from a device on the same network. This editor changes the board width, row height, position, text sizes, opacity, and background-fill behavior without editing HTML.

The browser display scales the full template to the available screen while preserving its proportions. The desktop display also compensates for Windows 150% and 200% display scaling.

## Network Setup
1. On the leaderboard PC:
   - Find your IP address using `ipconfig` in Command Prompt
   - Enter your IP in the Server URL field (e.g., `http://192.168.1.100:5000`)
   - Make sure port 5000 is open in Windows Firewall

2. On other PCs:
   - Run the LapTimeSender application
   - Enter the leaderboard PC's IP address in the Server URL field
   - Submit lap times as needed

## Development
- Main application source code is in `Client/gui_client_qt.py`
- Can be rebuilt using PyInstaller if needed:
  ```bash
  cd Client
  python -m PyInstaller --name=SimRacingClient --onefile --noconsole gui_client_qt.py
  ```

## Troubleshooting
- The server runs on port 5000 - make sure this port is available
- If another device cannot open the leaderboard URL, confirm it is on the same network and allow the Receiver through Windows Firewall.
