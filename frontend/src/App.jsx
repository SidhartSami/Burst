import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  AlertCircle,
  Inbox,
  CheckCircle2,
  Folder,
  Magnet,
  Pause,
  Settings,
  X,
  Download,
  Zap,
  History,
  Search,
  FolderOpen,
  Minus,
  Square,
  Play,
  Moon,
  Sun,
  Menu
} from "lucide-react";

const API_BASE = (window.location.port === "5173" || window.location.port === "4173")
  ? "http://127.0.0.1:59284"
  : window.location.origin;
const HISTORY_KEY = "burst_history";

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let idx = 0;
  while (value >= 1024 && idx < units.length - 1) {
    value /= 1024;
    idx += 1;
  }
  const rounded = idx >= 2 ? Math.round(value) : value.toFixed(0);
  return `${rounded} ${units[idx]}`;
}

function formatSpeed(mbps) {
  if (!mbps) return "0 B/s";
  const bytesPerSec = Number(mbps) * 1024 * 1024;
  if (bytesPerSec < 1024) return `${bytesPerSec.toFixed(0)} B/s`;
  if (bytesPerSec < 1024 * 1024) return `${(bytesPerSec / 1024).toFixed(1)} KB/s`;
  if (bytesPerSec < 1024 * 1024 * 1024) return `${(bytesPerSec / (1024 * 1024)).toFixed(2)} MB/s`;
  return `${(bytesPerSec / (1024 * 1024 * 1024)).toFixed(2)} GB/s`;
}

function formatETA(seconds) {
  if (!seconds || seconds <= 0 || !isFinite(seconds)) return "calculating...";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) {
    const m = Math.floor(seconds / 60);
    const s = Math.round(seconds % 60);
    return s > 0 ? `${m}m ${s}s` : `${m}m`;
  }
  if (seconds < 86400) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return m > 0 ? `${h}h ${m}m` : `${h}h`;
  }
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  return h > 0 ? `${d}d ${h}h` : `${d}d`;
}

function shortName(name, type) {
  const lowered = String(name || "").toLowerCase();
  if (lowered.includes("wi-fi") || lowered.includes("wifi")) return "Wi-Fi";
  if (lowered.includes("usb") || lowered.includes("rndis")) return "Phone";
  if (String(type || "").toLowerCase().includes("ethernet")) return "Ethernet";
  return name || "Adapter";
}

function toneFor(name) {
  const lowered = String(name || "").toLowerCase();
  if (/(wi-?fi|wireless|wlan)/i.test(lowered)) {
    return { dot: "var(--wifi-color)" };
  }
  if (/(phone|usb|rndis|mobile|samsung|huawei|xiaomi)/i.test(lowered)) {
    return { dot: "var(--ethernet-color)" };
  }
  return { dot: "var(--extra-color)" };
}

function readDroppedUrl(event) {
  const uriList = event.dataTransfer.getData("text/uri-list");
  if (uriList) return uriList.split("\n").find((line) => line.startsWith("http")) || "";
  const plainText = event.dataTransfer.getData("text/plain");
  if (plainText && /^https?:\/\//i.test(plainText.trim())) return plainText.trim();
  return "";
}

function PromptModal({ isOpen, title, defaultValue, onConfirm, onCancel }) {
  const [value, setValue] = useState(defaultValue);
  if (!isOpen) return null;
  return (
    <div className="modal-overlay">
      <div className="modal-content slide-in">
        <h3>{title}</h3>
        <input 
          autoFocus 
          type="number" 
          value={value} 
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && onConfirm(value)}
        />
        <div className="modal-actions">
          <button className="btn-secondary" onClick={onCancel}>Cancel</button>
          <button className="btn-primary" onClick={() => onConfirm(value)}>OK</button>
        </div>
      </div>
    </div>
  );
}

