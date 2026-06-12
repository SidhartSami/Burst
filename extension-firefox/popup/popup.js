// Burst Extension — popup.js

const statusDot       = document.getElementById("statusDot");
const statusLabel     = document.getElementById("statusLabel");
const toggle          = document.getElementById("interceptToggle");
const toggleRow       = document.getElementById("toggleRow");
const adaptersSection = document.getElementById("adaptersSection");
const interfacesList  = document.getElementById("interfacesList");
const openAppBtn      = document.getElementById("openApp");

let pollIntervalId = null;

// ── Toggle state initialization ──────────────────────────────────────────────

chrome.storage.local.get({ burstEnabled: true }, ({ burstEnabled }) => {
  toggle.checked = burstEnabled;
});

toggle.addEventListener("change", () => {
  chrome.storage.local.set({ burstEnabled: toggle.checked });
});

// ── Status & Active Interfaces Check ──────────────────────────────────────────

function checkStatus() {
  chrome.runtime.sendMessage({ type: "CHECK_STATUS" }, (res) => {
    if (res?.alive) {
      statusDot.className = "status-dot online";
      statusLabel.textContent = "Burst is running";
      
      toggle.disabled = false;
      toggleRow.classList.remove("disabled");
      
      adaptersSection.style.display = "block";
      
      openAppBtn.className = "open-app-link";
      
      fetchInterfaces();
      
      if (!pollIntervalId) {
        pollIntervalId = setInterval(() => {
          fetchInterfaces();
        }, 3000);
      }
    } else {
      statusDot.className = "status-dot offline";
      statusLabel.textContent = "Burst is not running";
      
      toggle.disabled = true;
      toggleRow.classList.add("disabled");
      
      adaptersSection.style.display = "none";
      
      openAppBtn.className = "open-app-btn";
      
      if (pollIntervalId) {
        clearInterval(pollIntervalId);
        pollIntervalId = null;
      }
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

// ── Wake up App click ─────────────────────────────────────────────────────────

openAppBtn.addEventListener("click", (e) => {
  chrome.runtime.sendMessage({ type: "CHECK_STATUS" });
  setTimeout(checkStatus, 1500);
});

// Initialize status check
checkStatus();
// Also check status on popup show
document.addEventListener("DOMContentLoaded", checkStatus);
