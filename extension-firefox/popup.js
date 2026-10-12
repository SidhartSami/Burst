const API_BASE = "http://127.0.0.1:59284";

document.addEventListener("DOMContentLoaded", async () => {
  const statusDot = document.getElementById("statusDot");
  const statusText = document.getElementById("statusText");
  const turboToggle = document.getElementById("turboToggle");
  const turboCard = document.getElementById("turboCard");
  const networkList = document.getElementById("networkList");
  const netCount = document.getElementById("netCount");
  const btnOpenApp = document.getElementById("btnOpenApp");

  // Load saved Turbo Stream preference
  chrome.storage.local.get(["turboStreamEnabled"], (res) => {
    const isEnabled = !!res.turboStreamEnabled;
    turboToggle.checked = isEnabled;
    if (isEnabled) {
      turboCard.classList.add("active");
    }
  });

  // Handle Turbo Stream Toggle
  turboToggle.addEventListener("change", () => {
    const isEnabled = turboToggle.checked;
    if (isEnabled) {
      turboCard.classList.add("active");
    } else {
      turboCard.classList.remove("active");
    }

    chrome.storage.local.set({ turboStreamEnabled: isEnabled });
    chrome.runtime.sendMessage({ type: "SET_TURBO_STREAM", enabled: isEnabled });
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