function DownloadCard({ jid, status, availableInterfaces, onToggle, onCancel, onPause, onResume, allUsedIps }) {
  if (!status) return null;

  const currentInterfacesProgress = status.type === "torrent"
    ? Object.keys(status.speeds || {}).map(ip => ({
      ip_address: ip,
      speed_mb_s: (status.speeds[ip] || 0) / (1024 * 1024),
      peers: status.peers_per_interface?.[ip] || 0
    }))
    : Object.values(status.interfaces || {});

  const [isOptimistic, setIsOptimistic] = useState(null);

  const activeIfaces = status.type === "torrent"
    ? (status.interface_ips || []).length
    : Object.values(status.interfaces || {}).filter(i => i.status !== "excluded" && i.status !== "cancelled").length;

  const isPaused = status.status === 'paused' || status.status === 'waiting_reconnect' || (status.status === 'downloading' && activeIfaces === 0);
  const statusLabel = isPaused ? 'PAUSED' : status.status;
  const statusClass = status.status === 'completed' ? 'completed' : (status.status === 'failed' ? 'failed' : (isPaused ? 'paused' : 'downloading'));

  const pct = Math.min(100, ((status.total_downloaded || 0) / Math.max(1, status.expected_size || 1)) * 100);

  // Force speed to 0 if paused to avoid unprofessional jitter/rolling averages
  const speedRaw = status.type === "torrent"
    ? (status.speed_combined || 0) / (1024 * 1024)
    : currentInterfacesProgress.reduce((sum, item) => sum + Number(item.speed_mb_s || 0), 0);

  const combinedCurrentSpeed = isPaused ? 0 : speedRaw;
  const eta = (!isPaused && combinedCurrentSpeed > 0) ? (status.expected_size - status.total_downloaded) / (combinedCurrentSpeed * 1024 * 1024) : 0;

  return (
    <div className="dl-card slide-in">
      <div className="dl-card-top">
        <div className="dl-title-row">
          <div className="dl-filename">{status.output_path?.split(/[\\/]/).pop() || "download.bin"}</div>
          <div className={`status-badge ${statusClass}`}>{statusLabel}</div>
        </div>
        <div className="dl-actions">
          {status.status === 'downloading' && status.type !== 'torrent' && (
            <button className="action-btn" onClick={onPause} title="Pause"><Pause size={16} /></button>
          )}
          {(status.status === 'paused' || status.status === 'waiting_reconnect') && (
            <button className="action-btn" onClick={onResume} title="Resume"><Play size={16} /></button>
          )}
          <button className="action-btn" onClick={onCancel} title="Cancel"><X size={16} /></button>
          <div className="dl-speed">{formatSpeed(combinedCurrentSpeed)}</div>
        </div>
      </div>

      <div className="iface-pills">
        {availableInterfaces.map(iface => {
          let live = null;
          let isSelected = false;

          if (status.type === "torrent") {
            isSelected = status.interface_ips?.includes(iface.ip_address) ?? (status.speeds && iface.ip_address in status.speeds);
            if (isSelected) {
              const speedBytes = status.speeds?.[iface.ip_address] || 0;
              live = {
                status: 'downloading',
                speed_mb_s: speedBytes / (1024 * 1024)
              };
            }
          } else {
            live = status.interfaces?.[iface.ip_address];
            isSelected = !!live && live.status !== "excluded" && live.status !== "cancelled";
          }

          if (isOptimistic && isOptimistic.ip === iface.ip_address) {
            isSelected = isOptimistic.selected;
          }

          const speed = isPaused ? 0 : (live?.speed_mb_s || 0);
          const tone = toneFor(shortName(iface.name, iface.interface_type));
          const isShared = allUsedIps.filter(ip => ip === iface.ip_address).length > 1;

          return (
            <div
              key={iface.ip_address}
              className={`iface-pill ${isSelected ? 'active' : ''}`}
              onClick={() => {
                if (status.status === 'completed' || status.status === 'failed') return;
                setIsOptimistic({ ip: iface.ip_address, selected: !isSelected });
                onToggle(iface.ip_address, isSelected).finally(() => {
                  setTimeout(() => setIsOptimistic(null), 1000);
                });
              }}
            >
              <div className="dot" style={{ background: isSelected ? tone.dot : 'var(--text-muted)' }} />
              {shortName(iface.name, iface.interface_type)}
              {isSelected && speed > 0 && <span style={{ opacity: 0.8 }}>{Number(speed).toFixed(1)} MB/s</span>}
              {isSelected && isShared && status.status === 'downloading' && <AlertTriangle size={12} style={{ color: 'var(--warning)', marginLeft: '2px' }} title="Shared with another download" />}
            </div>
          );
        })}
      </div>

      <div className="progress-track">
        <div className={`progress-fill ${statusClass}`} style={{ width: `${pct}%` }} />
      </div>

      <div className="dl-bottom">
        <span>{pct.toFixed(1)}% • {formatBytes(status.total_downloaded)} / {formatBytes(status.expected_size)}</span>
        <span>{!isPaused && status.status === 'downloading' ? formatETA(eta) : ''}</span>
      </div>
    </div>
  );
}

