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

const API_BASE = "http://127.0.0.1:8000";
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

function formatEta(seconds) {
  if (!seconds || seconds <= 0 || !Number.isFinite(seconds)) return "~--";
  if (seconds < 60) return `~${Math.round(seconds)}s`;
  const mins = Math.floor(seconds / 60);
  const sec = Math.round(seconds % 60);
  return `~${mins}m ${sec}s`;
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
            <button className="action-btn" onClick={onPause} title="Pause"><Pause size={16}/></button>
          )}
          {(status.status === 'paused' || status.status === 'waiting_reconnect') && (
            <button className="action-btn" onClick={onResume} title="Resume"><Play size={16}/></button>
          )}
          <button className="action-btn" onClick={onCancel} title="Cancel"><X size={16}/></button>
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
        <span>{!isPaused && status.status === 'downloading' ? formatEta(eta) : ''}</span>
      </div>
    </div>
  );
}

export default function App() {
  const [activeTab, setActiveTab] = useState("downloads");
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
  const [history, setHistory] = useState([]);

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

  const jobSocketsRef = useRef({});

  useEffect(() => {
    if (isDarkMode) document.body.classList.add("dark");
    else document.body.classList.remove("dark");
    localStorage.setItem("burst_theme_mode", themeMode);
  }, [isDarkMode, themeMode]);

  useEffect(() => {
    const savedLimits = JSON.parse(localStorage.getItem("burst_bandwidth_limits") || "{}");
    setBandwidthLimits(savedLimits);
    const savedPath = localStorage.getItem("burst_default_path") || "C:\\Downloads\\";
    if (!url) setOutputPath(savedPath + (url ? "" : "burst-download.bin"));
    
    fetch(`${API_BASE}/settings`).then(r => r.json()).then(d => setAppSettings(d.settings)).catch(() => {});
    setActiveJobs([]);
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
    const isTorrent = newUrl.trim().startsWith("magnet:?") || newUrl.trim().endsWith(".torrent");
    if (isTorrent) {
      const magnetNameMatch = newUrl.match(/dn=([^&]+)/);
      const magnetName = magnetNameMatch ? decodeURIComponent(magnetNameMatch[1]).replace(/\+/g, ' ') : "torrent-download";
      const savedPath = localStorage.getItem("burst_default_path") || "C:\\Downloads\\";
      setOutputPath(savedPath + magnetName);
      return;
    }
    try {
      const urlObj = new URL(newUrl);
      const pathname = urlObj.pathname;
      if (pathname && pathname !== "/") {
        const segments = pathname.split("/");
        const lastSegment = segments[segments.length - 1];
        if (lastSegment && !lastSegment.includes("?") && lastSegment.includes(".")) {
          setOutputPath(`C:\\Downloads\\${lastSegment}`);
          return;
        }
      }
      setOutputPath("C:\\Downloads\\burst-download.bin");
    } catch {
      setOutputPath("C:\\Downloads\\burst-download.bin");
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
    // If running in Electron, these would be ipcRenderer calls
    if (window.electronAPI) {
      window.electronAPI[action]();
    } else {
      if (action === 'close') {
        if (window.confirm("Quit Burst?")) window.close();
      } else {
        setToast(`${action.charAt(0).toUpperCase() + action.slice(1)} is handled by the OS/Electron.`);
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
    } catch {}
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
    } catch {}
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
      setActiveJobs(prev => [...prev, data.job_id]);
      setUrl("");
      setActiveTab(isTorrent ? "torrents" : "downloads");
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
            <div className="logo-square" style={{ width: '12px', height: '12px', borderRadius: '2px', background: 'var(--accent)' }} />
            Burst 1.0
          </div>
        </div>
        <div className="titlebar-drag-region" />
        <div className="window-controls">
          <button className="window-btn" onClick={() => handleWindowAction('minimize')}><Minus size={16}/></button>
          <button className="window-btn" onClick={() => handleWindowAction('maximize')}><Square size={14}/></button>
          <button className="window-btn close" onClick={() => handleWindowAction('close')}><X size={16}/></button>
        </div>
      </div>

      <div className={`main-layout ${isSidebarCollapsed ? 'collapsed' : ''}`}>
        <aside className="sidebar">
          <div className="nav-list" style={{ marginTop: '12px' }}>
             <button className={`nav-item ${activeTab === 'downloads' ? 'active' : ''}`} onClick={() => setActiveTab('downloads')} title="Downloads">
              <Download size={16} /> {!isSidebarCollapsed && "Downloads"}
            </button>
            <button className={`nav-item ${activeTab === 'torrents' ? 'active' : ''}`} onClick={() => setActiveTab('torrents')} title="Torrents">
              <Magnet size={16} /> {!isSidebarCollapsed && "Torrents"}
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
          {(activeTab === 'downloads' || activeTab === 'torrents') && (
            <>
              <div className="top-controls">
                <div className="input-group">
                  <input 
                    type="text" 
                    className="url-input" 
                    placeholder={activeTab === 'torrents' ? "Paste a magnet link or .torrent URL..." : "Paste a download URL..."}
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
                    } catch {}
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
                <div className="section-label">Active {activeTab === 'torrents' ? 'Torrents' : 'Downloads'}</div>
                {activeJobs.filter(jid => {
                  const st = jobStatuses[jid];
                  if (!st) return false;
                  if (activeTab === 'torrents') return st.type === 'torrent';
                  return st.type !== 'torrent';
                }).length === 0 && (
                  <div className="empty-state slide-in">
                    <div className="empty-icon-wrapper">
                      <Inbox size={32} strokeWidth={1.5} />
                    </div>
                    <h3>No active {activeTab}</h3>
                    <p>Paste a link above to start your first speed-bonded download.</p>
                  </div>
                )}
                {activeJobs.filter(jid => {
                  const st = jobStatuses[jid];
                  if (!st) return false;
                  if (activeTab === 'torrents') return st.type === 'torrent';
                  return st.type !== 'torrent';
                }).map(jid => (
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
                {history.filter(h => activeTab === 'torrents' ? h.type === 'torrent' : h.type !== 'torrent').length > 0 && (
                  <div className="recent-completed-section">
                    <div className="section-label">Recently Completed</div>
                    {history
                      .filter(h => activeTab === 'torrents' ? h.type === 'torrent' : h.type !== 'torrent')
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
                      {item.time_saved > 0 && <div className="completed-saved">Saved {formatEta(item.time_saved)}</div>}
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
                              const val = prompt("Set bandwidth limit in MB/s (0 to remove):", bandwidthLimits[iface.ip_address] ? (bandwidthLimits[iface.ip_address] / 1024 / 1024) : "");
                              if (val !== null) {
                                const num = parseFloat(val);
                                if (num > 0) {
                                  setBandwidthLimits({ ...bandwidthLimits, [iface.ip_address]: Math.round(num * 1024 * 1024) });
                                } else {
                                  const newLimits = { ...bandwidthLimits };
                                  delete newLimits[iface.ip_address];
                                  setBandwidthLimits(newLimits);
                                }
                              }
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
              <div className="section-label">Settings</div>
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
                      value={localStorage.getItem("burst_default_path") || "C:\\Downloads\\"} 
                      onChange={(e) => {
                        localStorage.setItem("burst_default_path", e.target.value);
                        setToast("Default path updated");
                      }} 
                    />
                    <button className="btn-small" onClick={() => handleBrowsePath(p => {
                        localStorage.setItem("burst_default_path", p);
                        setToast("Default path updated");
                    })}>Browse</button>
                  </div>
                </label>
                <label className="setting-row">
                  <span>Theme Mode</span>
                  <div className="setting-input-wrap">
                    <select 
                      value={themeMode} 
                      onChange={(e) => setThemeMode(e.target.value)}
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
                              setAppSettings(prev => ({...prev, [key]: raw}));
                            }} />
                          <span className="setting-unit">{unit}</span>
                        </div>
                      </label>
                    );
                  })}
                  
                  <div className="settings-section-title">Rebalancing</div>
                  {[
                    { key: "WEIGHT_REBALANCE_INTERVAL_SECONDS", label: "Rebalance interval", unit: "sec", divisor: 1, step: 1 },
                    { key: "MIN_INTERFACE_SPEED_THRESHOLD", label: "Min speed threshold", unit: "KB/s", divisor: 1/1024, step: 10 },
                    { key: "SLOW_INTERFACE_GRACE_PERIOD", label: "Slow grace period", unit: "sec", divisor: 1, step: 1 },
                  ].map(({ key, label, unit, divisor, step }) => {
                    const displayVal = Math.round((appSettings[key] ?? 0) / divisor * 100) / 100;
                    return (
                      <label key={key} className="setting-row">
                        <span>{label}</span>
                        <div className="setting-input-wrap">
                          <input type="number" value={displayVal} step={step} min={0} onChange={(e) => {
                              const raw = Number(e.target.value) * divisor;
                              setAppSettings(prev => ({...prev, [key]: raw}));
                            }} />
                          <span className="setting-unit">{unit}</span>
                        </div>
                      </label>
                    );
                  })}
                  
                  <div style={{ marginTop: '24px', display: 'flex', gap: '12px' }}>
                    <button className="btn-primary" onClick={() => {
                      fetch(`${API_BASE}/settings`, {
                        method: "POST",
                        headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({settings: appSettings})
                      }).then(() => setToast("Settings saved.")).catch(() => {});
                    }}>Save Settings</button>
                    <button className="btn-small" onClick={() => {
                      fetch(`${API_BASE}/settings/reset`, { method: "POST" }).then(r => r.json()).then(d => {
                        setAppSettings(d.settings);
                        setToast("Settings reset to defaults.");
                      }).catch(() => {});
                    }}>Reset to Defaults</button>
                  </div>
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
    </div>
  );
}
