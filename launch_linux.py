# Linux only — do not import on Windows
"""
Burst — Linux application launcher and entry point.
Starts the FastAPI backend, registers system tray icon, and launches pywebview with GTK3.
"""
platform_override = "linux"

import os
import sys
import time
import socket
import signal
import threading
import urllib.request
from typing import Optional

# Ensure repository paths are on sys.path
base_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.join(base_dir, "backend")
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)
if base_dir not in sys.path:
    sys.path.insert(0, base_dir)

# Import backend application and Linux-specific components
from main import app, TaskbarProgress
import config
from window_linux import LinuxWindowAPI, detect_linux_theme

# Global references
uvicorn_server = None
window_ref = None
tray_icon_ref = None
is_quitting = False


def handle_shutdown(sig=None, frame=None):
    """Clean shutdown handler for SIGINT, SIGTERM, and tray exit."""
    global is_quitting, window_ref, uvicorn_server, tray_icon_ref
    if is_quitting:
        return
    is_quitting = True
    print("\n[Burst-Linux] Shutting down gracefully...", flush=True)

    if uvicorn_server:
        uvicorn_server.should_exit = True

    if tray_icon_ref:
        try:
            tray_icon_ref.stop()
        except Exception:
            pass

    if window_ref:
        try:
            window_ref.destroy()
        except Exception:
            pass

    sys.exit(0)


# Register Linux POSIX signal handlers
signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)


def is_backend_running(port: int = 59284) -> bool:
    """Socket-based single-instance check."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except Exception:
            return False


def setup_tray(window):
    """Initialize system tray icon with Open and Quit actions."""
    global tray_icon_ref
    try:
        import pystray
        from PIL import Image

        def on_open(icon, item):
            if window:
                window.show()

        def on_exit(icon, item):
            handle_shutdown()

        logo_path = os.path.join(base_dir, "assets", "logo.png")
        if os.path.exists(logo_path):
            image = Image.open(logo_path)
        else:
            image = Image.new("RGB", (64, 64), color=(249, 115, 22))

        menu = pystray.Menu(
            pystray.MenuItem("Open Burst", on_open, default=True),
            pystray.MenuItem("Quit", on_exit),
        )
        icon = pystray.Icon("Burst", image, "Burst Download Manager", menu)
        tray_icon_ref = icon

        t = threading.Thread(target=icon.run, daemon=True)
        t.start()
        print("[Burst-Linux] System tray icon started successfully.", flush=True)
    except Exception as e:
        print(f"[Burst-Linux] System tray initialization skipped: {e}", flush=True)


def main():
    global uvicorn_server, window_ref, is_quitting

    # Parse command line flags
    headless = "--headless" in sys.argv or "--minimized" in sys.argv
    if "--headless" in sys.argv:
        sys.argv.remove("--headless")
    if "--minimized" in sys.argv:
        sys.argv.remove("--minimized")

    # 1. Single-instance check
    if is_backend_running():
        if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
            try:
                from native_host import handle
                handle({"url": sys.argv[1]})
            except Exception as e:
                print(f"[Burst-Linux] Error sending URL to running instance: {e}")
        else:
            if not headless:
                try:
                    req = urllib.request.Request("http://127.0.0.1:59284/show", method="POST")
                    with urllib.request.urlopen(req, timeout=2):
                        pass
                except Exception:
                    pass
        sys.exit(0)

    # 2. Start Uvicorn FastAPI backend in background thread
    import uvicorn

    def start_server():
        global uvicorn_server
        full_silence_config = {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {"()": "uvicorn.logging.DefaultFormatter", "fmt": "%(message)s", "use_colors": False},
                "access": {"()": "uvicorn.logging.AccessFormatter", "fmt": "%(message)s", "use_colors": False},
            },
            "handlers": {"null": {"class": "logging.NullHandler"}},
            "loggers": {
                "uvicorn": {"handlers": ["null"], "level": "CRITICAL"},
                "uvicorn.error": {"handlers": ["null"], "level": "CRITICAL"},
                "uvicorn.access": {"handlers": ["null"], "level": "CRITICAL"},
            },
        }
        config_obj = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=59284,
            reload=False,
            log_level="critical",
            log_config=full_silence_config,
        )
        uvicorn_server = uvicorn.Server(config_obj)
        import main
        main.uvicorn_server = uvicorn_server
        uvicorn_server.run()

    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    # 3. Create Window API for window controls
    api = LinuxWindowAPI()

    # 4. Determine theme and create loading screen
    theme = detect_linux_theme()
    if theme == "light":
        bg_color = "#ffffff"
        text_color = "#1f2937"
        spinner_bg = "rgba(234, 88, 12, 0.1)"
        spinner_fg = "#ea580c"
        status_color = "#6b7280"
    else:
        bg_color = "#0f0f11"
        text_color = "#f3f4f6"
        spinner_bg = "rgba(249, 115, 22, 0.1)"
        spinner_fg = "#f97316"
        status_color = "#9ca3af"

    loading_html = f"""<!DOCTYPE html>
