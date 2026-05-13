import json
import os
from pathlib import Path
from typing import List, Dict, Any

HISTORY_FILE = Path("burst_history.json")

def load_history() -> List[Dict[str, Any]]:
    """Load history from disk."""
    if not HISTORY_FILE.exists():
        return []
    try:
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"[HISTORY] Load error: {e}")
        return []

def save_to_history(job_dict: Dict[str, Any]):
    """Append a completed/failed job to history file."""
    history = load_history()
    
    # Check for duplicates by job_id
    if any(item.get("job_id") == job_dict.get("job_id") for item in history):
        return
        
    history.insert(0, job_dict)
    
    # Keep only the last 100 entries to keep it fast
    history = history[:100]
    
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        print(f"[HISTORY] Save error: {e}")

def clear_history():
    """Delete the history file."""
    try:
        if HISTORY_FILE.exists():
            HISTORY_FILE.unlink()
    except Exception as e:
        print(f"[HISTORY] Clear error: {e}")
