import React, { useState, useEffect, useMemo, useCallback, useRef } from "react";
import {
  Folder,
  FolderOpen,
  File,
  FileText,
  Search,
  HardDrive,
  CheckSquare,
  Square,
  MinusSquare,
  Download,
  X,
  ChevronRight,
  ChevronDown,
  AlertTriangle,
  Loader2,
} from "lucide-react";

function formatBytes(bytes) {
  if (!bytes || bytes <= 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return (bytes / Math.pow(k, i)).toFixed(i > 1 ? 1 : 0) + " " + sizes[i];
}

function buildFileTree(files) {
  const root = {
    name: "",
    path: "",
    isDirectory: true,
    children: {},
    files: [],
    totalSize: 0,
    fileIndices: [],
  };

  files.forEach((file) => {
    const rawPath = file.path || `file_${file.index}`;
    const normalized = rawPath.replace(/\\/g, "/");
    const parts = normalized.split("/").filter(Boolean);
    const filename = parts.pop() || "unknown";

    let current = root;
    let currentPath = "";

    for (const folder of parts) {
      currentPath = currentPath ? `${currentPath}/${folder}` : folder;
      if (!current.children[folder]) {
        current.children[folder] = {
          name: folder,
          path: currentPath,
          isDirectory: true,
          children: {},
          files: [],
          totalSize: 0,
          fileIndices: [],
        };
      }
      current = current.children[folder];
    }

    current.files.push({
      ...file,
      name: filename,
      fullPath: normalized,
    });
  });

  function computeStats(node) {
    let size = 0;
    let indices = [];

    node.files.forEach((f) => {
      size += f.size || 0;
      indices.push(f.index);
    });

    Object.values(node.children).forEach((child) => {
      computeStats(child);
      size += child.totalSize;
      indices.push(...child.fileIndices);
    });

    node.totalSize = size;
    node.fileIndices = indices;
  }

  computeStats(root);
  return root;
}

export function cleanReleaseTitle(raw) {
  if (!raw) return "torrent-download";
  let s = raw.trim();

  // 1. Replace Windows-illegal characters
  s = s.replace(/[:*?"<>|]/g, (match) => (match === ":" ? " - " : ""));

  // 2. Extract [Tag] at the end if present (e.g. [FitGirl Repack], [DODI Repack], etc.)
  const tagMatch = s.match(/\[([^\]]*(?:fitgirl|repack|dodi|elamigos|gog|rg|empress|skidrow|codex|flt|tenoke)[^\]]*)\]/i);
  let groupTag = "";
  if (tagMatch) {
    const tagInner = tagMatch[1].replace(/,?\s*\d+(?:\.\d+)?\s*(?:GB|MB|TB|GiB|MiB)/gi, "").trim().replace(/^[,\s]+|[,\s]+$/g, "");
    groupTag = `[${tagInner}]`;
    s = s.slice(0, tagMatch.index) + s.slice(tagMatch.index + tagMatch[0].length);
  }

  // 3. Remove sizes like 10.4 GB, [10.4 GB], (10.4 GB)
  s = s.replace(/\[?\s*\d+(?:\.\d+)?\s*(?:GB|MB|TB|GiB|MiB)\s*\]?/gi, "");

  // 4. Remove version/DLC/patch/build/multi info in parens: (v1.4.00... MULTi5)
  s = s.replace(/\s*\([^)]*(?:v\d|patch|dlc|multi|build|update|\.0|\.1|\.2|\.3|\.4|\.5|\.6|\.7|\.8|\.9)[^)]*\)/gi, "");

  // 5. Remove edition prefixes before version parens, e.g. ' - Praetor Edition', ' - Deluxe Edition'
  s = s.replace(/\s*-\s*[A-Za-z0-9\s]+Edition\b/gi, "");

  // 6. Re-attach group tag if present
  if (groupTag) {
    s = s.trim().replace(/[-–—\s]+$/, "") + " " + groupTag;
  }

  // 7. Normalize spaces and dashes
  s = s.replace(/\s*-\s*-\s*/g, " - ");
  s = s.replace(/\s{2,}/g, " ").trim().replace(/^[-–—\s]+|[-–—\s]+$/g, "");

  return s || "torrent-download";
}

