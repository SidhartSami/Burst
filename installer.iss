[Setup]
AppName=Burst
AppVersion=1.0
AppPublisher=SidhartSami
DefaultDirName={pf}\Burst
DefaultGroupName=Burst
UninstallDisplayIcon={app}\Burst.exe
Compression=lzma2
SolidCompression=yes
OutputDir=Output
OutputBaseFilename=Burst_Setup_v1.0
PrivilegesRequired=admin

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; This grabs the EXE you just built with PyInstaller
Source: "build_v1\Burst.exe"; DestDir: "{app}"; Flags: ignoreversion
; We also include the logo in the install folder just in case
Source: "logo.png"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Burst"; Filename: "{app}\Burst.exe"
Name: "{commondesktop}\Burst"; Filename: "{app}\Burst.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Burst.exe"; Description: "{cm:LaunchProgram,Burst}"; Flags: nowait postinstall skipifsilent runascurrentuser
