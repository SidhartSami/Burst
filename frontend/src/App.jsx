import { useEffect, useMemo, useRef, useState } from "react";
import { closestCenter, DndContext, PointerSensor, useSensor, useSensors } from "@dnd-kit/core";
import { arrayMove, SortableContext, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  AlertTriangle,
  Cable,
  ChevronDown,
  CheckCircle2,
  CircleX,
  Folder,
  GripVertical,
  Pause,
  Settings,
  Settings2,
  Smartphone,
  Wifi,
  X
} from "lucide-react";
import { createPortal } from "react-dom";

const API_BASE = "http://127.0.0.1:8000";
const HISTORY_KEY = "burst_history";
const SEEN_KEY = "burst_seen";

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

function readDroppedUrl(event) {
  const uriList = event.dataTransfer.getData("text/uri-list");
  if (uriList) return uriList.split("\n").find((line) => line.startsWith("http")) || "";
  const plainText = event.dataTransfer.getData("text/plain");
  if (plainText && /^https?:\/\//i.test(plainText.trim())) return plainText.trim();
  return "";
}

function analyzeTimeSaved(size, baseSpeedMbS, actualSeconds) {
  if (!size || !baseSpeedMbS || !actualSeconds) {
    return { estimatedSingleTime: 0, timeSaved: 0 };
  }
  const baseBytesPerSec = baseSpeedMbS * 1024 * 1024;
  if (!baseBytesPerSec) return { estimatedSingleTime: 0, timeSaved: 0 };
  const estimatedSingleTime = size / baseBytesPerSec;
  const timeSaved = Math.max(0, estimatedSingleTime - actualSeconds);
  return { estimatedSingleTime, timeSaved };
}

function SortableQueueItem({ item }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: item.id
  });

  return (
    <article
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition, userSelect: "none" }}
      className={`queue-item-v31 ${isDragging ? "dragging" : ""}`}
    >
      <button className="queue-handle" {...attributes} {...listeners}>
        <GripVertical size={13} />
      </button>
      <div className="queue-copy">
        <p>{item.filename}</p>
        <span>{formatBytes(item.size)}</span>
      </div>
      <span className="queue-badge">queued</span>
    </article>
  );
}