<html>
<head>
    <style>
        body {{
            margin: 0;
            padding: 0;
            background: {bg_color};
            color: {text_color};
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            display: flex;
            align-items: center;
            justify-content: center;
            height: 100vh;
            overflow: hidden;
            -webkit-user-select: none;
            user-select: none;
        }}
        .container {{ text-align: center; }}
        .logo {{
            font-size: 2.5rem;
            font-weight: 800;
            background: linear-gradient(135deg, #f97316 0%, #ea580c 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 1rem;
            letter-spacing: -0.05em;
        }}
        .spinner {{
            width: 40px;
            height: 40px;
            border: 3px solid {spinner_bg};
            border-radius: 50%;
            border-top-color: {spinner_fg};
            animation: spin 1s ease-in-out infinite;
            margin: 0 auto 1.5rem auto;
        }}
        @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
        .status {{
            font-size: 0.875rem;
            color: {status_color};
            letter-spacing: 0.05em;
            text-transform: uppercase;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="logo">BURST</div>
        <div class="spinner"></div>
        <div class="status">Connecting to engine...</div>
    </div>
</body>
</html>"""

    # 5. Background loader logic
    def load_logic(window):
        server_ready = False
        for _ in range(50):
            try:
                urllib.request.urlopen("http://127.0.0.1:59284/interfaces?benchmark=false", timeout=0.1)
                server_ready = True
                break
            except Exception:
                time.sleep(0.1)

        if not server_ready:
            window.load_html("<h3>Burst could not start</h3><p>Local API server did not become ready.</p>")
            return

        time.sleep(0.05)
        window.load_url("http://127.0.0.1:59284")

    # 6. Check for graphical display
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

    if not has_display or headless:
        print("[Burst-Linux] Backend engine running at http://127.0.0.1:59284 (headless mode)", flush=True)
        setup_tray(None)
        try:
            while not is_quitting and (uvicorn_server is None or not getattr(uvicorn_server, "should_exit", False)):
                time.sleep(0.5)
        except (KeyboardInterrupt, SystemExit):
            handle_shutdown()
        handle_shutdown()
        return

    # 7. Launch pywebview with GTK3
    import webview

    window = webview.create_window(
        "Burst",
        html=loading_html,
        width=950,
        height=680,
        frameless=True,
        easy_drag=True,
        resizable=False,
        hidden=headless,
        js_api=api,
        background_color=bg_color,
    )
    api.set_window(window)
    window_ref = window

    def on_closing():
        global is_quitting
        if is_quitting:
            return True
        if window:
            window.hide()
        return False

    window.events.closing += on_closing
    setup_tray(window)

    print("[Burst-Linux] Starting pywebview with GTK GUI...", flush=True)
    webview.start(load_logic, window, gui="gtk")


if __name__ == "__main__":
    main()
