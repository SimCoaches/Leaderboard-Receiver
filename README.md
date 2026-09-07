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
