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
Source: "assets\logo.png"; DestDir: "{app}"; Flags: ignoreversion
; Native messaging host
Source: "backend\native_host.py";           DestDir: "{app}"; Flags: ignoreversion
Source: "backend\native_host.bat";          DestDir: "{app}"; Flags: ignoreversion
Source: "backend\com.burst.download.manager.json"; DestDir: "{app}"; Flags: ignoreversion

[Registry]
; -- Chrome Native Messaging Host --
Root: HKCU; Subkey: "Software\Google\Chrome\NativeMessagingHosts\com.burst.download.manager"; ValueType: string; ValueData: "{app}\com.burst.download.manager.json"; Flags: uninsdeletekey
; -- Edge Native Messaging Host --
Root: HKCU; Subkey: "Software\Microsoft\Edge\NativeMessagingHosts\com.burst.download.manager"; ValueType: string; ValueData: "{app}\com.burst.download.manager.json"; Flags: uninsdeletekey
; -- Magnet Protocol Handler --
Root: HKCR; Subkey: "magnet"; ValueType: string; ValueData: "URL:Magnet Protocol"; Flags: uninsdeletekey
Root: HKCR; Subkey: "magnet"; ValueName: "URL Protocol"; ValueType: string; ValueData: ""
Root: HKCR; Subkey: "magnet\DefaultIcon"; ValueType: string; ValueData: "{app}\Burst.exe,0"
Root: HKCR; Subkey: "magnet\shell\open\command"; ValueType: string; ValueData: """{app}\Burst.exe"" ""%1"""

[Icons]
Name: "{group}\Burst"; Filename: "{app}\Burst.exe"
Name: "{commondesktop}\Burst"; Filename: "{app}\Burst.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Burst.exe"; Description: "{cm:LaunchProgram,Burst}"; Flags: nowait postinstall skipifsilent runascurrentuser
