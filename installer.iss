[Setup]
AppName=Burst
AppVersion=1.3.0
AppVerName=Burst
AppPublisher=SidhartSami
DefaultDirName={autopf}\Burst
DefaultGroupName=Burst
UninstallDisplayIcon={app}\Burst.exe
Compression=lzma2
SolidCompression=yes
OutputDir=Output
OutputBaseFilename=Burst_Setup_v1.3.0
PrivilegesRequired=admin
ChangesEnvironment=yes
UsedUserAreasWarning=no

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "chromeext"; Description: "Enable Chrome/Edge Integration"; GroupDescription: "Browser Extensions:"; Flags: unchecked
Name: "firefoxext"; Description: "Enable Firefox/Zen Integration"; GroupDescription: "Browser Extensions:"; Flags: unchecked

[Files]
; This grabs the EXE you just built with PyInstaller
Source: "build_v1\Burst.exe"; DestDir: "{app}"; Flags: ignoreversion
; We also include the logo in the install folder just in case
Source: "assets\logo.png"; DestDir: "{app}"; Flags: ignoreversion
; Native messaging host
Source: "build_v1\native_host.exe";          DestDir: "{app}"; Flags: ignoreversion
Source: "backend\native_host.py";           DestDir: "{app}"; Flags: ignoreversion
Source: "backend\native_host.bat";          DestDir: "{app}"; Flags: ignoreversion
Source: "backend\com.burst.download.manager.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "backend\com.burst.download.manager.firefox.json"; DestDir: "{app}"; Flags: ignoreversion
; Package the extensions so they are automatically unpacked for the user to load
Source: "extension-chrome\*"; DestDir: "{app}\extension-chrome"; Excludes: "*.pem"; Flags: ignoreversion recursesubdirs createallsubdirs; Tasks: chromeext
Source: "extension-firefox\*"; DestDir: "{app}\extension-firefox"; Flags: ignoreversion recursesubdirs createallsubdirs; Tasks: firefoxext

[Registry]
; -- Chrome Native Messaging Host --
Root: HKCU; Subkey: "Software\Google\Chrome\NativeMessagingHosts\com.burst.download.manager"; ValueType: string; ValueData: "{app}\com.burst.download.manager.json"; Flags: uninsdeletekey; Tasks: chromeext
Root: HKLM; Subkey: "Software\Google\Chrome\NativeMessagingHosts\com.burst.download.manager"; ValueType: string; ValueData: "{app}\com.burst.download.manager.json"; Flags: uninsdeletekey; Tasks: chromeext

; -- Edge Native Messaging Host --
Root: HKCU; Subkey: "Software\Microsoft\Edge\NativeMessagingHosts\com.burst.download.manager"; ValueType: string; ValueData: "{app}\com.burst.download.manager.json"; Flags: uninsdeletekey; Tasks: chromeext
Root: HKLM; Subkey: "Software\Microsoft\Edge\NativeMessagingHosts\com.burst.download.manager"; ValueType: string; ValueData: "{app}\com.burst.download.manager.json"; Flags: uninsdeletekey; Tasks: chromeext

; -- Firefox/Zen Native Messaging Host (HKCU) --
Root: HKCU; Subkey: "Software\Mozilla\NativeMessagingHosts\com.burst.download.manager"; ValueType: string; ValueData: "{app}\com.burst.download.manager.firefox.json"; Flags: uninsdeletekey; Tasks: firefoxext
Root: HKCU; Subkey: "Software\Zen\NativeMessagingHosts\com.burst.download.manager"; ValueType: string; ValueData: "{app}\com.burst.download.manager.firefox.json"; Flags: uninsdeletekey; Tasks: firefoxext
Root: HKCU; Subkey: "Software\Wow6432Node\Mozilla\NativeMessagingHosts\com.burst.download.manager"; ValueType: string; ValueData: "{app}\com.burst.download.manager.firefox.json"; Flags: uninsdeletekey; Tasks: firefoxext
Root: HKCU; Subkey: "Software\Wow6432Node\Zen\NativeMessagingHosts\com.burst.download.manager"; ValueType: string; ValueData: "{app}\com.burst.download.manager.firefox.json"; Flags: uninsdeletekey; Tasks: firefoxext

; -- Firefox/Zen Native Messaging Host (HKLM) --
Root: HKLM; Subkey: "Software\Mozilla\NativeMessagingHosts\com.burst.download.manager"; ValueType: string; ValueData: "{app}\com.burst.download.manager.firefox.json"; Flags: uninsdeletekey; Tasks: firefoxext
Root: HKLM; Subkey: "Software\Zen\NativeMessagingHosts\com.burst.download.manager"; ValueType: string; ValueData: "{app}\com.burst.download.manager.firefox.json"; Flags: uninsdeletekey; Tasks: firefoxext
Root: HKLM; Subkey: "Software\Wow6432Node\Mozilla\NativeMessagingHosts\com.burst.download.manager"; ValueType: string; ValueData: "{app}\com.burst.download.manager.firefox.json"; Flags: uninsdeletekey; Tasks: firefoxext
Root: HKLM; Subkey: "Software\Wow6432Node\Zen\NativeMessagingHosts\com.burst.download.manager"; ValueType: string; ValueData: "{app}\com.burst.download.manager.firefox.json"; Flags: uninsdeletekey; Tasks: firefoxext

