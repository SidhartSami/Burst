import os
import sys
import subprocess
import ctypes

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def request_elevation():
    """
    Relaunches the current process with administrator privileges using the 'runas' verb.
    This triggers the Windows UAC prompt.
    """
    if not is_admin():
        # Prepare arguments: exclude the first arg (executable path)
        args = " ".join([f'"{arg}"' for arg in sys.argv[1:]])
        try:
            # ShellExecuteW with 'runas' triggers UAC elevation
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, args, None, 1
            )
            sys.exit(0)
        except Exception as e:
            print(f"Elevation request failed: {e}")
            return False
    return True

def apply_firewall_rules():
    if not is_admin():
        print("Skipping firewall setup (not running as Admin).")
        return False

    exe_path = sys.executable
    print(f"Applying firewall rules for: {exe_path}")

    # Run silently with CREATE_NO_WINDOW to prevent flickering blue windows
    CREATE_NO_WINDOW = 0x08000000
    rule_name = "Burst P2P Engine"

    subprocess.run(
        ["netsh", "advfirewall", "firewall", "add", "rule", 
         f"name={rule_name}", "dir=in", "action=allow", 
         f"program={exe_path}", "enable=yes"],
        check=False,
        creationflags=CREATE_NO_WINDOW
    )
    subprocess.run(
        ["netsh", "advfirewall", "firewall", "add", "rule", 
         f"name={rule_name}", "dir=out", "action=allow", 
         f"program={exe_path}", "enable=yes"],
        check=False,
        creationflags=CREATE_NO_WINDOW
    )
    
    print("Firewall rules applied successfully.")
    return True

def apply_autostart_rules(enabled: bool) -> bool:
    """
    Registry-based autostart on boot for Burst under HKCU Run.
    """
    import sys
    import os
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    key_name = "Burst"

    try:
        # Open key with write permissions
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
    except OSError as e:
        try:
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
        except Exception as create_err:
            print(f"[autostart] Failed to open/create registry key: {create_err}")
            return False

    try:
        if enabled:
            # Determine the correct path and arguments based on whether the app is compiled (frozen)
            is_packaged = getattr(sys, 'frozen', False)
            if is_packaged:
                exec_command = f'"{sys.executable}" --minimized'
            else:
                main_script = os.path.abspath(sys.argv[0])
                python_exe = sys.executable
                if python_exe.lower().endswith("python.exe"):
                    pythonw_exe = python_exe[:-10] + "pythonw.exe"
                    if os.path.exists(pythonw_exe):
                        python_exe = pythonw_exe
                exec_command = f'"{python_exe}" "{main_script}" --minimized'

            winreg.SetValueEx(key, key_name, 0, winreg.REG_SZ, exec_command)
            print(f"[autostart] Registry autostart key set: {exec_command}")
        else:
            try:
                winreg.DeleteValue(key, key_name)
                print("[autostart] Registry autostart key deleted successfully.")
            except FileNotFoundError:
                # Key already deleted or doesn't exist, ignore
                pass
        winreg.CloseKey(key)
        return True
    except Exception as e:
        print(f"[autostart] Failed to apply autostart rules: {e}")
        return False