export default function App() {
  const [activeTab, setActiveTab] = useState("active");
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  const [themeMode, setThemeMode] = useState(() => localStorage.getItem("burst_theme_mode") || "dark");
  const isDarkMode = themeMode === "dark" || (themeMode === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);

  const [interfaces, setInterfaces] = useState([]);
  const [renderedInterfaces, setRenderedInterfaces] = useState([]);
  const [selectedIps, setSelectedIps] = useState([]);
  const [url, setUrl] = useState("");
  const [outputPath, setOutputPath] = useState("C:\\Downloads\\burst-download.bin");
  const [activeJobs, setActiveJobs] = useState([]);
  const [jobStatuses, setJobStatuses] = useState({});
  const [history, setHistory] = useState(() => {
    const saved = localStorage.getItem(HISTORY_KEY);
    return saved ? JSON.parse(saved) : [];
  });

  const groupedHistory = useMemo(() => {
    const groups = {};
    history.forEach(item => {
      const dateStr = item.timestamp ? item.timestamp.split(',')[0] : 'Unknown Date';
      if (!groups[dateStr]) groups[dateStr] = [];
      groups[dateStr].push(item);
    });
    return groups;
  }, [history]);
  const [dragOverlay, setDragOverlay] = useState(false);
  const [toast, setToast] = useState("");
  const [downloadBtnState, setDownloadBtnState] = useState("idle");
  const [editingOutputPath, setEditingOutputPath] = useState(false);
  const [bandwidthLimits, setBandwidthLimits] = useState({});
  const [appSettings, setAppSettings] = useState(null);
  const [promptData, setPromptData] = useState(null); // { title, value, ip }

  const jobSocketsRef = useRef({});

  useEffect(() => {
    if (isDarkMode) document.body.classList.add("dark");
    else document.body.classList.remove("dark");
    localStorage.setItem("burst_theme_mode", themeMode);
  }, [isDarkMode, themeMode]);

  useEffect(() => {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history));
  }, [history]);

  useEffect(() => {
    // 1. Restore local defaults immediately
    const savedLimits = JSON.parse(localStorage.getItem("burst_bandwidth_limits") || "{}");
    setBandwidthLimits(savedLimits);
    const savedPath = localStorage.getItem("burst_default_path") || "C:\\Downloads\\";
    if (!url) setOutputPath(savedPath + (url ? "" : "burst-download.bin"));

    // 2. Fetch settings from backend in background
    fetch(`${API_BASE}/settings`)
      .then(r => r.json())
      .then(d => {
        setAppSettings(d.settings);
        if (d.settings.THEME_MODE) {
          setThemeMode(d.settings.THEME_MODE);
        }
        if (d.settings.DOWNLOAD_PATH && !url) {
          setOutputPath(d.settings.DOWNLOAD_PATH);
        }
      })
      .catch(() => { });

    // 3. Fetch active jobs so background downloads added while UI was closed
    //    are immediately visible on mount. The existing useEffect([activeJobs])
    //    will automatically open a WebSocket for each new ID returned here.
    fetch(`${API_BASE}/active-jobs`)
      .then(r => r.json())
      .then(d => {
        if (d.job_ids && d.job_ids.length > 0) {
          setActiveJobs(prev => {
            const merged = [...prev];
            d.job_ids.forEach(id => { if (!merged.includes(id)) merged.push(id); });
            return merged;
          });
        }
      })
      .catch(() => {});

    // 4. Hydrate history from the backend (includes jobs from previous sessions).
    //    We only set history from the server if localStorage is empty so we don't
    //    overwrite user-visible state on a normal reload.
    fetch(`${API_BASE}/history`)
      .then(r => r.json())
      .then(d => {
        if (d.history && d.history.length > 0) {
          setHistory(prev => {
            if (prev.length > 0) return prev; // already have local history — don't stomp
            return d.history.map(item => ({
              id: item.job_id || item.id || crypto.randomUUID(),
              filename: item.filename || item.output_path?.split(/[\\/]/).pop() || "download.bin",
              path: item.output_path || item.path || "Unknown path",
              size: item.total_downloaded || item.size || 0,
              avgSpeed: item.speed_combined || item.avgSpeed || 0,
              time_saved: item.time_saved || 0,
              status: item.status || "completed",
              timestamp: item.finished_at
                ? new Date(item.finished_at * 1000).toLocaleString()
                : (item.timestamp || new Date().toLocaleString()),
              type: item.type || "download"
            })).slice(0, 50);
          });
        }
      })
      .catch(() => {});

    // 5. Listen for global events (like new downloads from extension)
    const eventWsUrl = API_BASE.replace("http", "ws") + "/ws/events";
    let eventWs = new WebSocket(eventWsUrl);
    
    eventWs.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "new_job") {
        const jobId = msg.data.job_id;
        setActiveJobs(prev => {
          if (prev.includes(jobId)) return prev;
          return [...prev, jobId];
        });
      }
    };

    // Reconnect logic
    eventWs.onclose = () => {
      setTimeout(() => {
        // Simple reconnect logic would go here if needed
      }, 5000);
    };

    return () => eventWs.close();
  }, []);

  useEffect(() => {
    if (toast) {
      const timer = setTimeout(() => setToast(""), 5000);
      return () => clearTimeout(timer);
    }
  }, [toast]);

  useEffect(() => {
    localStorage.setItem("burst_bandwidth_limits", JSON.stringify(bandwidthLimits));
  }, [bandwidthLimits]);

  const handleUrlChange = (newUrl) => {
    setUrl(newUrl);
    const isTorrent = newUrl.trim().startsWith("magnet:") || newUrl.trim().endsWith(".torrent");
    // Helper to get directory only
    const getBaseDir = () => {
      if (outputPath) {
        const parts = outputPath.split(/[\\\/]/);
        parts.pop();
        return parts.join("\\") + "\\";
      }
      return "C:\\Downloads\\";
    };

    const baseDir = getBaseDir();

    if (isTorrent) {
      const magnetNameMatch = newUrl.match(/dn=([^&]+)/);
      const magnetName = magnetNameMatch ? decodeURIComponent(magnetNameMatch[1]).replace(/\+/g, ' ') : "torrent-download";
      setOutputPath(baseDir + magnetName);
      return;
    }
    try {
      const urlObj = new URL(newUrl);
      const pathname = urlObj.pathname;
      if (pathname && pathname !== "/") {
        const segments = pathname.split("/");
        const lastSegment = segments[segments.length - 1];
        if (lastSegment && !lastSegment.includes("?") && lastSegment.includes(".")) {
          setOutputPath(baseDir + lastSegment);
          return;
        }
      }
      setOutputPath(baseDir + "burst-download.bin");
    } catch {
      setOutputPath(baseDir + "burst-download.bin");
    }
  };

  const handleBrowsePath = async (callback) => {
    try {
      const resp = await fetch(`${API_BASE}/select-path`);
      const data = await resp.json();
      if (data.path) {
        callback(data.path);
      }
    } catch (err) {
      setToast("Failed to open file explorer");
    }
  };

  const handleWindowAction = (action) => {
    if (window.pywebview && window.pywebview.api) {
      if (action === 'close') window.pywebview.api.close();
      else if (action === 'minimize') window.pywebview.api.minimize();
      else if (action === 'maximize') window.pywebview.api.maximize();
    } else {
      // Fallback for browser testing
      if (action === 'close') {
        if (window.confirm("Quit Burst?")) window.close();
      } else {
        setToast(`${action.charAt(0).toUpperCase() + action.slice(1)} is handled by the OS.`);
      }
    }
  };

  const mergeInterfacesForUi = (incoming) => {
    setRenderedInterfaces((prev) => {
      const nextMap = new Map(incoming.map((item) => [item.ip_address, item]));
      const prevMap = new Map(prev.map((item) => [item.ip_address, item]));
      const merged = [];

      for (const oldItem of prev) {
        const fresh = nextMap.get(oldItem.ip_address);
        if (!fresh) {
          if (!oldItem.exiting) merged.push({ ...oldItem, exiting: true, entering: false });
          else merged.push(oldItem);
          continue;
        }
        merged.push({ ...oldItem, ...fresh, entering: false, exiting: false });
      }

      for (const fresh of incoming) {
        if (!prevMap.has(fresh.ip_address)) {
          merged.push({ ...fresh, entering: true, exiting: false, speedFlash: false });
        }
      }
      return merged;
    });
  };

  const fetchInterfaces = async () => {
    try {
      const resp = await fetch(`${API_BASE}/interfaces`);
      if (!resp.ok) return;
      const data = await resp.json();
      const fresh = data.interfaces || [];
      setInterfaces(fresh);
      mergeInterfacesForUi(fresh);

      setSelectedIps((prev) =>
        prev.length ? prev.filter((ip) => fresh.some((item) => item.ip_address === ip)) : fresh.map((item) => item.ip_address)
      );
    } catch { }
  };

  const runSpeedtestSilent = async () => {
    try {
      const resp = await fetch(`${API_BASE}/speedtest`, { method: "POST" });
      const data = await resp.json();
      if (!resp.ok) return;
      setRenderedInterfaces((prev) =>
        prev.map((iface) => {
          const found = (data.results || []).find((r) => r.ip_address === iface.ip_address);
          if (!found) return iface;
          return { ...iface, speed_mb_s: found.speed_mb_s };
        })
      );
    } catch { }
  };

  useEffect(() => {
    fetchInterfaces();
    const t1 = setInterval(fetchInterfaces, 15000);
    const t2 = setInterval(runSpeedtestSilent, 20000);
    return () => { clearInterval(t1); clearInterval(t2); };
  }, []);

  const startDownload = async (forceUrl = null, forcePath = null) => {
    const cleanUrl = (forceUrl || url).trim();
    const cleanOutputPath = (forcePath || outputPath).trim();
    const effectiveIps = selectedIps.length ? selectedIps : renderedInterfaces.map(i => i.ip_address);
    const isTorrent = cleanUrl.startsWith("magnet:?") || cleanUrl.endsWith(".torrent");

    try {
      const endpoint = isTorrent ? `${API_BASE}/torrent/start` : `${API_BASE}/download`;
      const body = isTorrent
        ? { magnet_uri: cleanUrl, output_path: cleanOutputPath, interface_ips: effectiveIps, bandwidth_limits: bandwidthLimits }
        : { url: cleanUrl, output_path: cleanOutputPath, interface_ips: effectiveIps, bandwidth_limits: bandwidthLimits };

      const resp = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || "Failed to start");
      
      if (data.job_id) {
        setActiveJobs(prev => {
          if (prev.includes(data.job_id)) return prev;
          return [...prev, data.job_id];
        });
        setUrl("");
        setActiveTab("active");
      }
    } catch (err) {
      setToast(err.message);
    }
  };

  const handleDownloadClick = async () => {
    if (downloadBtnState === "checking") return;
    const targetUrl = url.trim();
    if (!targetUrl) return;

    const isTorrent = targetUrl.startsWith("magnet:?") || targetUrl.endsWith(".torrent");
    if (!isTorrent) {
      setDownloadBtnState("checking");
      try {
        const resp = await fetch(`${API_BASE}/analyze`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url: targetUrl, interface_ip: selectedIps[0] || null })
        });
        const data = await resp.json();
        setDownloadBtnState("idle");
        if (!resp.ok) { setToast(data.detail || "URL unreachable"); return; }
      } catch (err) {
        setDownloadBtnState("idle");
        setToast("Connection error");
        return;
      }
    }
    startDownload();
  };

  useEffect(() => {
    Object.keys(jobSocketsRef.current).forEach(id => {
      if (!activeJobs.includes(id)) {
        jobSocketsRef.current[id].close();
        delete jobSocketsRef.current[id];
      }
    });
    activeJobs.forEach(id => {
      if (!jobSocketsRef.current[id]) {
        const ws = new WebSocket(`${API_BASE.replace("http", "ws")}/ws/${id}`);
        ws.onmessage = (event) => {
          const payload = JSON.parse(event.data);
          setJobStatuses(prev => ({ ...prev, [id]: payload }));
          if (payload.status === "completed" || payload.status === "failed") {
            const duration = Math.max(0, (payload.finished_at || 0) - (payload.started_at || 0));
            const historyItem = {
              id: payload.job_id || crypto.randomUUID(),
              filename: payload.output_path?.split(/[\\/]/).pop() || "download.bin",
              path: payload.output_path || "Unknown path",
              size: payload.total_downloaded,
              avgSpeed: payload.speed_combined || 0,
              time_saved: payload.time_saved || 0,
              status: payload.status,
              timestamp: new Date().toLocaleString(),
              type: payload.type || (activeTab === 'torrents' ? 'torrent' : 'download')
            };
            setHistory(prev => [historyItem, ...prev].slice(0, 50));
          }
        };
        jobSocketsRef.current[id] = ws;
      }
    });
  }, [activeJobs]);

  useEffect(() => {
    const raw = localStorage.getItem(HISTORY_KEY);
    if (raw) setHistory(JSON.parse(raw).slice(0, 50));
  }, []);

  useEffect(() => {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history));
  }, [history]);

  const allUsedIps = useMemo(() => {
    return Object.keys(jobStatuses)
      .filter(jid => activeJobs.includes(jid))
      .filter(jid => jobStatuses[jid].status === "downloading")
      .flatMap(jid => {
        const s = jobStatuses[jid];
        if (s.type === "torrent") return s.interface_ips || [];
        return Object.values(s.interfaces || {})
          .filter(i => i.status !== "excluded" && i.status !== "cancelled" && i.status !== "disconnected")
          .map(i => i.ip_address);
      });
  }, [jobStatuses, activeJobs]);

  const clearHistory = () => setHistory([]);

  const activeConnectionCount = interfaces.length;

  return (
    <div className={`app-container ${isDarkMode ? 'dark' : ''}`} onDragOver={e => { e.preventDefault(); setDragOverlay(true); }}>
      {dragOverlay && (
        <div className="drag-overlay" onDragLeave={() => setDragOverlay(false)} onDrop={e => {
          e.preventDefault();
          setDragOverlay(false);
          const droppedUrl = readDroppedUrl(e);
          if (droppedUrl) handleUrlChange(droppedUrl);
        }}>
          <div className="drag-overlay-content">Drop URL to download</div>
        </div>
      )}

      <div className="titlebar">
        <div className="titlebar-left">
          <button className="titlebar-btn" onClick={() => setIsSidebarCollapsed(!isSidebarCollapsed)}>
            <Menu size={16} />
          </button>
          <div className="titlebar-title">
            <img src="/logo.png" alt="Burst" style={{ width: '18px', height: '18px', objectFit: 'contain' }} />
            Burst
          </div>
        </div>
        <div className="titlebar-drag-region" />
        <div className="window-controls">
          <button className="window-btn" onClick={() => handleWindowAction('minimize')}><Minus size={16} /></button>
          <button className="window-btn close" onClick={() => handleWindowAction('close')}><X size={16} /></button>
        </div>
      </div>

      <div className={`main-layout ${isSidebarCollapsed ? 'collapsed' : ''}`}>
        <aside className="sidebar">
          <div className="nav-list" style={{ marginTop: '12px' }}>
            <button className={`nav-item ${activeTab === 'active' ? 'active' : ''}`} onClick={() => setActiveTab('active')} title="Active Downloads">
              <Download size={16} /> {!isSidebarCollapsed && "Active"}
            </button>
            <button className={`nav-item ${activeTab === 'history' ? 'active' : ''}`} onClick={() => setActiveTab('history')} title="History">
              <History size={16} /> {!isSidebarCollapsed && "History"}
            </button>
            <button className={`nav-item ${activeTab === 'connections' ? 'active' : ''}`} onClick={() => setActiveTab('connections')} title="Connections">
              <Zap size={16} /> {!isSidebarCollapsed && "Connections"}
            </button>
            <button className={`nav-item ${activeTab === 'settings' ? 'active' : ''}`} onClick={() => setActiveTab('settings')} title="Settings">
              <Settings size={16} /> {!isSidebarCollapsed && "Settings"}
            </button>
          </div>

          {!isSidebarCollapsed && (
            <div className="sidebar-footer">
              <div className="conn-pill">
                <div className="conn-dot" style={{ background: activeConnectionCount > 0 ? 'var(--success)' : 'var(--danger)' }} />
                {activeConnectionCount} connected
              </div>
            </div>
          )}
        </aside>

        <main className="content-area">
          {activeTab === 'active' && (
            <>
              <div className="top-controls">
                <div className="input-group">
                  <input
                    type="text"
                    className="url-input"
                    placeholder="Paste a URL, magnet link, or .torrent..."
                    value={url}
                    onChange={(e) => handleUrlChange(e.target.value)}
                  />
                  <button
                    className="btn-primary"
                    onClick={handleDownloadClick}
                    disabled={downloadBtnState === "checking"}
                  >
                    {downloadBtnState === "checking" ? <div className="spinner-small" /> : <Download size={18} />}
                    {downloadBtnState === "checking" ? "Checking..." : "Download"}
                  </button>
                </div>

                <div className="path-row">
                  <button className="browse-btn-icon" onClick={async () => {
                    try {
                      const resp = await fetch(`${API_BASE}/select-path`);
                      const data = await resp.json();
                      if (data.path) setOutputPath(data.path + (url ? "" : "\\burst-download.bin"));
                    } catch { }
                  }} title="Browse directory">
                    <FolderOpen size={16} />
                  </button>
                  <div className="path-input-container" onClick={() => setEditingOutputPath(true)}>
                    <div className="path-text">
                      {editingOutputPath ? (
                        <input
                          autoFocus
                          value={outputPath}
                          onChange={(e) => setOutputPath(e.target.value)}
                          onBlur={() => setEditingOutputPath(false)}
                          onKeyDown={(e) => e.key === 'Enter' && setEditingOutputPath(false)}
                        />
                      ) : outputPath}
                    </div>
                  </div>
                </div>
              </div>

              <div className="content-body">
                <div className="section-label">Active Downloads</div>
                {activeJobs.length === 0 && (
                    <div className="empty-state slide-in">
                      <div className="empty-icon-wrapper">
                        <Inbox size={32} strokeWidth={1.5} />
                      </div>
                      <h3>No active {activeTab}</h3>
                      <p>Paste a link above to start your first speed-bonded download.</p>
                    </div>
                  )}
                {activeJobs.map(jid => (
                  <DownloadCard
                    key={jid}
                    jid={jid}
                    status={jobStatuses[jid]}
                    availableInterfaces={renderedInterfaces}
                    allUsedIps={allUsedIps}
                    onToggle={async (ip, selected) => {
                      const endpoint = selected ? 'remove_interface' : 'add_interface';
                      return fetch(`${API_BASE}/download/${jid}/${endpoint}`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ interface_ip: ip })
                      }).then(async r => {
                        if (!r.ok) setToast(await r.text());
                      });
                    }}
                    onCancel={() => {
                      fetch(`${API_BASE}/download/${jid}/cancel`, { method: "POST" });
                      setActiveJobs(prev => prev.filter(x => x !== jid));
                    }}
                    onPause={() => fetch(`${API_BASE}/download/${jid}/pause`, { method: "POST" })}
                    onResume={() => fetch(`${API_BASE}/download/${jid}/resume`, { method: "POST" })}
                  />
                ))}

                {/* Recently Completed Section */}
                {history.length > 0 && (
                  <div className="recent-completed-section">
                    <div className="section-label">Recently Completed</div>
                    {history
                      .slice(0, 3)
                      .map(item => (
                        <div className="completed-row mini" key={item.id}>
                          {item.status === 'failed' ? <AlertCircle size={14} color="var(--danger)" /> : <CheckCircle2 size={14} color="var(--success)" />}
                          <div className="completed-filename">{item.filename}</div>
                          <div className="completed-meta">
                            <span>{formatBytes(item.size)}</span>
                          </div>
                        </div>
                      ))
                    }
                  </div>
                )}
              </div>
            </>
          )}

          {activeTab === 'history' && (
            <div className="content-body">
              <div className="history-header">
                <div className="section-label">History</div>
                <button onClick={clearHistory} className="clear-btn">Clear History</button>
              </div>
              {history.length === 0 && (
                <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-muted)', fontSize: '13px' }}>
                  No completed downloads yet.
                </div>
              )}
              {Object.keys(groupedHistory).map(date => (
                <div className="history-date-group" key={date}>
                  <div className="history-date-label">{date}</div>
                  {groupedHistory[date].map((item) => (
                    <div className="completed-row" key={item.id ?? Math.random()}>
                      <div className="completed-icon">
                        {item.status === 'failed' ? <AlertCircle size={18} color="var(--danger)" /> : <CheckCircle2 size={18} color="var(--success)" />}
                      </div>
                      <div className="completed-info">
                        <div className="completed-filename" style={{ color: item.status === 'failed' ? "var(--danger)" : "var(--text)" }}>{item.filename || 'Unknown'}</div>
                        <div className="completed-path">{item.path}</div>
                        <div className="completed-meta-row">
                          <span className="meta-tag">{item.timestamp.split(',')[1]}</span>
                          <span className="meta-tag">{formatBytes(item.size)}</span>
                          {item.status !== 'failed' && <span className="meta-tag">{formatSpeed(item.avgSpeed)} avg</span>}
                        </div>
                      </div>
                      {item.time_saved > 0 && <div className="completed-saved">Saved {formatETA(item.time_saved)}</div>}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          )}

          {activeTab === 'connections' && (
            <div className="content-body">
              <div className="section-label">Interfaces</div>
              <table className="conn-table">
                <tbody>
                  {renderedInterfaces.map((iface) => (
                    <tr className="conn-row" key={iface.ip_address}>
                      <td>
                        <div className="conn-name">
                          <div className="conn-dot" style={{ width: 8, height: 8, borderRadius: '50%', background: toneFor(shortName(iface.name, iface.interface_type)).dot }} />
                          {iface.name || iface.ip_address}
                          <span className="conn-type">{shortName(iface.name, iface.interface_type)}</span>
                        </div>
                      </td>
                      <td className="conn-speed">
                        {iface.speed_mb_s ? `${iface.speed_mb_s.toFixed(2)} MB/s` : '0.00 MB/s'}
                      </td>
                      <td>
                        <div className="conn-actions">
                          {bandwidthLimits[iface.ip_address] && <span style={{ fontSize: '11px', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', paddingRight: '8px' }}>Limit: {(bandwidthLimits[iface.ip_address] / 1024 / 1024).toFixed(1)} MB/s</span>}
                          <button className="btn-small" onClick={() => {
                            setPromptData({
                              title: "Set bandwidth limit (MB/s)",
                              ip: iface.ip_address,
                              value: bandwidthLimits[iface.ip_address] ? (bandwidthLimits[iface.ip_address] / 1024 / 1024).toString() : ""
                            });
                          }}>Set limit (MB/s)</button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {activeTab === 'settings' && (
            <div className="content-body">
              <div className="settings-header">
                <div className="section-label">Settings</div>
                <div className="settings-actions">
                  <button className="clear-btn" onClick={() => {
                    fetch(`${API_BASE}/settings/reset`, { method: "POST" }).then(r => r.json()).then(d => {
                      setAppSettings(d.settings);
                      setToast("Settings reset to defaults.");
                    }).catch(() => { });
                  }}>Reset Defaults</button>
                  <button className="btn-primary-small" onClick={() => {
                    fetch(`${API_BASE}/settings`, {
                      method: "POST",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ settings: appSettings })
                    }).then(() => setToast("Settings saved.")).catch(() => { });
                  }}>Save Settings</button>
                </div>
              </div>
              {!appSettings ? (
                <div style={{ color: 'var(--text-muted)', fontSize: '13px' }}>Loading settings...</div>
              ) : (
                <div className="settings-grid">
                  <div className="settings-section-title">General</div>
                  <label className="setting-row">
                    <span>Default Download Path</span>
                    <div className="setting-input-wrap">
                      <input
                        type="text"
                        style={{ width: '240px' }}
                        value={appSettings?.DEFAULT_DOWNLOAD_PATH || "C:\\Downloads"}
                        onChange={(e) => {
                          setAppSettings({ ...appSettings, DEFAULT_DOWNLOAD_PATH: e.target.value });
                        }}
                      />
                      <button className="btn-small" onClick={() => handleBrowsePath(p => {
                        setAppSettings({ ...appSettings, DEFAULT_DOWNLOAD_PATH: p });
                        setToast("Path selected. Remember to Save Settings.");
                      })}>Browse</button>
                    </div>
                  </label>
                  <label className="setting-row">
                    <span>Theme Mode</span>
                    <div className="setting-input-wrap">
                      <select
                        value={themeMode}
                        onChange={(e) => {
                          const newMode = e.target.value;
                          setThemeMode(newMode);
                          setAppSettings(prev => ({ ...prev, THEME_MODE: newMode }));
                          // Auto-save theme preference to backend instantly
                          fetch(`${API_BASE}/settings`, {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ settings: { ...appSettings, THEME_MODE: newMode } })
                          }).catch(() => {});
                        }}
                        style={{ background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--border)', padding: '4px', borderRadius: '4px' }}
                      >
                        <option value="dark">Always Dark</option>
                        <option value="light">Always Light</option>
                        <option value="system">System Preference</option>
                      </select>
                    </div>
                  </label>

                  <div className="settings-section-title">Chunking</div>
                  {[
                    { key: "BASE_CHUNK_SIZE", label: "Chunk size", unit: "MB", divisor: 1048576, step: 1 },
                    { key: "MIN_CHUNK_SIZE", label: "Min chunk", unit: "KB", divisor: 1024, step: 64 },
                    { key: "MAX_CHUNK_SIZE", label: "Max chunk", unit: "MB", divisor: 1048576, step: 1 },
                  ].map(({ key, label, unit, divisor, step }) => {
                    const displayVal = Math.round((appSettings[key] ?? 0) / divisor * 100) / 100;
                    return (
                      <label key={key} className="setting-row">
                        <span>{label}</span>
                        <div className="setting-input-wrap">
                          <input type="number" value={displayVal} step={step} min={0} onChange={(e) => {
                            const raw = Number(e.target.value) * divisor;
                            setAppSettings(prev => ({ ...prev, [key]: raw }));
                          }} />
                          <span className="setting-unit">{unit}</span>
                        </div>
                      </label>
                    );
                  })}

                  <div className="settings-section-title">Rebalancing</div>
                  {[
                    { key: "WEIGHT_REBALANCE_INTERVAL_SECONDS", label: "Rebalance interval", unit: "sec", divisor: 1, step: 1 },
                    { key: "MIN_INTERFACE_SPEED_THRESHOLD", label: "Min speed threshold", unit: "KB/s", divisor: 1 / 1024, step: 10 },
                    { key: "SLOW_INTERFACE_GRACE_PERIOD", label: "Slow grace period", unit: "sec", divisor: 1, step: 1 },
                  ].map(({ key, label, unit, divisor, step }) => {
                    const displayVal = Math.round((appSettings[key] ?? 0) / divisor * 100) / 100;
                    return (
                      <label key={key} className="setting-row">
                        <span>{label}</span>
                        <div className="setting-input-wrap">
                          <input type="number" value={displayVal} step={step} min={0} onChange={(e) => {
                            const raw = Number(e.target.value) * divisor;
                            setAppSettings(prev => ({ ...prev, [key]: raw }));
                          }} />
                          <span className="setting-unit">{unit}</span>
                        </div>
                      </label>
                    );
                  })}

                </div>
              )}
            </div>
          )}
        </main>
      </div>

      {toast && (
        <div style={{ position: 'fixed', bottom: '24px', right: '32px', background: 'var(--text)', color: 'var(--bg)', padding: '8px 16px', borderRadius: '4px', fontSize: '13px', zIndex: 9999, animation: 'slideIn 0.2s ease' }}>
          {toast}
        </div>
      )}

      <PromptModal 
        isOpen={!!promptData}
        title={promptData?.title}
        defaultValue={promptData?.value}
        onCancel={() => setPromptData(null)}
        onConfirm={(val) => {
          const num = parseFloat(val);
          const ip = promptData.ip;
          if (num > 0) {
            setBandwidthLimits({ ...bandwidthLimits, [ip]: Math.round(num * 1024 * 1024) });
          } else {
            const newLimits = { ...bandwidthLimits };
            delete newLimits[ip];
            setBandwidthLimits(newLimits);
          }
          setPromptData(null);
        }}
      />
    </div>
  );
}
