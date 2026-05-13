// Burst Extension — content.js
// Intercepts magnet: link clicks on the page before the browser handles them.

document.addEventListener("click", (e) => {
  const anchor = e.target.closest("a");
  if (!anchor) return;

  const href = anchor.href || "";
  if (!href.startsWith("magnet:")) return;

  e.preventDefault();
  e.stopImmediatePropagation();

  chrome.runtime.sendMessage({ type: "SEND_URL", url: href }, (response) => {
    if (response?.success) {
      // Brief visual feedback on the clicked link
      const original = anchor.textContent;
      anchor.textContent = "⚡ Sent to Burst!";
      setTimeout(() => (anchor.textContent = original), 2000);
    }
  });
}, true);
