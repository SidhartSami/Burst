import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import ReactDOM from "react-dom";
import { ExternalLink } from "lucide-react";
import { BROWSER_EXTENSION_URLS, openStoreUrl } from "../config/extensions";

/**
 * Reusable row option for each supported browser store.
 */
function BrowserStoreOption({ icon, name, storeName, url, onToast }) {
  const isAvailable = Boolean(url);

  return (
    <button
      type="button"
      className="extensions-popover-option"
      disabled={!isAvailable}
      onClick={() => {
        if (isAvailable) {
          openStoreUrl(url, onToast);
        }
      }}
      title={isAvailable ? `Open ${name} extension on ${storeName}` : "Store link unavailable"}
    >
      <div className="extensions-popover-option-icon">
        {icon}
      </div>
      <div className="extensions-popover-option-text">
        <span className="extensions-popover-option-title">{name}</span>
        <span className="extensions-popover-option-subtitle">{storeName}</span>
      </div>
      <ExternalLink size={14} className="extensions-popover-option-ext" />
    </button>
  );
}

/**
 * Floating Popover Menu for Browser Extensions.
 * Positioned cleanly to the right of the Extensions trigger button with collision detection.
 */
export default function ExtensionsPopover({
  isOpen,
  onClose,
  triggerRef,
  chromeSvg,
  firefoxSvg,
  onToast,
}) {
  const popoverRef = useRef(null);
  const [coords, setCoords] = useState({ top: 0, left: 0 });

  // Compute position relative to trigger button with viewport edge collision checks
  const updatePosition = () => {
    if (!triggerRef?.current) return;
    const triggerRect = triggerRef.current.getBoundingClientRect();
    const popoverEl = popoverRef.current;

    const popoverWidth = popoverEl ? popoverEl.offsetWidth : 300;
    const popoverHeight = popoverEl ? popoverEl.offsetHeight : 230;

    // Preferred position: 8px to the right of trigger, top-aligned
    let left = triggerRect.right + 8;
    let top = triggerRect.top;

    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;

    // Collision check right edge: flip to left or shift if overflowing
    if (left + popoverWidth > viewportWidth - 12) {
      if (triggerRect.left - popoverWidth - 8 > 12) {
        left = triggerRect.left - popoverWidth - 8;
      } else {
        left = Math.max(12, viewportWidth - popoverWidth - 12);
      }
    }

    // Collision check bottom edge: shift upwards if overflowing
    if (top + popoverHeight > viewportHeight - 12) {
      top = Math.max(12, viewportHeight - popoverHeight - 12);
    }

    setCoords({ top, left });
  };

  useLayoutEffect(() => {
    if (isOpen) {
      updatePosition();
    }
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;

    // Reposition on window resize or scroll
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);

    // Outside click detection
    const handleMouseDown = (event) => {
      const popoverEl = popoverRef.current;
      const triggerEl = triggerRef?.current;

      if (popoverEl && !popoverEl.contains(event.target)) {
        if (!triggerEl || !triggerEl.contains(event.target)) {
          onClose();
        }
      }
    };

    // Keyboard Escape to dismiss and return focus
    const handleKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        triggerRef?.current?.focus();
      }
    };

    document.addEventListener("mousedown", handleMouseDown);
    window.addEventListener("keydown", handleKeyDown);

    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
      document.removeEventListener("mousedown", handleMouseDown);
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [isOpen, onClose, triggerRef]);

  if (!isOpen) return null;

  return ReactDOM.createPortal(
    <div
      ref={popoverRef}
      role="dialog"
      aria-label="Browser Integration"
      aria-modal="false"
      className="extensions-popover-panel"
      style={{
        position: "fixed",
        top: `${coords.top}px`,
        left: `${coords.left}px`,
        zIndex: 1000,
      }}
    >
      <div className="extensions-popover-header">
        <h4 className="extensions-popover-title">Browser Integration</h4>
        <p className="extensions-popover-desc">
          Install an extension to send browser downloads directly to Burst.
        </p>
      </div>

      <div className="extensions-popover-divider" />

      <div className="extensions-popover-options">
        <BrowserStoreOption
          icon={chromeSvg}
          name="Chrome / Edge"
          storeName="Chrome Web Store"
          url={BROWSER_EXTENSION_URLS.chrome}
          onToast={onToast}
        />
        <BrowserStoreOption
          icon={firefoxSvg}
          name="Firefox / Zen"
          storeName="Firefox Add-ons"
          url={BROWSER_EXTENSION_URLS.firefox}
          onToast={onToast}
        />
      </div>

      <div className="extensions-popover-divider" style={{ marginTop: "10px", marginBottom: "8px" }} />

      <div className="extensions-popover-footer">
        Once installed, downloads can be sent to Burst directly from your browser.
      </div>
    </div>,
    document.body
  );
}
