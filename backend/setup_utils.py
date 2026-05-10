import os
import sys
import subprocess
import ctypes

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

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
