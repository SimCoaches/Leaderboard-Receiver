# Lap Time Sender

A simple application to send lap times to the SimRacing Leaderboard server.

## Usage - Executable Version

1. Make sure the SimRacing Leaderboard server is running
2. Run `LapTimeSender.exe` from the `dist` folder
3. Enter the following information:
   - Server URL (default: http://localhost:5000)
   - Driver Name
   - Lap Time in mm:ss.xxx format (e.g., 1:23.456)
4. Click "Send Lap Time" to submit the time to the server

## Usage - Python Version (for development)

1. Install Python 3.8 or higher
2. Install dependencies:
```bash
pip install -r requirements.txt
```
3. Run the sender:
```bash
python lap_time_sender.py
```

## Notes

- The server must be running to receive lap times
- Times are automatically converted to seconds before sending
- The status label will show success/error messages
- Invalid inputs will be caught and displayed as errors

## Building the Executable

To rebuild the executable:

```bash
python -m PyInstaller --name=LapTimeSender --onefile --noconsole --hidden-import PyQt6.QtCore --hidden-import PyQt6.QtGui --hidden-import PyQt6.QtWidgets --hidden-import requests lap_time_sender.py
```

The executable will be created in the `dist` directory 