export function extractCleanTorrentName(torrentData, files) {
  // 1. If files are loaded, check if all files share a common root directory
  if (files && files.length > 0) {
    const first = (files[0].path || "").replace(/\\/g, "/").trim();
    const parts = first.split("/").filter(Boolean);
    if (parts.length > 1) {
      const candidateRoot = parts[0];
      const allShare = files.every((f) => {
        const p = (f.path || "").replace(/\\/g, "/").trim();
        return p === candidateRoot || p.startsWith(candidateRoot + "/");
      });
      if (allShare && candidateRoot) {
        return cleanReleaseTitle(candidateRoot);
      }
    } else if (parts.length === 1 && files.length === 1) {
      return cleanReleaseTitle(parts[0]);
    }
  }

  // 2. Fall back to torrentData.name cleaned up
  return cleanReleaseTitle(torrentData?.name || "Torrent Download");
}

export default function TorrentAddModal({
  isOpen,
  torrentData,
  apiBase,
  defaultPath = "C:/Burst-Downloads/",
  onConfirm,
  onCancel,
  onBrowsePath,
}) {
  if (!isOpen || !torrentData) return null;

  const files = useMemo(() => torrentData.files || [], [torrentData.files]);
  const isMagnet = Boolean(torrentData.is_magnet);
  const isMetadataLoading = isMagnet && files.length === 0;

  const cleanName = useMemo(() => extractCleanTorrentName(torrentData, files), [torrentData, files]);

  // Destination directory state
  const userEditedPathRef = useRef(false);

  const [outputPath, setOutputPath] = useState(() => {
    const base = defaultPath.endsWith("/") || defaultPath.endsWith("\\")
      ? defaultPath
      : defaultPath + "/";
    if (torrentData.output_path) {
      const norm = torrentData.output_path.replace(/\\/g, "/");
      const lastSlash = norm.lastIndexOf("/");
      const dir = lastSlash !== -1 ? norm.slice(0, lastSlash + 1) : base;
      return dir + cleanName;
    }
    return base + cleanName;
  });

  // Track if initial selection has been performed so "None" button works permanently
  const hasInitializedSelectionRef = useRef(false);

  // Selected file indices (Set of numbers)
  const [selectedIndices, setSelectedIndices] = useState(() => {
    const initial = new Set();
    files.forEach((f) => {
      if (f.wanted !== false && f.priority !== 0) {
        initial.add(f.index);
      }
    });
    // If empty initially but files exist, select all by default
    if (initial.size === 0 && files.length > 0) {
      files.forEach((f) => initial.add(f.index));
    }
    if (files.length > 0) {
      hasInitializedSelectionRef.current = true;
    }
    return initial;
  });

  // Update selection ONCE when files first arrive (e.g. magnet metadata arriving via WebSocket)
  useEffect(() => {
    if (files.length > 0 && !hasInitializedSelectionRef.current) {
      hasInitializedSelectionRef.current = true;
      setSelectedIndices(new Set(files.map((f) => f.index)));
    }
  }, [files]);

  // Update outputPath if cleanName resolves and user hasn't typed a custom path
  useEffect(() => {
    if (!userEditedPathRef.current && cleanName && cleanName !== "Torrent Download") {
      setOutputPath((prev) => {
        const norm = (prev || "").replace(/\\/g, "/");
        const lastSlash = norm.lastIndexOf("/");
        const dir = lastSlash !== -1 ? norm.slice(0, lastSlash + 1) : defaultPath;
        const safeDir = dir.endsWith("/") || dir.endsWith("\\") ? dir : dir + "/";
        return safeDir + cleanName;
      });
    }
  }, [cleanName, defaultPath]);

  // Collapsed folder paths
  const [collapsedFolders, setCollapsedFolders] = useState(new Set());
  const [searchQuery, setSearchQuery] = useState("");

  // Disk space state
  const [diskSpace, setDiskSpace] = useState({
    freeBytes: null,
    totalBytes: null,
    loading: false,
  });

  // Query disk space when output path changes
  useEffect(() => {
    let active = true;
    const fetchDiskSpace = async () => {
      setDiskSpace((prev) => ({ ...prev, loading: true }));
      try {
        const resp = await fetch(
          `${apiBase}/disk-space?path=${encodeURIComponent(outputPath)}`
        );
        if (!resp.ok) throw new Error("Disk space query failed");
        const data = await resp.json();
        if (active) {
          setDiskSpace({
            freeBytes: data.free_bytes,
            totalBytes: data.total_bytes,
            loading: false,
          });
        }
      } catch {
        if (active) {
          setDiskSpace((prev) => ({ ...prev, loading: false }));
        }
      }
    };

    const timer = setTimeout(fetchDiskSpace, 200);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [outputPath, apiBase]);

  // Build tree
  const fileTree = useMemo(() => buildFileTree(files), [files]);

  // Compute total selected size and total torrent size
  const totalTorrentSize = torrentData.total_size || fileTree.totalSize || 0;
  const selectedSize = useMemo(() => {
    let sum = 0;
    files.forEach((f) => {
      if (selectedIndices.has(f.index)) {
        sum += f.size || 0;
      }
    });
    return sum;
  }, [files, selectedIndices]);

  const selectedCount = selectedIndices.size;
  const totalCount = files.length;
  const percentSelected = totalTorrentSize > 0 ? (selectedSize / totalTorrentSize) * 100 : 0;

  // Disk space validation
  const isInsufficientSpace =
    diskSpace.freeBytes !== null &&
    selectedSize > diskSpace.freeBytes;

  // Toggle single file
  const toggleFile = useCallback((index) => {
    setSelectedIndices((prev) => {
      const next = new Set(prev);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  }, []);

  // Toggle folder (recursive)
  const toggleFolder = useCallback((folder) => {
    setSelectedIndices((prev) => {
      const next = new Set(prev);
      const allSelected = folder.fileIndices.every((idx) => next.has(idx));
      if (allSelected) {
        // Deselect all in this folder
        folder.fileIndices.forEach((idx) => next.delete(idx));
      } else {
        // Select all in this folder
        folder.fileIndices.forEach((idx) => next.add(idx));
      }
      return next;
    });
  }, []);

  // Collapse / expand folder
  const toggleCollapse = useCallback((folderPath) => {
    setCollapsedFolders((prev) => {
      const next = new Set(prev);
      if (next.has(folderPath)) {
        next.delete(folderPath);
      } else {
        next.add(folderPath);
      }
      return next;
    });
  }, []);

  // Select all / none
  const handleSelectAll = (e) => {
    e?.preventDefault?.();
    e?.stopPropagation?.();
    setSelectedIndices(new Set(files.map((f) => f.index)));
  };

  const handleSelectNone = (e) => {
    e?.preventDefault?.();
    e?.stopPropagation?.();
    setSelectedIndices(new Set());
  };

  // Browse folder handler
  const handleBrowse = async () => {
    if (onBrowsePath) {
      onBrowsePath((newDir) => {
        if (!newDir) return;
        const safeDir = newDir.endsWith("/") || newDir.endsWith("\\")
          ? newDir
          : newDir + "/";
        setOutputPath(safeDir + cleanName);
        userEditedPathRef.current = true;
      });
    }
  };

  // Confirm download
  const handleStart = () => {
    if (isMetadataLoading || selectedCount === 0 || isInsufficientSpace) return;

    // Build priorities dictionary: 4 = normal, 0 = skip
    const priorities = {};
    files.forEach((f) => {
      priorities[f.index] = selectedIndices.has(f.index) ? 4 : 0;
    });

    onConfirm({
      outputPath: outputPath.trim(),
      filePriorities: priorities,
      selectedIndices: Array.from(selectedIndices),
    });
  };

  // Helper to determine tri-state checkbox for a folder
  const getFolderCheckboxState = (folder) => {
    if (folder.fileIndices.length === 0) return "none";
    let count = 0;
    for (const idx of folder.fileIndices) {
      if (selectedIndices.has(idx)) count++;
    }
    if (count === folder.fileIndices.length) return "all";
    if (count > 0) return "some";
    return "none";
  };

  // Render tree node recursively
  const renderTreeNode = (node, depth = 0) => {
    const q = searchQuery.trim().toLowerCase();

    // Check if node matches or has matching descendants
    const matchesSearch = (item) => {
      if (!q) return true;
      if (item.name.toLowerCase().includes(q)) return true;
      if (item.fullPath && item.fullPath.toLowerCase().includes(q)) return true;
      return false;
    };

    const hasMatchingDescendants = (dir) => {
      if (!q) return true;
      if (dir.files.some(matchesSearch)) return true;
      return Object.values(dir.children).some(hasMatchingDescendants);
    };

    return (
      <div key={node.path || "root"}>
        {/* Render child folders */}
        {Object.values(node.children)
          .filter(hasMatchingDescendants)
          .map((subfolder) => {
            const isCollapsed = collapsedFolders.has(subfolder.path);
            const folderState = getFolderCheckboxState(subfolder);

            return (
              <div key={subfolder.path} className="torrent-tree-folder-group">
                <div
                  className="torrent-tree-row torrent-tree-folder"
                  style={{ paddingLeft: `${depth * 18 + 10}px` }}
                >
                  <button
                    type="button"
                    className="torrent-tree-chevron"
                    onClick={() => toggleCollapse(subfolder.path)}
                    title={isCollapsed ? "Expand folder" : "Collapse folder"}
                  >
                    {isCollapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
                  </button>

                  <div
                    className="torrent-tree-check-btn"
                    onClick={() => toggleFolder(subfolder)}
                    title="Toggle folder selection"
                  >
                    {folderState === "all" ? (
                      <CheckSquare size={16} className="torrent-check-icon checked" />
                    ) : folderState === "some" ? (
                      <MinusSquare size={16} className="torrent-check-icon partial" />
                    ) : (
                      <Square size={16} className="torrent-check-icon unchecked" />
                    )}
                  </div>

                  <div
                    className="torrent-tree-name-wrap"
                    onClick={() => toggleCollapse(subfolder.path)}
                  >
                    {isCollapsed ? (
                      <Folder size={15} className="torrent-folder-icon" />
                    ) : (
                      <FolderOpen size={15} className="torrent-folder-icon" />
                    )}
                    <span className="torrent-folder-name" title={subfolder.path}>
                      {subfolder.name}
                    </span>
                  </div>

                  <div className="torrent-tree-meta">
                    <span className="torrent-item-count">
                      {subfolder.fileIndices.length} item{subfolder.fileIndices.length !== 1 ? "s" : ""}
                    </span>
                    <span className="torrent-size-badge">
                      {formatBytes(subfolder.totalSize)}
                    </span>
                  </div>
                </div>

                {!isCollapsed && renderTreeNode(subfolder, depth + 1)}
              </div>
            );
          })}

        {/* Render files in current node */}
        {node.files
          .filter(matchesSearch)
          .map((file) => {
            const isChecked = selectedIndices.has(file.index);
            return (
              <div
                key={file.index}
                className={`torrent-tree-row torrent-tree-file ${isChecked ? "selected" : ""}`}
                style={{ paddingLeft: `${(depth + (node.name ? 1 : 0)) * 18 + 26}px` }}
                onClick={() => toggleFile(file.index)}
              >
                <div className="torrent-tree-check-btn">
                  {isChecked ? (
                    <CheckSquare size={16} className="torrent-check-icon checked" />
                  ) : (
                    <Square size={16} className="torrent-check-icon unchecked" />
                  )}
                </div>

                <div className="torrent-tree-name-wrap">
                  <FileText size={14} className="torrent-file-icon" />
                  <span className="torrent-file-name" title={file.fullPath || file.name}>
                    {file.name}
                  </span>
                </div>

                <div className="torrent-tree-meta">
                  <span className="torrent-size-badge mono">
                    {formatBytes(file.size)}
                  </span>
                </div>
              </div>
            );
          })}
      </div>
    );
  };

  return (
    <div className="modal-overlay torrent-modal-backdrop" onClick={onCancel}>
      <div
        className="torrent-modal-card slide-in"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="torrent-modal-header">
          <div className="torrent-modal-title-area">
            <img src="/logo.png" alt="Burst" className="torrent-modal-logo" />
            <div className="torrent-title-wrap">
              <h2 className="torrent-modal-title">Add Torrent</h2>
            </div>
          </div>
          <button
            type="button"
            className="torrent-modal-close"
            onClick={onCancel}
            title="Cancel and close"
          >
            <X size={18} />
          </button>
        </div>

        {/* Destination Path Row */}
        <div className="torrent-modal-section path-section">
          <div className="torrent-section-header">
            <label className="torrent-section-label">Save Location</label>
            <div className="torrent-disk-gauge">
              {diskSpace.loading ? (
                <span className="disk-badge loading">
                  <Loader2 size={12} className="spin" /> Checking disk...
                </span>
              ) : isInsufficientSpace ? (
                <span className="disk-badge danger" title="Available space on this drive is insufficient for the selected files">
                  <AlertTriangle size={13} /> Insufficient Space ({formatBytes(selectedSize)} needed, {formatBytes(diskSpace.freeBytes)} free)
                </span>
              ) : diskSpace.freeBytes !== null ? (
                <span className="disk-badge ok">
                  <HardDrive size={13} /> Disk: {formatBytes(diskSpace.freeBytes)} Free
                </span>
              ) : null}
            </div>
          </div>

          <div className="torrent-path-input-group">
            <input
              type="text"
              className="torrent-path-input"
              value={outputPath}
              onChange={(e) => {
                userEditedPathRef.current = true;
                setOutputPath(e.target.value);
              }}
              placeholder="C:/Burst-Downloads/..."
            />
            <button
              type="button"
              className="torrent-browse-btn"
              onClick={handleBrowse}
              title="Browse destination folder"
            >
              <Folder size={15} />
              <span>Browse</span>
            </button>
          </div>
        </div>

        {/* File Selection Header & Toolbar */}
        <div className="torrent-modal-section files-section">
          <div className="torrent-files-toolbar">
            <div className="torrent-search-wrap">
              <Search size={14} className="torrent-search-icon" />
              <input
                type="text"
                className="torrent-search-input"
                placeholder="Filter files..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
              {searchQuery && (
                <button
                  type="button"
                  className="torrent-search-clear"
                  onClick={() => setSearchQuery("")}
                >
                  <X size={12} />
                </button>
              )}
            </div>

            <div className="torrent-quick-actions">
              <button
                type="button"
                className="torrent-action-link"
                onClick={handleSelectAll}
                disabled={files.length === 0}
              >
                Select All
              </button>
              <span className="torrent-action-sep">·</span>
              <button
                type="button"
                className="torrent-action-link"
                onClick={handleSelectNone}
                disabled={files.length === 0}
              >
                None
              </button>
            </div>
          </div>

          {/* File Tree Container */}
          <div className="torrent-tree-container custom-scroll">
            {isMetadataLoading ? (
              <div className="torrent-metadata-loading">
                <Loader2 size={28} className="spin accent-spin" />
                <div className="torrent-loading-title">
                  Retrieving torrent metadata from peers...
                </div>
                <div className="torrent-loading-sub">
                  Connected to {torrentData.peers || 0} peer{torrentData.peers !== 1 ? "s" : ""}. File tree will appear once metadata resolves.
                </div>
                <div className="torrent-pulsing-bar">
                  <div className="torrent-pulsing-fill" />
                </div>
              </div>
            ) : files.length === 0 ? (
              <div className="torrent-empty-files">
                No files found in torrent metadata.
              </div>
            ) : (
              renderTreeNode(fileTree)
            )}
          </div>
        </div>

        {/* Footer Summary & Action Controls */}
        <div className="torrent-modal-footer">
          <div className="torrent-footer-summary">
            <span className="torrent-footer-size">
              Selected: <strong>{formatBytes(selectedSize)}</strong> of {formatBytes(totalTorrentSize)}
            </span>
          </div>

          <div className="torrent-modal-actions">
            <button
              type="button"
              className="btn-secondary torrent-cancel-btn"
              onClick={onCancel}
            >
              Cancel
            </button>
            <button
              type="button"
              className="btn-primary torrent-start-btn"
              onClick={handleStart}
              disabled={isMetadataLoading || selectedCount === 0 || isInsufficientSpace}
            >
              <Download size={15} />
              <span>Start Download</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
