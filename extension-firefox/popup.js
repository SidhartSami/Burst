const API_BASE = "http://127.0.0.1:59284";

document.addEventListener("DOMContentLoaded", async () => {
  const statusDot = document.getElementById("statusDot");
  const statusText = document.getElementById("statusText");
  const turboToggle = document.getElementById("turboToggle");
  const turboCard = document.getElementById("turboCard");
  const networkList = document.getElementById("networkList");
  const netCount = document.getElementById("netCount");
  const btnOpenApp = document.getElementById("btnOpenApp");

  const modeVideo = document.getElementById("modeVideo");
  const modeAll = document.getElementById("modeAll");
  const refreshHint = document.getElementById("refreshHint");
  let currentMode = "video";

  function updateModeUI(mode) {
    currentMode = mode;
    if (mode === "all") {
      modeAll.style.background = "rgba(255, 136, 0, 0.15)";
      modeAll.style.borderColor = "var(--accent)";
      modeAll.style.color = "var(--accent)";
      modeAll.style.fontWeight = "600";

      modeVideo.style.background = "var(--surface-2)";
      modeVideo.style.borderColor = "var(--border)";
      modeVideo.style.color = "var(--text-muted)";
      modeVideo.style.fontWeight = "400";
    } else {
      modeVideo.style.background = "rgba(255, 136, 0, 0.15)";
      modeVideo.style.borderColor = "var(--accent)";
      modeVideo.style.color = "var(--accent)";
      modeVideo.style.fontWeight = "600";

      modeAll.style.background = "var(--surface-2)";
      modeAll.style.borderColor = "var(--border)";
      modeAll.style.color = "var(--text-muted)";
      modeAll.style.fontWeight = "400";
    }
  }

  // Load saved Turbo Stream preference
  chrome.storage.local.get(["turboStreamEnabled", "turboStreamMode"], (res) => {
    const isEnabled = !!res.turboStreamEnabled;
    currentMode = res.turboStreamMode || "video";
    turboToggle.checked = isEnabled;
    updateModeUI(currentMode);
    if (isEnabled) {
      turboCard.classList.add("active");
      if (refreshHint) refreshHint.style.display = "block";
    }
  });

  if (modeVideo) {
    modeVideo.addEventListener("click", () => {
      updateModeUI("video");
      chrome.storage.local.set({ turboStreamMode: "video" });
      if (turboToggle.checked) {
        chrome.runtime.sendMessage({ type: "SET_TURBO_STREAM", enabled: true, mode: "video" });
      }
    });
  }

  if (modeAll) {
    modeAll.addEventListener("click", () => {
      updateModeUI("all");
      chrome.storage.local.set({ turboStreamMode: "all" });
      if (turboToggle.checked) {
        chrome.runtime.sendMessage({ type: "SET_TURBO_STREAM", enabled: true, mode: "all" });
      }
    });
  }

  // Handle Turbo Stream Toggle
  turboToggle.addEventListener("change", () => {
    const isEnabled = turboToggle.checked;
    if (isEnabled) {
      turboCard.classList.add("active");
      if (refreshHint) refreshHint.style.display = "block";
    } else {
      turboCard.classList.remove("active");
      if (refreshHint) refreshHint.style.display = "none";
    }

    chrome.storage.local.set({ turboStreamEnabled: isEnabled, turboStreamMode: currentMode });
    chrome.runtime.sendMessage({ type: "SET_TURBO_STREAM", enabled: isEnabled, mode: currentMode });
  });

  // Open App Button
  btnOpenApp.addEventListener("click", () => {
    chrome.runtime.sendMessage({ type: "OPEN_BURST_APP" });
  });

  // Fetch Burst Status and Interfaces
  try {
    const [settingsRes, ifacesRes] = await Promise.all([
      fetch(`${API_BASE}/settings`).catch(() => null),
      fetch(`${API_BASE}/interfaces?benchmark=false`).catch(() => null),
    ]);

    if (settingsRes && settingsRes.ok) {
      statusDot.classList.add("online");
      statusText.textContent = "Online";
    } else {
      statusDot.classList.remove("online");
      statusText.textContent = "Offline";
    }

    if (ifacesRes && ifacesRes.ok) {
      const data = await ifacesRes.json();
      const ifaces = data.interfaces || [];
      netCount.textContent = `${ifaces.length} active`;

      if (ifaces.length > 0) {
        networkList.innerHTML = ifaces.map((i) => {
          const label = i.name || i.ip_address;
          return `<div class="net-pill"><div class="net-dot"></div>${escapeHtml(label)}</div>`;
        }).join("");
      } else {
        networkList.innerHTML = `<span style="font-size: 10px; color: var(--text-muted);">No active interfaces</span>`;
      }
    } else {
      netCount.textContent = "0 active";
      networkList.innerHTML = `<span style="font-size: 10px; color: var(--text-muted);">Start Burst to bond networks</span>`;
    }
  } catch (err) {
    statusDot.classList.remove("online");
    statusText.textContent = "Offline";
  }
});

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}
