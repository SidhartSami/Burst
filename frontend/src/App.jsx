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

const CHROME_SVG = (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M12 22C17.5228 22 22 17.5228 22 12C22 6.47715 17.5228 2 12 2C6.47715 2 2 6.47715 2 12C2 17.5228 6.47715 22 12 22Z" fill="#4285F4"/>
    <path d="M12 17C14.7614 17 17 14.7614 17 12C17 9.23858 14.7614 7 12 7C9.23858 7 7 9.23858 7 12C7 14.7614 9.23858 17 12 17Z" fill="white"/>
    <path d="M12 15C13.6569 15 15 13.6569 15 12C15 10.3431 13.6569 9 12 9C10.3431 9 9 10.3431 9 12C9 13.6569 10.3431 15 12 15Z" fill="#4285F4"/>
  </svg>
);

const FIREFOX_SVG = (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M22 12C22 17.5228 17.5228 22 12 22C6.47715 22 2 17.5228 2 12C2 6.47715 6.47715 2 12 2C17.5228 2 22 6.47715 22 12Z" fill="#FF7139"/>
    <path d="M12 17C14.7614 17 17 14.7614 17 12C17 9.23858 14.7614 7 12 7C9.23858 7 7 9.23858 7 12C7 14.7614 9.23858 17 12 17Z" fill="white"/>
  </svg>
);

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

function PromptModal({ isOpen, title, message, hint, defaultValue, onConfirm, onCancel, type }) {
  const [value, setValue] = useState(defaultValue);
  if (!isOpen) return null;

  if (type === "confirm") {
    return (
      <div className="modal-overlay">
        <div className="modal-content slide-in" style={{ width: '380px' }}>
          <h3 style={{ marginBottom: '12px' }}>{title}</h3>
          <p style={{ fontSize: '13px', color: 'var(--text-muted)', lineHeight: '1.5', marginBottom: '24px' }}>
            {message}
          </p>
          <div className="modal-actions">
            <button className="btn-secondary" onClick={onCancel}>Cancel</button>
            <button className="btn-danger" onClick={() => onConfirm(value)}>Confirm</button>
          </div>
        </div>
      </div>
    );
  }

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
        {hint && <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '6px' }}>{hint}</div>}
        <div className="modal-actions">
          <button className="btn-secondary" onClick={onCancel}>Cancel</button>
          <button className="btn-primary" onClick={() => onConfirm(value)}>OK</button>
        </div>
      </div>
    </div>
  );
}

