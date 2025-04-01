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
1. Double-click `start_leaderboard.bat`
2. The leaderboard will appear and start listening for lap times
3. Use the LapTimeSender application on other PCs to submit lap times

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
- If nothing appears, make sure Python is installed and in your system PATH
- The server runs on port 5000 - make sure this port is available
- To close everything, press any key in the command window that opened 