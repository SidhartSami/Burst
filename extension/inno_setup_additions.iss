; ═══════════════════════════════════════════════════════════════
; ADD THESE BLOCKS INTO YOUR EXISTING .iss FILE
; ═══════════════════════════════════════════════════════════════

; ── [Files] section — add these lines ───────────────────────────
; (merge into your existing [Files] block)

[Files]
; Native messaging host
Source: "burst-extension\native_host.py";           DestDir: "{app}"; Flags: ignoreversion
Source: "burst-extension\native_host.bat";           DestDir: "{app}"; Flags: ignoreversion
Source: "burst-extension\com.burst.download.manager.json"; DestDir: "{app}"; Flags: ignoreversion


; ── [Registry] section — add these lines ────────────────────────
; (merge into your existing [Registry] block)

[Registry]

; -- Chrome Native Messaging Host --
; HKCU so no extra admin needed beyond the installer's own elevation
Root: HKCU; \
  Subkey: "Software\Google\Chrome\NativeMessagingHosts\com.burst.download.manager"; \
  ValueType: string; \
  ValueData: "{app}\com.burst.download.manager.json"; \
  Flags: uninsdeletekey

; Also register for Edge (same Chromium native messaging path)
Root: HKCU; \
  Subkey: "Software\Microsoft\Edge\NativeMessagingHosts\com.burst.download.manager"; \
  ValueType: string; \
  ValueData: "{app}\com.burst.download.manager.json"; \
  Flags: uninsdeletekey

; -- Magnet Protocol Handler --
; Lets Windows open magnet: links directly in Burst (same as qBittorrent)
Root: HKCR; Subkey: "magnet";                          ValueType: string;  ValueData: "URL:Magnet Protocol";      Flags: uninsdeletekey
Root: HKCR; Subkey: "magnet";                          ValueName: "URL Protocol"; ValueType: string; ValueData: ""
Root: HKCR; Subkey: "magnet\DefaultIcon";              ValueType: string;  ValueData: "{app}\Burst.exe,0"
Root: HKCR; Subkey: "magnet\shell\open\command";       ValueType: string;  ValueData: """{app}\Burst.exe"" ""%1"""


; ═══════════════════════════════════════════════════════════════
; IMPORTANT: After packaging, open com.burst.download.manager.json
; and replace "native_host.bat" with the full path IF needed,
; OR leave it as a relative filename — Windows will resolve it
; correctly because Inno Setup copies everything to the same {app} dir.
; ═══════════════════════════════════════════════════════════════