export default function App() {
  const [showSplash, setShowSplash] = useState(false);
  const [splashLeaving, setSplashLeaving] = useState(false);
  const [interfaces, setInterfaces] = useState([]);
  const [renderedInterfaces, setRenderedInterfaces] = useState([]);
  const [selectedIps, setSelectedIps] = useState([]);
  const [hasSpeedtestRun, setHasSpeedtestRun] = useState(false);
  const [url, setUrl] = useState("");
  const [outputPath, setOutputPath] = useState("C:\\Downloads\\burst-download.bin");
  const [downloading, setDownloading] = useState(false);
  const [downloadStatus, setDownloadStatus] = useState(null);
  const [jobId, setJobId] = useState("");
  const [history, setHistory] = useState([]);
  const [queue, setQueue] = useState([]);
  const [showExtension, setShowExtension] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [appSettings, setAppSettings] = useState(null);
  const [dragOverlay, setDragOverlay] = useState(false);
  const [dropTargetActive, setDropTargetActive] = useState(false);
  const [editingOutputPath, setEditingOutputPath] = useState(false);
  const [toast, setToast] = useState("");
  const [speedRefreshActive, setSpeedRefreshActive] = useState(false);
  const [newIfacePrompt, setNewIfacePrompt] = useState(null);
  const [downloadBtnState, setDownloadBtnState] = useState("idle");
  const [downloadErrorMsg, setDownloadErrorMsg] = useState("");
  const [downloadWarningMsg, setDownloadWarningMsg] = useState("");
  const [pathHintCount, setPathHintCount] = useState(0);
  const [pillHintSeen, setPillHintSeen] = useState(true);
  const [showSettingsModal, setShowSettingsModal] = useState(false);
  const [hotplugBanners, setHotplugBanners] = useState([]);
  const [contextMenu, setContextMenu] = useState(null);
  const [downloadOnlyIps, setDownloadOnlyIps] = useState([]);
  const [ignoredInterfaces, setIgnoredInterfaces] = useState([]);

  useEffect(() => {
    setIgnoredInterfaces(JSON.parse(localStorage.getItem("burst_ignored_interfaces") || "[]"));
    setDownloadOnlyIps(JSON.parse(localStorage.getItem("burst_download_only_ips") || "[]"));
  }, []);

  const handleUrlChange = (newUrl) => {
    setUrl(newUrl);
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
  const prevIfaceIpsRef = useRef([]);
  const appRef = useRef(null);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));

  const mergeInterfacesForUi = (incoming) => {
    setRenderedInterfaces((prev) => {
      const nextMap = new Map(incoming.map((item) => [item.ip_address, item]));
      const prevMap = new Map(prev.map((item) => [item.ip_address, item]));
      const merged = [];

      for (const oldItem of prev) {
        const fresh = nextMap.get(oldItem.ip_address);
        if (!fresh) {
          if (!oldItem.exiting) {
            merged.push({ ...oldItem, exiting: true, entering: false });
          } else {
            merged.push(oldItem);
          }
          continue;
        }
        const oldSpeed = Number(oldItem.speed_mb_s || 0);
        const newSpeed = Number(fresh.speed_mb_s || 0);
        merged.push({
          ...oldItem,
          ...fresh,
          entering: false,
          exiting: false,
          speedFlash: hasSpeedtestRun && oldSpeed <= 0 && newSpeed > 0
        });
      }

      for (const fresh of incoming) {
        if (!prevMap.has(fresh.ip_address)) {
          merged.push({ ...fresh, entering: true, exiting: false, speedFlash: false });
        }
      }
      return merged;
    });
  };

  const fetchInterfaces = async (silent = true) => {
    const resp = await fetch(`${API_BASE}/interfaces`);
    if (!resp.ok) throw new Error("Could not load interfaces");
    const data = await resp.json();
    const fresh = data.interfaces || [];
    setInterfaces(fresh);
    mergeInterfacesForUi(fresh);

    // Detect new or returning interface during active download
    if (downloading && jobId && !newIfacePrompt) {
      const freshIps = fresh.map((i) => i.ip_address);

      // Case 1: Brand new interface that wasn't there before
      const brandNew = freshIps.filter((ip) => !prevIfaceIpsRef.current.includes(ip));

      // Case 2: Interface exists in download but is dead/paused — has returned at OS level
      const returning = freshIps.filter((ip) => {
        const dlStatus = downloadStatus?.interfaces?.[ip];
        return dlStatus && ["paused_slow", "disconnected", "excluded"].includes(dlStatus.status);
      });

      const candidates = [...brandNew, ...returning];
      if (candidates.length > 0) {
        const targetIp = candidates[0];
        const targetIface = fresh.find((i) => i.ip_address === targetIp);
        if (targetIface) {
          setNewIfacePrompt({
            ip: targetIface.ip_address,
            name: shortName(targetIface.name, targetIface.interface_type),
            isReturning: returning.includes(targetIp),
          });
        }
      }
      prevIfaceIpsRef.current = freshIps;
    } else {
      prevIfaceIpsRef.current = fresh.map((i) => i.ip_address);
    }

    setSelectedIps((prev) =>
      prev.length
        ? prev.filter((ip) => fresh.some((item) => item.ip_address === ip))
        : fresh.map((item) => item.ip_address).filter((ip) => {
            const ignored = JSON.parse(localStorage.getItem("burst_ignored_interfaces") || "[]");
            return !ignored.includes(ip);
          })
    );
    if (!silent) setToast("");
  };

  const runSpeedtestSilent = async () => {
    setSpeedRefreshActive(true);
    try {
      const resp = await fetch(`${API_BASE}/speedtest`, { method: "POST" });
      const data = await resp.json();
      if (!resp.ok) return;
      setHasSpeedtestRun(true);
      setInterfaces((prev) =>
        prev.map((iface) => {
          const found = (data.results || []).find((r) => r.ip_address === iface.ip_address);
          return found ? { ...iface, speed_mb_s: found.speed_mb_s } : iface;
        })
      );
      setRenderedInterfaces((prev) =>
        prev.map((iface) => {
          const found = (data.results || []).find((r) => r.ip_address === iface.ip_address);
          if (!found) return iface;
          const oldSpeed = Number(iface.speed_mb_s || 0);
          const newSpeed = Number(found.speed_mb_s || 0);
          return {
            ...iface,
            speed_mb_s: found.speed_mb_s,
            speedFlash: oldSpeed <= 0 && newSpeed > 0
          };
        })
      );
      setTimeout(() => {
        setRenderedInterfaces((prev) => prev.map((item) => ({ ...item, speedFlash: false })));
      }, 300);
    } catch {
      // silent refresh; ignore failures
    } finally {
      setSpeedRefreshActive(false);
    }
  };

  useEffect(() => {
    setShowSplash(!localStorage.getItem(SEEN_KEY));
    fetchInterfaces().catch((err) => setToast(err.message));
    fetch(`${API_BASE}/settings`).then(r => r.json()).then(d => setAppSettings(d.settings)).catch(() => {});
    
    const count = parseInt(localStorage.getItem("burst_path_hint_count") || "0", 10);
    setPathHintCount(count);
    if (count < 3) localStorage.setItem("burst_path_hint_count", (count + 1).toString());
    
    setPillHintSeen(!!localStorage.getItem("burst_pill_hint"));
  }, []);



  useEffect(() => {
    const fadeTicker = setInterval(() => {
      setRenderedInterfaces((prev) => prev.filter((item) => !item.exiting));
    }, 220);
    return () => clearInterval(fadeTicker);
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => {
      runSpeedtestSilent();
    }, 2000);
    const refreshTimer = setInterval(() => {
      fetchInterfaces().catch(() => {});
    }, 15000);
    const speedTimer = setInterval(() => {
      runSpeedtestSilent();
    }, 20000);
    return () => {
      clearTimeout(timer);
      clearInterval(refreshTimer);
      clearInterval(speedTimer);
    };
  }, []);

  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
      if (Array.isArray(saved)) setHistory(saved.slice(0, 8));
    } catch {
      setHistory([]);
    }
  }, []);

  useEffect(() => {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(0, 8)));
  }, [history]);

  useEffect(() => {
    const onDragOver = (event) => {
      event.preventDefault();
      setDragOverlay(true);
    };
    const onDrop = (event) => {
      event.preventDefault();
      const droppedUrl = readDroppedUrl(event);
      setDragOverlay(false);
      if (droppedUrl) {
        setUrl(droppedUrl);
        setTimeout(() => {
          runAnalyze(droppedUrl);
        }, 0);
      }
    };
    const onDragLeave = (event) => {
      if (event.clientX === 0 && event.clientY === 0) {
        setDragOverlay(false);
        setDropTargetActive(false);
      }
    };
    window.addEventListener("dragover", onDragOver);
    window.addEventListener("drop", onDrop);
    window.addEventListener("dragleave", onDragLeave);
    return () => {
      window.removeEventListener("dragover", onDragOver);
      window.removeEventListener("drop", onDrop);
      window.removeEventListener("dragleave", onDragLeave);
    };
  }, []);

  useEffect(() => {
    if (!jobId) return undefined;
    const ws = new WebSocket(`${API_BASE.replace("http", "ws")}/ws/${jobId}`);
    ws.onmessage = (event) => {
      const payload = JSON.parse(event.data);
      setDownloadStatus(payload);

      if (payload.status === "downloading") {
        setRenderedInterfaces((prev) =>
          prev.map((iface) => {
            const liveData = payload.interfaces?.[iface.ip_address];
            if (liveData && liveData.status === "downloading") {
              return { ...iface, speed_mb_s: liveData.speed_mb_s };
            }
            return iface;
          })
        );
      } else if (payload.status === "completed") {
        setDownloading(false);
        const duration = Math.max(0, (payload.finished_at || 0) - (payload.started_at || 0));
        const ifaceUsed = Object.values(payload.interfaces || {}).map((i) => i.ip_address);
        const selectedSpeeds = interfaces
          .filter((i) => ifaceUsed.includes(i.ip_address))
          .map((i) => Number(i.speed_mb_s || 0))
          .filter((v) => v > 0);
        const slowestSelected = selectedSpeeds.length > 0 ? Math.min(...selectedSpeeds) : 0;
        const avgCombinedSpeed = duration
          ? payload.expected_size / (duration * 1024 * 1024)
          : 0;
        const { estimatedSingleTime, timeSaved } = analyzeTimeSaved(
          payload.expected_size,
          slowestSelected,
          duration
        );
        const actualTimeSaved = ifaceUsed.length > 1 ? timeSaved : 0;
        const historyItem = {
          id: payload.job_id,
          filename: payload.output_path?.split(/[\\/]/).pop() || "download.bin",
          output_path: payload.output_path,
          size: payload.expected_size,
          timestamp: Date.now(),
          avgSpeed: avgCombinedSpeed,
          interfaces_used: ifaceUsed,
          estimated_single_time: estimatedSingleTime,
          total_time: duration,
          time_saved: actualTimeSaved,
          status: "completed"
        };
        setHistory((prev) => [historyItem, ...prev].slice(0, 10));
        setToast(
          `Completed ${historyItem.filename} · ${formatSpeed(avgCombinedSpeed)}` +
            (actualTimeSaved > 0 ? ` · saved ~${Math.round(actualTimeSaved)}s` : "")
        );
        setJobId("");
        setDownloadStatus(null);
      } else if (payload.status === "failed") {
        setDownloading(false);
        const historyItem = {
          id: payload.job_id,
          filename: payload.output_path?.split(/[\\/]/).pop() || "download.bin",
          output_path: payload.output_path,
          size: payload.expected_size,
          timestamp: Date.now(),
          avgSpeed: 0,
          interfaces_used: Object.values(payload.interfaces || {}).map((i) => i.ip_address),
          estimated_single_time: 0,
          total_time: 0,
          time_saved: 0,
          status: "failed",
          error_reason: String(payload.error || "Unknown error")
        };
        setHistory((prev) => [historyItem, ...prev].slice(0, 10));
        setToast(`Download failed: ${payload.error || "Unknown error"}`);
        setJobId("");
        setDownloadStatus(null);
      }
    };
    ws.onerror = () => setToast("WebSocket disconnected");
    return () => ws.close();
  }, [jobId, interfaces]);

  const handleToggle = async (ip) => {
    const isSelected = selectedIps.includes(ip);
    setSelectedIps((prev) => (isSelected ? prev.filter((x) => x !== ip) : [...prev, ip]));

    if (downloading && jobId && !isSelected) {
      try {
        await fetch(`${API_BASE}/download/${jobId}/interfaces`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ interface_ips: [ip] })
        });
      } catch (err) {
        setToast("Failed to add interface: " + err.message);
      }
    }

    // Also handle tap-to-rejoin on disconnected/paused interfaces
    if (downloading && jobId && isSelected) {
      const liveStatus = downloadStatus?.interfaces?.[ip];
      if (liveStatus && ["paused_slow", "disconnected", "excluded"].includes(liveStatus.status)) {
        try {
          await fetch(`${API_BASE}/download/${jobId}/interfaces`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ interface_ips: [ip] })
          });
          setToast("Interface rejoining download...");
        } catch (err) {
          setToast("Failed to rejoin: " + err.message);
        }
        return; // Don't deselect
      }
    }
  };

  const handleNewIfaceAccept = async () => {
    if (!newIfacePrompt || !jobId) return;
    setSelectedIps((prev) => [...prev, newIfacePrompt.ip]);
    try {
      await fetch(`${API_BASE}/download/${jobId}/interfaces`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ interface_ips: [newIfacePrompt.ip] })
      });
    } catch (err) {
      setToast("Failed to add interface: " + err.message);
    }
    setNewIfacePrompt(null);
  };

  const handleNewIfaceDismiss = () => {
    setNewIfacePrompt(null);
  };

  const availableInterfaceIps = useMemo(
    () => interfaces.map((item) => item.ip_address).filter(Boolean),
    [interfaces]
  );
  const validSelectedIps = useMemo(
    () => selectedIps.filter((ip) => availableInterfaceIps.includes(ip)),
    [selectedIps, availableInterfaceIps]
  );
  const activeConnectionCount = availableInterfaceIps.length;

  const validSelectedIpsRef = useRef([]);
  const downloadingRef = useRef(false);
  const jobIdRef = useRef("");
  
  useEffect(() => {
    validSelectedIpsRef.current = validSelectedIps;
    downloadingRef.current = downloading;
    jobIdRef.current = jobId;
  }, [validSelectedIps, downloading, jobId]);

  useEffect(() => {
    let ws;
    const connectWs = () => {
      ws = new WebSocket(`${API_BASE.replace("http", "ws")}/ws/interfaces`);
      ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload.event === "interface_added") {
            const ignored = JSON.parse(localStorage.getItem("burst_ignored_interfaces") || "[]");
            if (!ignored.includes(payload.interface.ip)) {
              setHotplugBanners(prev => [...prev, { id: Date.now() + Math.random(), interface: payload.interface }]);
            }
          } else if (payload.event === "interface_removed") {
            const removedIp = payload.interface.ip;
            setRenderedInterfaces(prev => {
              const copy = [...prev];
              const idx = copy.findIndex(i => i.ip_address === removedIp);
              if (idx >= 0) copy[idx] = { ...copy[idx], exiting: true };
              return copy;
            });
            if (downloadingRef.current && validSelectedIpsRef.current.includes(removedIp)) {
              setToast(`${payload.interface.name} disconnected — download continuing on remaining interfaces`);
            }
          }
        } catch {}
      };
      ws.onclose = () => {
        setTimeout(connectWs, 3000);
      };
    };
    connectWs();
    return () => { if (ws) ws.close(); };
  }, []);

  const startDownload = async (incomingUrl = url, incomingOutputPath = outputPath) => {
    const cleanUrl = (incomingUrl ?? url).trim();
    const cleanOutputPath = (incomingOutputPath ?? outputPath).trim();
    const effectiveIps = validSelectedIps.length ? validSelectedIps : availableInterfaceIps;

    const missing = [];
    if (!cleanUrl) missing.push("URL");
    if (!cleanOutputPath) missing.push("output path");
    if (effectiveIps.length === 0) missing.push("interfaces");
    if (missing.length) {
      return setToast(`Missing required fields: ${missing.join(", ")}`);
    }

    setDownloading(true);
    try {
      const resp = await fetch(`${API_BASE}/download`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: cleanUrl,
          output_path: cleanOutputPath,
          interface_ips: effectiveIps
        })
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || "Download failed to start");
      setJobId(data.job_id);
    } catch (err) {
      setDownloading(false);
      setToast(err.message);
    }
  };

  const handleDownloadClick = async () => {
    const targetUrl = url.trim();
    const targetOutputPath = outputPath.trim();
    if (!targetUrl || !targetOutputPath) return setToast("URL and output path are required.");
    
    setDownloadBtnState("checking");
    setDownloadErrorMsg("");
    setDownloadWarningMsg("");
    
    try {
      const resp = await fetch(`${API_BASE}/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: targetUrl, interface_ip: validSelectedIps[0] || null })
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || "URL is invalid or unreachable");
      
      if (!data.supports_ranges) {
        setDownloadWarningMsg("⚠ Server doesn't support split downloads — downloading via fastest connection only");
      }
      
      startDownload(targetUrl, targetOutputPath);
      setDownloadBtnState("idle");
    } catch (err) {
      setDownloadBtnState("error");
      setDownloadErrorMsg(err.message);
      setTimeout(() => {
        setDownloadBtnState("idle");
        setDownloadErrorMsg("");
      }, 4000);
    }
  };

  useEffect(() => {
    if (!downloading && !jobId && queue.length > 0) {
      if (validSelectedIps.length > 0 || availableInterfaceIps.length > 0) {
        const nextItem = queue[0];
        setQueue((prev) => prev.slice(1));
        startDownload(nextItem.url, nextItem.outputPath);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [downloading, jobId, queue.length, validSelectedIps.length, availableInterfaceIps.length]);

  const cancelDownload = async () => {
    if (!jobId) return;
    try {
      await fetch(`${API_BASE}/download/${jobId}/cancel`, { method: "POST" });
      setToast("Download cancelled");
    } catch (err) {
      setToast("Failed to cancel: " + err.message);
    }
  };

  const clearHistory = () => setHistory([]);

  const currentInterfacesProgress = Object.values(downloadStatus?.interfaces || {});
  const combinedCurrentSpeed = currentInterfacesProgress.reduce(
    (sum, item) => sum + Number(item.speed_mb_s || 0),
    0
  );
  const currentEta = useMemo(() => {
    if (!downloadStatus?.expected_size || !combinedCurrentSpeed) return 0;
    const remaining = downloadStatus.expected_size - (downloadStatus.total_downloaded || 0);
    if (remaining <= 0) return 0;
    return remaining / (combinedCurrentSpeed * 1024 * 1024);
  }, [downloadStatus, combinedCurrentSpeed]);

  const getInterfaceTone = (iface) => {
    const t = `${iface.interface_type || ""} ${iface.name || ""}`.toLowerCase();
    if (/(wi-?fi|wireless|wlan)/i.test(t)) {
      return { dot: "var(--wifi-color)", bar: "var(--wifi-color)", Icon: Wifi };
    }
    if (/(rndis|usb|mobile|samsung|huawei|xiaomi|remote ndis)/i.test(t)) {
      return { dot: "var(--ethernet-color)", bar: "var(--ethernet-color)", Icon: Smartphone };
    }
    return { dot: "var(--extra-color)", bar: "var(--extra-color)", Icon: Cable };
  };

  const selectedSlowestSpeed = useMemo(() => {
    const selected = interfaces.filter((iface) =>
      currentInterfacesProgress.some((item) => item.ip_address === iface.ip_address)
    );
    const valid = selected.map((i) => Number(i.speed_mb_s || 0)).filter((v) => v > 0);
    if (!valid.length) return 0;
    return Math.min(...valid);
  }, [interfaces, currentInterfacesProgress]);

  const speedupText = useMemo(() => {
    if (!downloadStatus?.expected_size || !downloadStatus.started_at) return "";
    const activeIfaces = Object.values(downloadStatus.interfaces || {});
    if (activeIfaces.length <= 1) return "";

    const elapsed =
      (downloadStatus.finished_at || Date.now() / 1000) - (downloadStatus.started_at || Date.now() / 1000);
    const { timeSaved } = analyzeTimeSaved(downloadStatus.expected_size, selectedSlowestSpeed, elapsed);
    if (timeSaved <= 0) return "";
    if (timeSaved < 5) return "faster than single connection";
    return `~${Math.round(timeSaved)}s faster than single connection`;
  }, [downloadStatus, selectedSlowestSpeed]);

  const handleQueueDragEnd = (event) => {
    const { active, over } = event;
    if (!active?.id || !over?.id || active.id === over.id) return;
    setQueue((prev) => {
      const oldIndex = prev.findIndex((item) => item.id === active.id);
      const newIndex = prev.findIndex((item) => item.id === over.id);
      if (oldIndex < 0 || newIndex < 0) return prev;
      return arrayMove(prev, oldIndex, newIndex);
    });
  };

  const handleSplashContinue = () => {
    localStorage.setItem(SEEN_KEY, "1");
    setSplashLeaving(true);
    setTimeout(() => {
      setShowSplash(false);
      setSplashLeaving(false);
    }, 300);
  };

  return (
    <main className="app-shell">
      {dragOverlay && (
        <div className="drag-overlay">
          <div className="drag-overlay-content">Drop URL to download</div>
        </div>
      )}
      {showSplash ? (
        <section className={`splash-panel ${splashLeaving ? "fade-out" : "fade-in"}`} style={{justifyContent: 'center', backgroundColor: '#e2e5e4', backgroundImage: 'none'}}>
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center' }}>
            <h1 className="logo-mini" style={{ fontSize: '48px', marginBottom: '16px', color: '#1a1a1a', gap: '12px' }}>
              <span className="logo-mark" style={{ width: '24px', height: '24px', borderRadius: '4px' }} /> Burst
            </h1>
            <p className="splash-tagline" style={{ fontSize: '20px', fontWeight: '400', color: '#1a2a3a', lineHeight: '1.3', marginBottom: '40px' }}>
              Combine your connections. Multiply your speed.
            </p>
            <button className="btn-start" style={{ width: '48px', height: '32px', padding: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', borderRadius: '6px', color: '#fff' }} onClick={handleSplashContinue}>
              →
            </button>
          </div>
        </section>
      ) : (
        <div ref={appRef} className="app-panel fade-in">
          <header className="top-bar">
            <h1 className="logo-mini">
              <span className="logo-mark" /> Burst
            </h1>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
              <span className="connections-pill">
                <span className="dot">●</span> {activeConnectionCount} active
              </span>
              <button style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-muted)' }} onClick={() => setShowSettingsModal(true)}>
                <Settings2 size={16} />
              </button>
            </div>
          </header>

          {hotplugBanners.map((banner) => (
            <div key={banner.id} className="hotplug-banner slide-in">
              <div className="hotplug-banner-text">
                📱 New connection detected — {banner.interface.name} ({banner.interface.type || "Unknown"})
              </div>
              <div className="hotplug-banner-actions">
                <button className="btn-add" onClick={async () => {
                  setHotplugBanners(prev => prev.filter(b => b.id !== banner.id));
                  setSelectedIps(prev => [...prev, banner.interface.ip]);
                  fetchInterfaces();
                  
                  if (downloadingRef.current && jobIdRef.current) {
                    try {
                      await fetch(`${API_BASE}/download/${jobIdRef.current}/add_interface`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ interface_ip: banner.interface.ip })
                      });
                      setToast(`${banner.interface.name} added — download rebalanced across ${validSelectedIpsRef.current.length + 1} connections`);
                    } catch (err) {
                      setToast("Failed to add interface: " + err.message);
                    }
                  }
                }}>Add to downloads</button>
                <button className="btn-ignore" onClick={() => {
                  setHotplugBanners(prev => prev.filter(b => b.id !== banner.id));
                  fetchInterfaces();
                }}>Ignore</button>
              </div>
            </div>
          ))}

          <div style={{ padding: '24px 24px 0 24px', fontSize: '11px', textTransform: 'uppercase', color: 'var(--text-muted)', fontWeight: 600 }}>
            ACTIVE CONNECTIONS
          </div>
          <section className="row-section iface-row">
            {renderedInterfaces.map((iface) => {
              const selected = validSelectedIps.includes(iface.ip_address);
              const tone = getInterfaceTone(iface);
              const Icon = tone.Icon;
              return (
                <button
                  key={iface.ip_address}
                  onClick={() => {
                    handleToggle(iface.ip_address);
                    if (!pillHintSeen) {
                      setPillHintSeen(true);
                      localStorage.setItem("burst_pill_hint", "1");
                    }
                  }}
                  onContextMenu={(e) => {
                    e.preventDefault();
                    setContextMenu({ x: e.clientX, y: e.clientY, ip: iface.ip_address, name: iface.name });
                  }}
                  className={`iface-pill-v3 relative ${selected ? "selected" : ""} ${iface.entering ? "pill-enter" : ""} ${
                    iface.exiting ? "pill-exit" : ""
                  }`}
                  style={{ "--tone": tone.dot }}
                >
                  {selected ? (
                    <CheckCircle2 size={13} style={{ position: 'absolute', top: '8px', right: '8px', color: 'var(--accent)' }} />
                  ) : (
                    <div style={{ position: 'absolute', top: '8px', right: '10px', fontSize: '16px', lineHeight: '10px', color: 'var(--text-muted)' }}>+</div>
                  )}
                  {downloadOnlyIps.includes(iface.ip_address) && (
                    <div style={{ position: 'absolute', bottom: '8px', right: '8px', fontSize: '10px', background: 'var(--surface-2)', padding: '2px 4px', borderRadius: '4px', color: 'var(--text-muted)' }} title="Deprioritized for general traffic">
                      ↓ only
                    </div>
                  )}
                  <div className="iface-pill-head">
                    <span className="iface-dot" />
                    <Icon size={12} />
                    <span>{shortName(iface.name, iface.interface_type)}</span>
                  </div>
                  <p
                    className={`iface-meta ${iface.speedFlash ? "speed-flash" : ""} ${
                      hasSpeedtestRun && Number(iface.speed_mb_s || 0) > 1
                        ? "speed-fast"
                        : hasSpeedtestRun && Number(iface.speed_mb_s || 0) > 0 && Number(iface.speed_mb_s || 0) < 0.5
                          ? "speed-slow"
                          : ""
                    }`}
                  >
                    {speedRefreshActive && hasSpeedtestRun ? <span className="speed-pulse" /> : null}
                    {hasSpeedtestRun && Number(iface.speed_mb_s || 0) > 0 ? formatSpeed(iface.speed_mb_s) : "— MB/s"}
                  </p>
                  {downloading && downloadStatus?.interfaces?.[iface.ip_address] && (
                    <span className={`iface-status-badge status-${downloadStatus.interfaces[iface.ip_address].status || "pending"}`}>
                      {downloadStatus.interfaces[iface.ip_address].weight_percent > 0
                        ? `${downloadStatus.interfaces[iface.ip_address].weight_percent}%`
                        : downloadStatus.interfaces[iface.ip_address].status === "paused_slow" ? "Slow"
                        : downloadStatus.interfaces[iface.ip_address].status === "disconnected" ? "Lost"
                        : downloadStatus.interfaces[iface.ip_address].status === "excluded" ? "Off"
                        : "—"}
                    </span>
                  )}
                </button>
              );
            })}
          </section>
          {!pillHintSeen && (
            <div style={{ padding: '0 24px 12px 24px', fontSize: '12px', color: 'var(--text-muted)', marginTop: '-12px' }}>
              Select connections to use for downloads
            </div>
          )}

          <section className="row-section" style={{ marginTop: '24px' }}>
            <div className={`url-row ${dropTargetActive ? "dropping" : ""}`} style={{ gridTemplateColumns: '1fr auto' }}>
              <input
                value={url}
                onChange={(e) => handleUrlChange(e.target.value)}
                placeholder="Paste a download URL..."
                className="url-input"
                onDrop={(event) => {
                  event.preventDefault();
                  setDropTargetActive(false);
                  const droppedUrl = readDroppedUrl(event);
                  if (droppedUrl) {
                    handleUrlChange(droppedUrl);
                  }
                }}
                onDragOver={(event) => {
                  event.preventDefault();
                  setDropTargetActive(true);
                }}
                onDragLeave={() => setDropTargetActive(false)}
              />
              {/* <button className="btn-ghost" onClick={addToQueue}>+ Queue</button> */}
              <button className="btn-download" onClick={handleDownloadClick} disabled={downloading || downloadBtnState === "checking"}>
                {downloadBtnState === "checking" ? "Checking..." : "↓ Download"}
              </button>
            </div>
            {downloadWarningMsg && (
              <div style={{ marginTop: '12px', fontSize: '12px', color: '#92400e', background: '#fef3c7', padding: '8px 12px', borderRadius: '6px' }}>
                {downloadWarningMsg}
              </div>
            )}
            {downloadErrorMsg && (
              <div style={{ marginTop: '12px', fontSize: '12px', color: '#991b1b', background: '#fee2e2', padding: '8px 12px', borderRadius: '6px' }}>
                {downloadErrorMsg}
              </div>
            )}
            <div className="output-line group" style={{ marginTop: '16px' }}>
              <Folder size={12} />
              {editingOutputPath ? (
                <input
                  value={outputPath}
                  onChange={(event) => setOutputPath(event.target.value)}
                  className="output-input"
                  onBlur={() => setEditingOutputPath(false)}
                  autoFocus
                />
              ) : (
                <button className="output-btn" style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'text' }} onClick={() => {
                  setEditingOutputPath(true);
                  if (pathHintCount < 3) {
                    setPathHintCount(999);
                    localStorage.setItem("burst_path_hint_count", "999");
                  }
                }}>
                  {outputPath}
                  <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="opacity-0 group-hover:opacity-100 transition-opacity"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/><path d="m15 5 4 4"/></svg>
                </button>
              )}
            </div>
            {pathHintCount < 3 && !editingOutputPath && (
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginLeft: '18px', marginTop: '2px' }}>
                Click to change save location
              </div>
            )}
          </section>

          {downloadStatus && (
            <section className="download-card-v3 slide-in">
              <div className="download-head">
                <div>
                  <p className="download-name">{downloadStatus.output_path?.split(/[\\/]/).pop() || "download.bin"}</p>
                  <p className="download-meta">{formatBytes(downloadStatus.expected_size)}</p>
                </div>
                <button className="cancel-btn" onClick={cancelDownload} title="Cancel download">
                  <X size={14} />
                </button>
              </div>
              <div className="progress-main">
                <div
                  className={`progress-fill ${downloading ? "progress-shimmer" : ""}`}
                  style={{
                    width: `${Math.min(
                      100,
                      ((downloadStatus.total_downloaded || 0) / Math.max(1, downloadStatus.expected_size || 1)) * 100
                    )}%`
                  }}
                />
              </div>
              <div className="iface-bars">
                {currentInterfacesProgress.map((item) => {
                  const iface = interfaces.find((i) => i.ip_address === item.ip_address);
                  const tone = iface ? getInterfaceTone(iface) : { bar: "var(--extra-color)" };
                  const pct = Math.min(100, ((item.downloaded || 0) / Math.max(1, downloadStatus.expected_size || 1)) * 100);
                  return (
                    <div key={item.ip_address} className="progress-mini">
                      <div className="progress-mini-bg">
                        <div
                          className="progress-mini-fill"
                          style={{ width: `${pct}%`, backgroundColor: tone.bar }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
              <div className="speed-line">{formatSpeed(combinedCurrentSpeed)}</div>
              <p className="speed-detail">
                {currentInterfacesProgress.map((item) => `${shortName(item.name, "")} ${Number(item.speed_mb_s || 0).toFixed(2)}`).join(" · ")}
              </p>
              <p className="eta-line">
                {formatEta(currentEta)}
                {speedupText ? ` · ${speedupText}` : ""}
              </p>
              {downloadStatus.status === "waiting_reconnect" && (
                <div className="reconnect-banner">
                  <AlertTriangle size={14} /> All connections lost — waiting to reconnect
                </div>
              )}
              {(downloadStatus.retry_events || []).length > 0 && (
                <details className="activity-log">
                  <summary>Activity ({downloadStatus.retry_events.length} events)</summary>
                  <ul>
                    {downloadStatus.retry_events.slice(-10).reverse().map((ev, i) => (
                      <li key={i}>
                        Chunk {ev.chunk_index}: {shortName(ev.from_interface, "")} → {shortName(ev.to_interface, "")}
                        <span className="activity-reason">{ev.reason}</span>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </section>
          )}

          {queue.length > 0 && (
            <section className="queue-list">
              <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleQueueDragEnd}>
                <SortableContext items={queue.map((item) => item.id)} strategy={verticalListSortingStrategy}>
                  {queue.map((item) => (
                    <SortableQueueItem key={item.id} item={item} />
                  ))}
                </SortableContext>
              </DndContext>
            </section>
          )}

          <section className="recent-head">
            <h2>Recent</h2>
            <button onClick={clearHistory}>Clear</button>
          </section>
          <section className="recent-list">
            {history.map((item) => (
              <article key={item.id} className="recent-item">
                <div className="recent-main">
                  {item.status === "completed" ? (
                    <CheckCircle2 size={13} color="var(--success)" />
                  ) : (
                    <CircleX size={13} color="var(--danger)" />
                  )}
                  <div className="recent-copy">
                    <p>{item.filename}</p>
                    <span>
                      {new Date(item.timestamp).toLocaleString()} ·{" "}
                      {item.status === "failed" ? <em className="failed-tag">Failed</em> : `avg ${formatSpeed(item.avgSpeed)}`}
                    </span>
                    {item.status === "failed" && item.error_reason ? (
                      <span className="error-reason">{item.error_reason.slice(0, 60)}</span>
                    ) : (
                      <span className="iface-dots-row">
                        {(item.interfaces_used || []).slice(0, 3).map((iface, idx) => (
                          <span
                            key={`${item.id}-${iface}-${idx}`}
                            className="iface-history-dot"
                            style={{
                              background:
                                idx === 0 ? "var(--ethernet-color)" : idx === 1 ? "var(--wifi-color)" : "var(--extra-color)"
                            }}
                          />
                        ))}
                      </span>
                    )}
                  </div>
                </div>
                <div className="recent-metric">
                  {item.time_saved > 0 ? `Saved ${Math.round(item.time_saved)}s` : item.status === "failed" ? "Failed" : formatSpeed(item.avgSpeed)}
                </div>
              </article>
            ))}
          </section>

          <section className="ext-row">
            <button className="ext-toggle" onClick={() => setShowExtension((v) => !v)}>
              <span>⚡ Browser Extension</span>
              <span className="soon-badge">Coming soon</span>
              <ChevronDown size={14} className={showExtension ? "open" : ""} />
            </button>
            {showExtension && (
              <div className="ext-body fade-in">
                <button disabled title="coming soon">
                  Install Chrome Extension
                </button>
                <button disabled title="coming soon">
                  Install Firefox Extension
                </button>
              </div>
            )}
          </section>
          <div className="card-footer">Burst v0.2</div>
          {newIfacePrompt && (
            <div className="iface-prompt slide-in">
              <p>
                <strong>{newIfacePrompt.name}</strong>
                {newIfacePrompt.isReturning
                  ? " reconnected — rejoin the download?"
                  : " detected — add to current download?"}
              </p>
              <div className="iface-prompt-actions">
                <button className="btn-prompt-yes" onClick={handleNewIfaceAccept}>
                  {newIfacePrompt.isReturning ? "Rejoin" : "Yes, boost speed"}
                </button>
                <button className="btn-prompt-no" onClick={handleNewIfaceDismiss}>No thanks</button>
              </div>
            </div>
          )}
          {toast && <div className="toast-line">{toast}</div>}
        </div>
      )}
      
      {showSettingsModal && appSettings && createPortal(
        <div className="settings-modal-overlay" onClick={() => setShowSettingsModal(false)}>
          <div className="settings-modal slide-in" onClick={e => e.stopPropagation()}>
            <div className="settings-modal-header">
              <h2>Settings</h2>
              <button className="settings-modal-close" onClick={() => setShowSettingsModal(false)}>
                <X size={18} />
              </button>
            </div>
            <div className="settings-modal-body">
              <div className="settings-section">
                <div className="settings-section-title">Chunking</div>
                <div className="settings-grid">
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
                </div>
              </div>
              <div className="settings-section">
                <div className="settings-section-title">Rebalancing</div>
                <div className="settings-grid">
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
                </div>
              </div>
              <div className="settings-section">
                <div className="settings-section-title">Reliability</div>
                <div className="settings-grid">
                  {[
                    { key: "DISCONNECT_DETECTION_TIMEOUT", label: "Disconnect timeout", unit: "sec", divisor: 1, step: 1 },
                    { key: "RETRY_SAME_INTERFACE_COOLDOWN", label: "Retry cooldown", unit: "sec", divisor: 1, step: 1 },
                    { key: "MAX_CONSECUTIVE_FAILURES", label: "Max failures", unit: "×", divisor: 1, step: 1 },
                    { key: "RETRY_ATTEMPTS", label: "Retry attempts", unit: "×", divisor: 1, step: 1 },
                    { key: "RETRY_DELAY_SECONDS", label: "Retry delay", unit: "sec", divisor: 1, step: 1 },
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
                </div>
              </div>
            </div>
            <div className="settings-modal-footer">
              <button className="btn-reset-settings" onClick={() => {
                fetch(`${API_BASE}/settings/reset`, { method: "POST" }).then(r => r.json()).then(d => setAppSettings(d.settings)).catch(() => {});
              }}>Reset to defaults</button>
              <button className="btn-save-settings" onClick={() => {
                fetch(`${API_BASE}/settings`, {
                  method: "POST",
                  headers: {"Content-Type": "application/json"},
                  body: JSON.stringify({settings: appSettings})
                }).then(() => setShowSettingsModal(false)).catch(() => {});
              }}>Save</button>
            </div>
          </div>
        </div>,
        document.body
      )}

      {contextMenu && createPortal(
        <div className="context-menu-overlay" style={{ position: 'fixed', inset: 0, zIndex: 1999 }} onClick={() => setContextMenu(null)} onContextMenu={(e) => { e.preventDefault(); setContextMenu(null); }}>
          <div className="context-menu slide-in" style={{ left: contextMenu.x, top: contextMenu.y }} onClick={e => e.stopPropagation()}>
            <button className="context-menu-item" onClick={() => {
              if (!validSelectedIps.includes(contextMenu.ip)) {
                setSelectedIps(prev => [...prev, contextMenu.ip]);
              }
              setContextMenu(null);
            }}>Add to downloads</button>
            <button className="context-menu-item" onClick={() => {
              setSelectedIps(prev => prev.filter(ip => ip !== contextMenu.ip));
              setContextMenu(null);
            }}>Remove from downloads</button>
            <div className="context-menu-divider" />
            <button className="context-menu-item" onClick={() => {
              setDownloadOnlyIps(prev => {
                const isOnly = prev.includes(contextMenu.ip);
                const updated = isOnly ? prev.filter(ip => ip !== contextMenu.ip) : [...prev, contextMenu.ip];
                localStorage.setItem("burst_download_only_ips", JSON.stringify(updated));
                return updated;
              });
              setContextMenu(null);
            }}>Download only mode {downloadOnlyIps.includes(contextMenu.ip) ? "✓" : ""}</button>
            <button className="context-menu-item" onClick={async () => {
              const ip = contextMenu.ip;
              setContextMenu(null);
              setSpeedRefreshActive(true);
              try {
                const resp = await fetch(`${API_BASE}/speedtest`, { method: "POST" });
                const data = await resp.json();
                if (resp.ok) {
                  setInterfaces(prev => prev.map(i => {
                    const found = (data.results || []).find((r) => r.ip_address === i.ip_address);
                    return found ? { ...i, speed_mb_s: found.speed_mb_s } : i;
                  }));
                  setRenderedInterfaces(prev => prev.map(i => {
                    const found = (data.results || []).find((r) => r.ip_address === i.ip_address);
                    return found ? { ...i, speed_mb_s: found.speed_mb_s, speedFlash: true } : i;
                  }));
                  setTimeout(() => setRenderedInterfaces(prev => prev.map(i => ({...i, speedFlash: false}))), 300);
                }
              } finally {
                setSpeedRefreshActive(false);
              }
            }}>Run speedtest</button>
            <div className="context-menu-divider" />
            <button className="context-menu-item" style={{ color: 'var(--danger)' }} onClick={() => {
              setIgnoredInterfaces(prev => {
                const updated = [...prev, contextMenu.ip];
                localStorage.setItem("burst_ignored_interfaces", JSON.stringify(updated));
                return updated;
              });
              setSelectedIps(prev => prev.filter(ip => ip !== contextMenu.ip));
              setRenderedInterfaces(prev => prev.filter(i => i.ip_address !== contextMenu.ip));
              setContextMenu(null);
            }}>Forget this interface</button>
          </div>
        </div>,
        document.body
      )}

      {/* TODO: Electron wrapper will add system tray icon showing live combined speed */}
      {/* TODO: Extension sends POST to localhost:8000/download when user clicks download in browser */}
    </main>
  );
}
