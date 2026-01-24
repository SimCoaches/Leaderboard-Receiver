@echo off
echo Building Lap Time Receiver...
echo ======================================

echo Step 1: Building executable with PyInstaller...
cd Client
python -m PyInstaller --name="Lap Time Receiver" --onefile --noconsole --hidden-import=PyQt6.sip gui_client_qt.py
cd ..

echo Step 2: Creating Output directory for installer...
if not exist Output mkdir Output

echo Step 3: Building installer with Inno Setup...
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" (
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
    echo Done! Your installer can be found in the Output folder.
) else (
    echo Inno Setup not found. Executable only build completed.
    echo Your executable can be found in Client\dist\
)

pause 