; -- Magnet Protocol Handler --
Root: HKCR; Subkey: "magnet"; ValueType: string; ValueData: "URL:Magnet Protocol"; Flags: uninsdeletekey
Root: HKCR; Subkey: "magnet"; ValueName: "URL Protocol"; ValueType: string; ValueData: ""
Root: HKCR; Subkey: "magnet\DefaultIcon"; ValueType: string; ValueData: "{app}\Burst.exe,0"
Root: HKCR; Subkey: "magnet\shell\open\command"; ValueType: string; ValueData: """{app}\Burst.exe"" ""%1"""

; -- App Paths Registry --
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Burst.exe"; ValueType: string; ValueData: "{app}\Burst.exe"; Flags: uninsdeletekey

; -- Add to System PATH --
Root: HKCU; Subkey: "Environment"; ValueName: "Path"; ValueType: expandsz; ValueData: "{olddata};{app}"; Check: NeedsAddPath(ExpandConstant('{app}'))
Root: HKLM; Subkey: "SYSTEM\CurrentControlSet\Control\Session Manager\Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; Check: NeedsAddPath(ExpandConstant('{app}'))

[Icons]
Name: "{group}\Burst"; Filename: "{app}\Burst.exe"; AppUserModelID: "Burst.DownloadManager"
Name: "{commondesktop}\Burst"; Filename: "{app}\Burst.exe"; AppUserModelID: "Burst.DownloadManager"; Tasks: desktopicon

[Run]
Filename: "{app}\Burst.exe"; Description: "{cm:LaunchProgram,Burst}"; Flags: nowait postinstall skipifsilent runascurrentuser
; Register Burst as a Task Scheduler logon task (elevated, runs --headless silently on boot)
Filename: "schtasks"; Parameters: "/create /tn ""Burst Autostart"" /tr ""\""{app}\Burst.exe\"" --headless"" /sc onlogon /rl highest /f"; Flags: runhidden

[UninstallRun]
; Remove the Task Scheduler autostart task on uninstall
Filename: "schtasks"; Parameters: "/delete /tn ""Burst Autostart"" /f"; Flags: runhidden; RunOnceId: "RemoveBurstAutostart"

[Code]
function EscapeJsonPath(Path: String): String;
begin
  StringChange(Path, '\', '\\');
  Result := Path;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ManifestContent: String;
  ManifestPath: String;
  FirefoxManifestContent: String;
  FirefoxManifestPath: String;
  EscapedPath: String;
begin
  if CurStep = ssPostInstall then
  begin
    ManifestPath := ExpandConstant('{app}\com.burst.download.manager.json');
    FirefoxManifestPath := ExpandConstant('{app}\com.burst.download.manager.firefox.json');
    EscapedPath := EscapeJsonPath(ExpandConstant('{app}\native_host.exe'));
    
    if WizardIsTaskSelected('chromeext') then
    begin
      ManifestContent := 
        '{' + #13#10 +
        '  "name": "com.burst.download.manager",' + #13#10 +
        '  "description": "Burst Download Manager Native Messaging Host",' + #13#10 +
        '  "path": "' + EscapedPath + '",' + #13#10 +
        '  "type": "stdio",' + #13#10 +
        '  "allowed_origins": [' + #13#10 +
        '    "chrome-extension://pblmhjepeacmfphcnaaekefjnipfkcfd/"' + #13#10 +
        '  ],' + #13#10 +
        '  "allowed_extensions": [' + #13#10 +
        '    "burst@sidhartsami.com"' + #13#10 +
        '  ]' + #13#10 +
        '}';
      SaveStringToFile(ManifestPath, ManifestContent, False);
    end;

    if WizardIsTaskSelected('firefoxext') then
    begin
      FirefoxManifestContent :=
        '{' + #13#10 +
        '  "name": "com.burst.download.manager",' + #13#10 +
        '  "description": "Burst Download Manager Native Messaging Host",' + #13#10 +
        '  "path": "' + EscapedPath + '",' + #13#10 +
        '  "type": "stdio",' + #13#10 +
        '  "allowed_extensions": [' + #13#10 +
        '    "burst@sidhartsami.com"' + #13#10 +
        '  ]' + #13#10 +
        '}';
      SaveStringToFile(FirefoxManifestPath, FirefoxManifestContent, False);
    end;
    
    // Create burst-cli.bat alias
    // We just pass through all arguments (%*) without adding an extra 'pip' 
    // to keep it simple and robust.
    SaveStringToFile(ExpandConstant('{app}\burst-cli.bat'), '@echo off' + #13#10 + '"' + ExpandConstant('{app}\Burst.exe') + '" %*', False);
  end;
end;

function NeedsAddPath(Param: string): boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(HKEY_LOCAL_MACHINE, 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment', 'Path', OrigPath) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Uppercase(Param) + ';', ';' + Uppercase(OrigPath) + ';') = 0;
end;

procedure RemovePath(PathToRemove: string);
var
  Paths: string;
  P: Integer;
begin
  if not RegQueryStringValue(HKLM, 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment', 'Path', Paths) then
  begin
    Log('PATH not found');
  end
  else
  begin
    P := Pos(';' + Uppercase(PathToRemove) + ';', ';' + Uppercase(Paths) + ';');
    if P > 0 then
    begin
      if P > 1 then P := P - 1;
      Delete(Paths, P, Length(PathToRemove) + 1);
      RegWriteStringValue(HKLM, 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment', 'Path', Paths);
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    RemovePath(ExpandConstant('{app}'));
  end;
end;
