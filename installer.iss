[Setup]
AppName=Lap Time Receiver
AppVersion=1.0
AppPublisher=SimRacing Leaderboard
AppPublisherURL=https://github.com
DefaultDirName={autopf}\Lap Time Receiver
DefaultGroupName=Lap Time Receiver
OutputDir=Output
OutputBaseFilename=LapTimeReceiver_Setup
Compression=lzma
SolidCompression=yes
UninstallDisplayIcon={app}\Lap Time Receiver.exe
UsePreviousAppDir=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
LicenseFile=
InfoBeforeFile=
InfoAfterFile=
SetupIconFile=
WizardImageFile=
WizardSmallImageFile=

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
function InitializeSetup(): Boolean;
var
  VCRedistInstalled: Boolean;
begin
  // Check if Visual C++ 2015-2022 Redistributable (x64) is installed
  // Note: PyInstaller bundles VCRUNTIME140.dll and related DLLs in the onefile executable,
  // so the app should work even without the redistributable installed.
  // This check is informational only.
  VCRedistInstalled := RegKeyExists(HKEY_LOCAL_MACHINE, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64') or
                       RegKeyExists(HKEY_LOCAL_MACHINE, 'SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64') or
                       RegKeyExists(HKEY_LOCAL_MACHINE, 'SOFTWARE\Microsoft\VisualStudio\15.0\VC\Runtimes\x64') or
                       RegKeyExists(HKEY_LOCAL_MACHINE, 'SOFTWARE\WOW6432Node\Microsoft\VisualStudio\15.0\VC\Runtimes\x64');
  
  Result := True;
end;

procedure InitializeWizard;
begin
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    // Create empty lap_times.csv if it doesn't exist
    if not FileExists(ExpandConstant('{app}\lap_times.csv')) then begin
      SaveStringToFile(ExpandConstant('{app}\lap_times.csv'), 'simulator_id,driver_name,lap_time,email,timestamp' + #13#10, False);
    end;
    
    // Create default config.json if it doesn't exist
    if not FileExists(ExpandConstant('{app}\config.json')) then begin
      SaveStringToFile(ExpandConstant('{app}\config.json'), '{}', False);
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