// Burst Extension — popup.js

const statusDot   = document.getElementById("statusDot");
const statusLabel = document.getElementById("statusLabel");
const urlInput    = document.getElementById("urlInput");
const sendBtn     = document.getElementById("sendBtn");
const feedback    = document.getElementById("feedback");
const toggle      = document.getElementById("interceptToggle");

// ── Status check ──────────────────────────────────────────────────────────────

chrome.runtime.sendMessage({ type: "CHECK_STATUS" }, (res) => {
  if (res?.alive) {
    statusDot.className = "status-dot online";
    statusLabel.textContent = "Burst is running";
  } else {
    statusDot.className = "status-dot offline";
    statusLabel.textContent = "Burst is not running — open the app first";
    sendBtn.disabled = true;
  }
});

// ── Toggle state ──────────────────────────────────────────────────────────────

chrome.storage.local.get({ burstEnabled: true }, ({ burstEnabled }) => {
  toggle.checked = burstEnabled;
});

toggle.addEventListener("change", () => {
  chrome.storage.local.set({ burstEnabled: toggle.checked });
});

// ── Manual URL send ───────────────────────────────────────────────────────────

sendBtn.addEventListener("click", sendUrl);
urlInput.addEventListener("keydown", (e) => { if (e.key === "Enter") sendUrl(); });

function sendUrl() {
  const url = urlInput.value.trim();
  if (!url) return;

  sendBtn.disabled = true;
  setFeedback("Sending to Burst…", false);

  chrome.runtime.sendMessage({ type: "SEND_URL", url }, (response) => {
    sendBtn.disabled = false;
    if (response?.success) {
      setFeedback("✓ Queued in Burst!", false);
      urlInput.value = "";
    } else {
      setFeedback(`✗ ${response?.error || "Unknown error"}`, true);
    }
  });
}

function setFeedback(msg, isError) {
  feedback.textContent = msg;
  feedback.className = isError ? "feedback error" : "feedback";
  setTimeout(() => (feedback.textContent = ""), 4000);
}
