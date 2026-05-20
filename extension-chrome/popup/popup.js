// Burst Extension — popup.js

const statusDot      = document.getElementById("statusDot");
const statusLabel    = document.getElementById("statusLabel");
const toggle         = document.getElementById("interceptToggle");
const interfacesList = document.getElementById("interfacesList");
const openAppBtn     = document.getElementById("openApp");

// ── Status & Active Interfaces Check ──────────────────────────────────────────

function checkStatus() {
  chrome.runtime.sendMessage({ type: "CHECK_STATUS" }, (res) => {
    if (res?.alive) {
      statusDot.className = "status-dot online";
      statusLabel.textContent = "Burst is running";
      fetchInterfaces();
    } else {
      statusDot.className = "status-dot offline";
      statusLabel.textContent = "Burst is offline — launch the app to start";
      interfacesList.innerHTML = `<div class="interface-item loading">No adapters active (offline)</div>`;
    }
  });
}

function fetchInterfaces() {
  fetch("http://localhost:59284/interfaces?benchmark=false")
    .then(response => {
      if (!response.ok) throw new Error("Backend offline");
      return response.json();
    })
    .then(data => {
      const list = data.interfaces || [];
      if (list.length === 0) {
        interfacesList.innerHTML = `<div class="interface-item loading">No active adapters found</div>`;
      } else {
        interfacesList.innerHTML = list.map(iface => `
          <div class="interface-item">
            <span class="if-name" title="${iface.name || 'Adapter'}">${iface.name || 'Adapter'}</span>
            <span class="if-ip">${iface.ip_address}</span>
          </div>
        `).join("");
      }
    })
    .catch(err => {
      interfacesList.innerHTML = `<div class="interface-item loading">Failed to fetch adapters</div>`;
    });
}

// ── Toggle state ──────────────────────────────────────────────────────────────

chrome.storage.local.get({ burstEnabled: true }, ({ burstEnabled }) => {
  toggle.checked = burstEnabled;
});

toggle.addEventListener("change", () => {
  chrome.storage.local.set({ burstEnabled: toggle.checked });
});

// ── Wake up App click ─────────────────────────────────────────────────────────

openAppBtn.addEventListener("click", (e) => {
  e.preventDefault();
  chrome.runtime.sendMessage({ type: "CHECK_STATUS" });
  // Reload status shortly after
  setTimeout(checkStatus, 1500);
});

// Init
checkStatus();
