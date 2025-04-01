[Setup]
AppName=Lap Time Receiver
AppVersion=1.0
DefaultDirName={autopf}\Lap Time Receiver
DefaultGroupName=Lap Time Receiver
OutputDir=Output
OutputBaseFilename=LapTimeReceiver_Setup
Compression=lzma
SolidCompression=yes
UninstallDisplayIcon={app}\Lap Time Receiver.exe
UsePreviousAppDir=yes
ArchitecturesInstallIn64BitMode=x64

[Files]
; Main executable
Source: "Client\dist\Lap Time Receiver.exe"; DestDir: "{app}"; Flags: ignoreversion

; Configuration files
Source: "Client\config.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "config.json"; DestDir: "{app}"; DestName: "root_config.json"; Flags: ignoreversion

; Data files
Source: "lap_times.csv"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

; Documentation
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion

; Create empty lap_times.csv if it doesn't exist
[Code]
procedure InitializeWizard;
begin
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    if not FileExists(ExpandConstant('{app}\lap_times.csv')) then begin
      SaveStringToFile(ExpandConstant('{app}\lap_times.csv'), 'simulator_id,driver_name,lap_time,email,timestamp' + #13#10, False);
    end;
  end;
end;

[Icons]
Name: "{group}\Lap Time Receiver"; Filename: "{app}\Lap Time Receiver.exe"
Name: "{commondesktop}\Lap Time Receiver"; Filename: "{app}\Lap Time Receiver.exe"

[Run]
Filename: "{app}\Lap Time Receiver.exe"; Description: "Launch Lap Time Receiver"; Flags: nowait postinstall skipifsilent

[InstallDelete]
Type: files; Name: "{app}\*.pyc"
Type: filesandordirs; Name: "{app}\__pycache__"

[Dirs]
Name: "{app}"; Permissions: everyone-full 