# ═══════════════════════════════════════════════════════════════
# CHANGES NEEDED IN backend/config.py
# ═══════════════════════════════════════════════════════════════
# In your _DEFAULTS dictionary, add this one line:

_DEFAULTS = {
    # ... your existing keys ...
    "DEFAULT_DOWNLOAD_PATH": "C:\\Downloads",   # ← ADD THIS
}

# ═══════════════════════════════════════════════════════════════
# CHANGES NEEDED IN frontend/src/App.jsx (or wherever you read
# burst_default_path from localStorage)
# ═══════════════════════════════════════════════════════════════
# Replace:
#   const savedPath = localStorage.getItem("burst_default_path") || "C:\\Downloads\\";
#
# With a fetch from the backend settings endpoint:
#   const res = await fetch("http://localhost:8000/settings");
#   const { settings } = await res.json();
#   const savedPath = settings.DEFAULT_DOWNLOAD_PATH || "C:\\Downloads";
#
# And when saving path changes, POST to /settings instead of localStorage.
# This makes the native host and the UI share the same source of truth.