function InfoTooltip({ text }) {
  return (
    <span style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', marginLeft: '4px' }}
      className="info-tooltip-wrap">
      <span className="info-tooltip-icon">ℹ</span>
      <span className="info-tooltip-box">{text}</span>
    </span>
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
  const [confirmingCancel, setConfirmingCancel] = useState(false);
  const isDone = status.status === 'completed' || status.status === 'failed';

  const activeIfaces = status.type === "torrent"
    ? (status.interface_ips || []).length
    : Object.values(status.interfaces || {}).filter(i => i.status !== "excluded" && i.status !== "cancelled").length;

  const isPaused = status.status === 'paused' || status.status === 'waiting_reconnect' || (status.status === 'downloading' && activeIfaces === 0);
  const statusLabel = isPaused ? 'PAUSED' : status.status;
  const statusClass = status.status === 'completed' ? 'completed' : (status.status === 'failed' ? 'failed' : (isPaused ? 'paused' : 'downloading'));

  const safeDownloaded = Math.max(0, status.total_downloaded ?? 0);
  const pct = Math.min(100, (safeDownloaded / Math.max(1, status.expected_size || 1)) * 100);
  const safePct = Math.max(0, pct);

  const speedRaw = status.type === "torrent"
    ? (status.speed_combined || 0) / (1024 * 1024)
    : currentInterfacesProgress.reduce((sum, item) => sum + Number(item.speed_mb_s || 0), 0);

  const combinedCurrentSpeed = isPaused ? 0 : speedRaw;
  const eta = (!isPaused && combinedCurrentSpeed > 0) ? (status.expected_size - safeDownloaded) / (combinedCurrentSpeed * 1024 * 1024) : 0;

  const handleCancelClick = () => {
    if (isDone) { onCancel(); return; }  // completed/failed — no confirmation needed
    setConfirmingCancel(true);
  };

  return (
    <div className="dl-card slide-in">
      {/* Top row: filename + badge (left) | speed + buttons (right) */}
      <div className="dl-card-top">
        <div className="dl-title-row">
          <div className="dl-filename">{status.output_path?.split(/[\\/]/).pop() || "download.bin"}</div>
          <div className={`status-badge ${statusClass}`}>{statusLabel}</div>
        </div>

        <div className="dl-actions">
          {!confirmingCancel && !isDone && combinedCurrentSpeed > 0 && (
            <div className="dl-speed">{formatSpeed(combinedCurrentSpeed)}</div>
          )}

          {confirmingCancel ? (
            <>
              <span style={{ fontSize: '12px', color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>Cancel?</span>
              <button
                className="action-btn"
                style={{ background: 'var(--danger)', color: '#fff', padding: '2px 10px', borderRadius: '4px', fontSize: '12px', border: 'none', cursor: 'pointer', whiteSpace: 'nowrap' }}
                onClick={onCancel}
              >Yes</button>
              <button
                className="action-btn"
                style={{ background: 'transparent', color: 'var(--text-muted)', padding: '2px 8px', borderRadius: '4px', fontSize: '12px', border: '1px solid var(--border)', cursor: 'pointer' }}
                onClick={() => setConfirmingCancel(false)}
              >Keep</button>
            </>
          ) : (
            <>
              {status.status === 'downloading' && status.type !== 'torrent' && (
                <button
                  className={`action-btn boost-btn ${status.boosted ? 'active-boost' : ''}`}
                  onClick={() => fetch(`${API_BASE}/download/${jid}/boost`, { method: 'POST' })}
                  title="Boost bonded speed"
                  style={{ color: status.boosted ? 'var(--accent)' : 'var(--text-muted)' }}
                >
                  <Zap size={15} fill={status.boosted ? 'var(--accent)' : 'none'} />
                </button>
              )}
              <button className="action-btn" onClick={handleCancelClick} title="Dismiss"><X size={15} /></button>
            </>
          )}
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
              live = { status: 'downloading', speed_mb_s: speedBytes / (1024 * 1024) };
            }
          } else {
            live = status.interfaces?.[iface.ip_address];
            isSelected = !!live && live.status !== "excluded" && live.status !== "cancelled";
          }

          if (isOptimistic && isOptimistic.ip === iface.ip_address) isSelected = isOptimistic.selected;

          const speed = isPaused ? 0 : (live?.speed_mb_s || 0);
          const tone = toneFor(shortName(iface.name, iface.interface_type));
          const isShared = allUsedIps.filter(ip => ip === iface.ip_address).length > 1;

          return (
            <div
              key={iface.ip_address}
              className={`iface-pill ${isSelected ? 'active' : ''}`}
              style={{
                opacity: isPaused ? 0.4 : 1,
                filter: isPaused ? 'grayscale(60%)' : 'none',
              }}
              onClick={() => {
                if (isDone) return;
                setIsOptimistic({ ip: iface.ip_address, selected: !isSelected });
                onToggle(iface.ip_address, isSelected).finally(() => { setTimeout(() => setIsOptimistic(null), 1000); });
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
        {isPaused ? (
          <span>Paused • {safePct.toFixed(1)}%</span>
        ) : (
          <span>{pct.toFixed(1)}% • {formatBytes(safeDownloaded)} / {formatBytes(status.expected_size)}</span>
        )}
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
  const [outputPath, setOutputPath] = useState("C:/Burst-Downloads/burst-download.bin");
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
  const [showOnboarding, setShowOnboarding] = useState(false);
  const [onboardingStep, setOnboardingStep] = useState(1);
  const [bannerDismissed, setBannerDismissed] = useState(() => localStorage.getItem("burst_banner_dismissed") === "true");

  const jobSocketsRef = useRef({});
  const dragCounter = useRef(0);

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
    const savedPath = localStorage.getItem("burst_default_path") || "C:/Burst-Downloads/";
    if (!url) setOutputPath(savedPath + (url ? "" : "burst-download.bin"));

    // 2. Fetch all configuration and active jobs concurrently to avoid waterfalls
    Promise.all([
      fetch(`${API_BASE}/settings`).then(r => r.json()).catch(() => null),
      fetch(`${API_BASE}/active-jobs`).then(r => r.json()).catch(() => null),
      fetch(`${API_BASE}/history`).then(r => r.json()).catch(() => null)
    ])
      .then(([settingsData, activeJobsData, historyData]) => {
        // Handle settings
        if (settingsData && settingsData.settings) {
          setAppSettings(settingsData.settings);
          if (settingsData.settings.THEME_MODE) {
            setThemeMode(settingsData.settings.THEME_MODE);
          }
          if (settingsData.settings.DOWNLOAD_PATH && !url) {
            setOutputPath(settingsData.settings.DOWNLOAD_PATH);
          }
          // Check onboarding completion
          if (settingsData.settings.ONBOARDING_COMPLETE === false) {
            setShowOnboarding(true);
          }
        }

        // Handle active jobs
        if (activeJobsData && activeJobsData.job_ids && activeJobsData.job_ids.length > 0) {
          setActiveJobs(prev => {
            const merged = [...prev];
            activeJobsData.job_ids.forEach(id => { if (!merged.includes(id)) merged.push(id); });
            return merged;
          });
        }

        // Handle history
        if (historyData && historyData.history && historyData.history.length > 0) {
          setHistory(prev => {
            if (prev.length > 0) return prev; // already have local history — don't stomp
            return historyData.history.map(item => {
              const duration = Math.max(0, (item.finished_at || 0) - (item.started_at || 0));
              const computedAvgSpeed = duration > 0
                ? (item.total_downloaded || item.size || 0) / duration
                : (item.avgSpeed || item.speed_combined || 0); // fallback for old history entries
              return {
                id: item.job_id || item.id || crypto.randomUUID(),
                filename: item.filename || item.output_path?.split(/[\\/]/).pop() || "download.bin",
                path: item.output_path || item.path || "Unknown path",
                size: item.total_downloaded || item.size || 0,
                avgSpeed: computedAvgSpeed,
                time_saved: item.time_saved || 0,
                status: item.status || "completed",
                timestamp: item.finished_at
                  ? new Date(item.finished_at * 1000).toLocaleString()
                  : (item.timestamp || new Date().toLocaleString()),
                type: item.type || "download"
              };
            }).slice(0, 50);
          });
        }
      });

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
      return "C:/Burst-Downloads/";
    };

    const baseDir = getBaseDir();

    if (isTorrent) {
      const magnetNameMatch = newUrl.match(/dn=([^&]+)/);
      const magnetName = magnetNameMatch ? decodeURIComponent(magnetNameMatch[1]).replace(/\+/g, ' ') : "torrent-download";
      setOutputPath(baseDir + magnetName);
      return;
    }
    const filename = newUrl.split("/").pop()?.split("?")[0] || "burst-download.bin";
    const bd = appSettings?.DOWNLOAD_PATH || localStorage.getItem("burst_default_path") || "C:/Burst-Downloads";
    const safeDir = bd.endsWith("/") || bd.endsWith("\\") ? bd : bd + "/";
    setOutputPath(safeDir + filename);
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
    // Deduplicate incoming by ip_address first — keeps last entry per IP
    const deduped = Object.values(
      incoming.reduce((acc, iface) => { acc[iface.ip_address] = iface; return acc; }, {})
    );
    setRenderedInterfaces((prev) => {
      const nextMap = new Map(deduped.map((item) => [item.ip_address, item]));
      const prevMap = new Map(prev.map((item) => [item.ip_address, item]));
      const merged = [];
      const seen = new Set();

      for (const oldItem of prev) {
        if (seen.has(oldItem.ip_address)) continue; // skip duplicates from prev
        seen.add(oldItem.ip_address);
        const fresh = nextMap.get(oldItem.ip_address);
        if (!fresh) {
          if (!oldItem.exiting) merged.push({ ...oldItem, exiting: true, entering: false });
          else merged.push(oldItem);
          continue;
        }
        merged.push({ ...oldItem, ...fresh, entering: false, exiting: false });
      }

      for (const fresh of deduped) {
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
            // speed_combined is the live speed, zeroed at completion — compute true avg from bytes/time
            const computedAvgSpeed = duration > 0 ? (payload.total_downloaded || 0) / duration : 0;
            const historyItem = {
              id: payload.job_id || crypto.randomUUID(),
              filename: payload.output_path?.split(/[\\/]/).pop() || "download.bin",
              path: payload.output_path || "Unknown path",
              size: payload.total_downloaded,
              avgSpeed: computedAvgSpeed,
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

  const clearHistory = () => {
    setPromptData({
      title: "Clear History?",
      message: "Are you sure you want to permanently clear your download history? This action cannot be undone.",
      type: "confirm",
      onConfirm: () => {
        fetch(`${API_BASE}/history/clear`, { method: "POST" });
        setHistory([]);
        setPromptData(null);
      }
    });
  };

  const activeConnectionCount = interfaces.length;

  return (
    <div
      className={`app-container ${isDarkMode ? 'dark' : ''}`}
      onDragEnter={(e) => {
        e.preventDefault();
        dragCounter.current += 1;
        if (dragCounter.current === 1) {
          setDragOverlay(true);
        }
      }}
      onDragOver={(e) => {
        e.preventDefault();
      }}
      onDragLeave={(e) => {
        e.preventDefault();
        dragCounter.current -= 1;
        if (dragCounter.current === 0) {
          setDragOverlay(false);
        }
      }}
    >
      {dragOverlay && (
        <div
          className="drag-overlay"
          onDragEnter={(e) => {
            e.preventDefault();
            dragCounter.current += 1;
          }}
          onDragLeave={(e) => {
            e.preventDefault();
            dragCounter.current -= 1;
            if (dragCounter.current === 0) {
              setDragOverlay(false);
            }
          }}
          onDrop={(e) => {
            e.preventDefault();
            dragCounter.current = 0;
            setDragOverlay(false);
            const droppedUrl = readDroppedUrl(e);
            if (droppedUrl) {
              handleUrlChange(droppedUrl);
              setTimeout(() => startDownload(droppedUrl, outputPath), 0);
            }
          }}
        >
          <div className="drag-overlay-content">Drop URL to download</div>
        </div>
      )}

      {showOnboarding && (
        <div className="onboarding-fullscreen">
          <div className="onboarding-content">
            <img src="/logo.png" alt="Burst" className="onboarding-logo" />
            {onboardingStep === 1 ? (
              <>
                <h2 className="onboarding-title">Welcome to Burst</h2>
                <p className="onboarding-text">
                  The download manager that uses all your connections at once.
                </p>
                <button
                  className="onboarding-start-btn"
                  onClick={() => setOnboardingStep(2)}
                >
                  Get Started →
                </button>
              </>
            ) : (
              <>
                <h2 className="onboarding-title">Download faster from your browser</h2>
                <p className="onboarding-text">
                  Install our extension to right-click any link and send it directly to Burst.
                </p>
                <div className="extension-grid">
                  <button
                    className="extension-btn chrome"
                    onClick={() => window.open('https://chrome.google.com/webstore/detail/burst/pblmhjepeacmfphcnaaekefjnipfkcfd', '_blank')}
                  >
                    {CHROME_SVG} Add to Chrome
                  </button>
                  <button
                    className="extension-btn firefox"
                    onClick={() => window.open('https://addons.mozilla.org/firefox/addon/burst-download-manager', '_blank')}
                  >
                    {FIREFOX_SVG} Add to Firefox
                  </button>
                </div>
                <button
                  className="onboarding-skip"
                  onClick={async () => {
                    await fetch(`${API_BASE}/settings`, {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ settings: { ...appSettings, ONBOARDING_COMPLETE: true } })
                    });
                    setShowOnboarding(false);
                  }}
                >
                  Skip and start using Burst
                </button>
              </>
            )}
          </div>
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
                {interfaces.length <= 1 && appSettings?.ONBOARDING_COMPLETE && !bannerDismissed && (
                  <div className="interface-hint-bar" style={{ marginBottom: '16px', borderRadius: '8px', border: '1px solid var(--border)' }}>
                    <Zap size={13} className="hint-icon" />
                    <span>Connect a second interface (Ethernet, hotspot) to start bonding speeds</span>
                    <a className="hint-link" onClick={() => setActiveTab('connections')}>View Connections →</a>
                    <button className="close-btn" onClick={() => {
                      setBannerDismissed(true);
                      localStorage.setItem("burst_banner_dismissed", "true");
                    }}>
                      <X size={14} />
                    </button>
                  </div>
                )}
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
                            <span style={{
                              display: 'inline-block',
                              background: 'var(--surface-raised, var(--surface))',
                              border: '1px solid var(--border)',
                              borderRadius: '4px',
                              padding: '1px 6px',
                              fontSize: '11px',
                              fontVariantNumeric: 'tabular-nums',
                              color: 'var(--text-muted)'
                            }}>{formatBytes(item.size)}</span>
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
                      <td style={{ fontSize: '12px', color: 'var(--text-muted)', fontFamily: 'monospace', paddingRight: '16px' }}>
                        {iface.ip_address}
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
                              hint: "Enter 0 to remove the limit (unlimited).",
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

                  {/* DEFAULT DOWNLOAD PATH */}
                  <label className="setting-row">
                    <span>Default Download Path</span>
                    <div className="setting-input-wrap">
                      <input
                        type="text"
                        style={{ width: '240px' }}
                        value={appSettings?.DOWNLOAD_PATH || "C:/Burst-Downloads"}
                        onChange={(e) => {
                          setAppSettings({ ...appSettings, DOWNLOAD_PATH: e.target.value });
                        }}
                      />
                      <button className="btn-small" onClick={() => handleBrowsePath(p => {
                        setAppSettings({ ...appSettings, DOWNLOAD_PATH: p });
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

                  {/* START ON BOOT TOGGLE — 3rd item */}
                  <label className="setting-row" style={{ alignItems: 'flex-start' }}>
                    <div>
                      <span>Start Burst on Boot</span>
                      <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '2px' }}>Burst runs silently in the background on startup</div>
                    </div>
                    <div className="setting-input-wrap">
                      <div
                        onClick={() => setAppSettings(prev => ({ ...prev, START_ON_BOOT: !prev.START_ON_BOOT }))}
                        style={{
                          width: '40px', height: '22px', borderRadius: '11px', cursor: 'pointer',
                          background: appSettings?.START_ON_BOOT ? 'var(--accent)' : 'var(--border)',
                          position: 'relative', transition: 'background 0.2s', flexShrink: 0
                        }}
                      >
                        <div style={{
                          position: 'absolute', top: '3px',
                          left: appSettings?.START_ON_BOOT ? '21px' : '3px',
                          width: '16px', height: '16px', borderRadius: '50%',
                          background: '#fff', transition: 'left 0.2s',
                          boxShadow: '0 1px 3px rgba(0,0,0,0.3)'
                        }} />
                      </div>
                    </div>
                  </label>

                  <div className="settings-section-title">Chunking</div>
                  {[
                    { key: "BASE_CHUNK_SIZE", label: "Chunk size", unit: "MB", divisor: 1048576, step: 1, info: "Default size of each download chunk per interface. Larger = fewer requests, better for fast stable links. Smaller = more adaptive on unstable connections." },
                    { key: "MIN_CHUNK_SIZE", label: "Min chunk", unit: "KB", divisor: 1024, step: 64, info: "Floor chunk size. High-latency interfaces get smaller chunks so they don't hold up the download if they fall behind." },
                    { key: "MAX_CHUNK_SIZE", label: "Max chunk", unit: "MB", divisor: 1048576, step: 1, info: "Ceiling chunk size. Prevents any single chunk from being so large that a slow interface causes a bottleneck." },
                  ].map(({ key, label, unit, divisor, step, info }) => {
                    const displayVal = Math.round((appSettings[key] ?? 0) / divisor * 100) / 100;
                    return (
                      <label key={key} className="setting-row">
                        <span style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                          {label}
                          <InfoTooltip text={info} />
                        </span>
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
                    { key: "WEIGHT_REBALANCE_INTERVAL_SECONDS", label: "Rebalance interval", unit: "sec", divisor: 1, step: 1, info: "How often Burst recalculates bandwidth weights across interfaces based on live speed measurements." },
                    { key: "MIN_INTERFACE_SPEED_THRESHOLD", label: "Min speed threshold", unit: "KB/s", divisor: 1 / 1024, step: 10, info: "If an interface falls below this speed for longer than the grace period, it gets paused and its chunks redistributed." },
                    { key: "SLOW_INTERFACE_GRACE_PERIOD", label: "Slow grace period", unit: "sec", divisor: 1, step: 1, info: "How long an interface is allowed to stay below the min speed threshold before being paused. Prevents false positives on brief congestion." },
                  ].map(({ key, label, unit, divisor, step, info }) => {
                    const displayVal = Math.round((appSettings[key] ?? 0) / divisor * 100) / 100;
                    return (
                      <label key={key} className="setting-row">
                        <span style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                          {label}
                          <InfoTooltip text={info} />
                        </span>
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

                  <div style={{ color: 'var(--text-muted)', fontSize: '11px', marginTop: '24px', textAlign: 'center' }}>
                    Burst v1.2.1
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

      <PromptModal
        isOpen={!!promptData}
        title={promptData?.title}
        message={promptData?.message}
        hint={promptData?.hint}
        defaultValue={promptData?.value}
        type={promptData?.type}
        onCancel={() => setPromptData(null)}
        onConfirm={promptData?.onConfirm || ((val) => {
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
        })}
      />
    </div>
  );
}