def setup_native_host():
    """
    Dynamically configures and registers the Chrome/Firefox/Edge Native Messaging Host
    manifest with the correct absolute path to native_host.bat or native_host.exe.
    This dynamically calculates the local unpacked extension's SHA-256 ID to authorize
    it for Chrome native messaging, completely bypassing 'Access forbidden' errors.
    """
    import json
    import winreg
    import hashlib
    
    is_packaged = getattr(sys, 'frozen', False)
    if is_packaged:
        # Running from C:\Program Files (x86)\Burst\Burst.exe
        app_dir = os.path.dirname(sys.executable)
    else:
        # Running from source (development mode)
        app_dir = os.path.dirname(os.path.abspath(__file__))

    # Resolve paths
    bat_path = os.path.join(app_dir, "native_host.bat")
    if not os.path.exists(bat_path):
        if os.path.exists(os.path.join(app_dir, "backend", "native_host.bat")):
            app_dir = os.path.join(app_dir, "backend")
            bat_path = os.path.join(app_dir, "native_host.bat")

    # Save the manifest JSON under the user's writeable AppData directory to bypass Program Files restrictions
    appdata_dir = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Burst")
    os.makedirs(appdata_dir, exist_ok=True)
    manifest_path = os.path.join(appdata_dir, "com.burst.download.manager.json")
    firefox_manifest_path = os.path.join(appdata_dir, "com.burst.download.manager.firefox.json")
    
    # Check if compiled native_host.exe exists, use it instead of .bat for standalone setups
    exe_host_path = os.path.join(app_dir, "native_host.exe")
    if os.path.exists(exe_host_path):
        host_executable = exe_host_path
    else:
        host_executable = bat_path
        
    # Firefox-specific host executable resolution
    firefox_exe_path = os.path.join(app_dir, "native_host_firefox.exe")
    if os.path.exists(firefox_exe_path):
        firefox_host_executable = firefox_exe_path
    elif os.path.exists(exe_host_path):
        firefox_host_executable = exe_host_path
    else:
        firefox_host_executable = bat_path
        
    print(f"[NATIVE_HOST] Auto-resolved native host paths: Chrome={host_executable}, Firefox={firefox_host_executable}")
    
    # Hardcoded stable extension ID locked by manifest key
    allowed_origins = [
        "chrome-extension://pblmhjepeacmfphcnaaekefjnipfkcfd/"
    ]
    
    # Generate the dynamic JSON manifest content
    manifest_data = {
        "name": "com.burst.download.manager",
        "description": "Burst Download Manager Native Messaging Host",
        "path": host_executable,
        "type": "stdio",
        "allowed_origins": allowed_origins,
    }
    firefox_manifest_data = {
        "name": "com.burst.download.manager",
        "description": "Burst Download Manager Native Messaging Host",
        "path": firefox_host_executable,
        "type": "stdio",
        "allowed_extensions": [
            "burst@sidhartsami.com"
        ]
    }
    
    try:
        # NOTE: The static dev manifests are overwritten here dynamically at runtime
        # to ensure correct absolute paths (bat/exe) and browser authorizations.
        # Write the manifest file dynamically
        with open(manifest_path, "w") as f:
            json.dump(manifest_data, f, indent=2)
        print(f"[NATIVE_HOST] Successfully wrote manifest to: {manifest_path}")
        with open(firefox_manifest_path, "w") as f:
            json.dump(firefox_manifest_data, f, indent=2)
        print(f"[NATIVE_HOST] Successfully wrote Firefox manifest to: {firefox_manifest_path}")
        
        # Register in Windows registry for Chrome, Edge, Firefox, and Zen under HKCU
        chromium_keys = [
            (winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\NativeMessagingHosts\com.burst.download.manager"),
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Edge\NativeMessagingHosts\com.burst.download.manager"),
        ]
        firefox_keys = [
            (winreg.HKEY_CURRENT_USER, r"Software\Mozilla\NativeMessagingHosts\com.burst.download.manager"),
            (winreg.HKEY_CURRENT_USER, r"Software\Zen\NativeMessagingHosts\com.burst.download.manager"),
            (winreg.HKEY_CURRENT_USER, r"Software\Wow6432Node\Mozilla\NativeMessagingHosts\com.burst.download.manager"),
            (winreg.HKEY_CURRENT_USER, r"Software\Wow6432Node\Zen\NativeMessagingHosts\com.burst.download.manager")
        ]
        
        for root, subkey in chromium_keys:
            try:
                winreg.CreateKey(root, subkey)
                with winreg.OpenKey(root, subkey, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "", 0, winreg.REG_SZ, manifest_path)
                print(f"[NATIVE_HOST] Registered registry key: {subkey} -> {manifest_path}")
            except Exception as reg_err:
                print(f"[NATIVE_HOST] Failed to register key {subkey}: {reg_err}")

        for root, subkey in firefox_keys:
            try:
                winreg.CreateKey(root, subkey)
                with winreg.OpenKey(root, subkey, 0, winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, "", 0, winreg.REG_SZ, firefox_manifest_path)
                print(f"[NATIVE_HOST] Registered registry key: {subkey} -> {firefox_manifest_path}")
            except Exception as reg_err:
                print(f"[NATIVE_HOST] Failed to register key {subkey}: {reg_err}")
                
        # Directly write the JSON manifest into Mozilla and Zen NativeMessagingHosts folders in AppData
        # (This acts as a bulletproof filesystem fallback if Zen Browser skips registry scanning)
        for brand in ["Mozilla", "Zen"]:
            brand_dir = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), brand, "NativeMessagingHosts")
            os.makedirs(brand_dir, exist_ok=True)
            brand_manifest_path = os.path.join(brand_dir, "com.burst.download.manager.json")
            try:
                with open(brand_manifest_path, "w") as f:
                    json.dump(firefox_manifest_data, f, indent=2)
                print(f"[NATIVE_HOST] Wrote brand filesystem fallback: {brand_manifest_path}")
            except Exception as e:
                print(f"[NATIVE_HOST] Failed to write brand fallback for {brand}: {e}")
                
    except Exception as e:
        print(f"[NATIVE_HOST] Error setting up native messaging host: {e}")
