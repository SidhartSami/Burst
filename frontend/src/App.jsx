import React, { useEffect, useMemo, useRef, useState } from "react";
import { ResponsiveContainer, AreaChart, Area } from "recharts";
import { friendlyError } from "./utils/errors";
import SchedulePicker from "./components/SchedulePicker";
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
  Menu,
  Scan,
  ChevronDown,
  ChevronUp,
  Layers,
  ArrowLeft,
  Shield,
  Clock,
  Video,
  Music,
  Loader
} from "lucide-react";

const API_BASE = (window.location.port === "5173" || window.location.port === "4173")
  ? "http://127.0.0.1:59284"
  : window.location.origin;
const HISTORY_KEY = "burst_history";

const CHROME_SVG = (
  <svg version="1.1" id="Layer_1" xmlns="http://www.w3.org/2000/svg" x="0px" y="0px"
    viewBox="0 0 512 512">
  <path fill="#FFFFFF" d="M255.73,383.71c70.3,0,127.3-56.99,127.3-127.3s-56.99-127.3-127.3-127.3s-127.3,56.99-127.3,127.3
    S185.42,383.71,255.73,383.71z"/>
  <linearGradient id="SVGID_1_" gradientUnits="userSpaceOnUse" gradientTransform="translate(7978.7 8523.996)" r="80.797" cy="-8515.121" cx="-7907.187">
    <stop  offset="0" style={{stopColor:"#ffbd4f"}}/>
    <stop  offset="1" style={{stopColor:"#ff980e"}}/>
  </linearGradient>
  <path fill="url(#SVGID_1_)" d="M145.48,320.08L35.26,129.17c-22.35,38.7-34.12,82.6-34.12,127.29s11.76,88.59,34.11,127.29
    c22.35,38.7,54.49,70.83,93.2,93.17c38.71,22.34,82.61,34.09,127.3,34.08l110.22-190.92v-0.03c-11.16,19.36-27.23,35.44-46.58,46.62
    c-19.35,11.18-41.3,17.07-63.65,17.07s-44.3-5.88-63.66-17.05C172.72,355.52,156.65,339.44,145.48,320.08z"/>
  <linearGradient id="SVGID_2_" gradientUnits="userSpaceOnUse" gradientTransform="translate(7978.7 8523.996)" r="80.797" cy="-8482.089" cx="-7936.711">
    <stop  offset="0" style={{stopColor:"#fcc934"}}/>
    <stop  offset="1" style={{stopColor:"#fbbc04"}}/>
  </linearGradient>
  <path fill="url(#SVGID_2_)" d="M365.96,320.08L255.74,510.99c44.69,0.01,88.59-11.75,127.29-34.1
    c38.7-22.34,70.84-54.48,93.18-93.18c22.34-38.7,34.1-82.61,34.09-127.3c-0.01-44.69-11.78-88.59-34.14-127.28H255.72l-0.03,0.02
    c22.35-0.01,44.31,5.86,63.66,17.03c19.36,11.17,35.43,27.24,46.61,46.59c11.18,19.35,17.06,41.31,17.06,63.66
    C383.03,278.77,377.14,300.72,365.96,320.08L365.96,320.08z"/>
  <path fill="#1A73E8" d="M255.73,357.21c55.66,0,100.78-45.12,100.78-100.78s-45.12-100.78-100.78-100.78
    s-100.78,45.12-100.78,100.78S200.07,357.21,255.73,357.21z"/>
  <linearGradient id="SVGID_3_" gradientUnits="userSpaceOnUse" gradientTransform="translate(7978.7 8523.996)" r="118.081" cy="-8535.981" cx="-7915.977">
    <stop  offset="0" style={{stopColor:"#d93025"}}/>
    <stop  offset="1" style={{stopColor:"#ea4335"}}/>
  </linearGradient>
  <path fill="url(#SVGID_3_)" d="M255.73,129.14h220.45C453.84,90.43,421.7,58.29,383,35.95C344.3,13.6,300.4,1.84,255.71,1.84
    c-44.69,0-88.59,11.77-127.29,34.12c-38.7,22.35-70.83,54.5-93.16,93.2l110.22,190.92l0.03,0.02
    c-11.18-19.35-17.08-41.3-17.08-63.65s5.87-44.31,17.04-63.66c11.17-19.36,27.24-35.43,46.6-46.6
    C211.42,135.01,233.38,129.13,255.73,129.14z"/>
  </svg>
);
const FIREFOX_SVG = (
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 87.419 81.967"><defs><radialGradient gradientUnits="userSpaceOnUse" gradientTransform="translate(7978.7 8523.996)" r="80.797" cy="-8515.121" cx="-7907.187" id="b"><stop stop-color="#ffbd4f" offset=".129"/><stop stop-color="#ffac31" offset=".186"/><stop stop-color="#ff9d17" offset=".247"/><stop stop-color="#ff980e" offset=".283"/><stop stop-color="#ff563b" offset=".403"/><stop stop-color="#ff3750" offset=".467"/><stop stop-color="#f5156c" offset=".71"/><stop stop-color="#eb0878" offset=".782"/><stop stop-color="#e50080" offset=".86"/></radialGradient><radialGradient gradientUnits="userSpaceOnUse" gradientTransform="translate(7978.7 8523.996)" r="80.797" cy="-8482.089" cx="-7936.711" id="c"><stop stop-color="#960e18" offset=".3"/><stop stop-opacity=".74" stop-color="#b11927" offset=".351"/><stop stop-opacity=".343" stop-color="#db293d" offset=".435"/><stop stop-opacity=".094" stop-color="#f5334b" offset=".497"/><stop stop-opacity="0" stop-color="#ff3750" offset=".53"/></radialGradient><radialGradient gradientUnits="userSpaceOnUse" gradientTransform="translate(7978.7 8523.996)" r="58.534" cy="-8533.457" cx="-7926.97" id="d"><stop stop-color="#fff44f" offset=".132"/><stop stop-color="#ffdc3e" offset=".252"/><stop stop-color="#ff9d12" offset=".506"/><stop stop-color="#ff980e" offset=".526"/></radialGradient><radialGradient gradientUnits="userSpaceOnUse" gradientTransform="translate(7978.7 8523.996)" r="38.471" cy="-8460.984" cx="-7945.648" id="e"><stop stop-color="#3a8ee6" offset=".353"/><stop stop-color="#5c79f0" offset=".472"/><stop stop-color="#9059ff" offset=".669"/><stop stop-color="#c139e6" offset="1"/></radialGradient><radialGradient gradientUnits="userSpaceOnUse" gradientTransform="matrix(.972 -.235 .275 1.138 10095.002 7833.794)" r="20.397" cy="-8491.546" cx="-7935.62" id="f"><stop stop-opacity="0" stop-color="#9059ff" offset=".206"/><stop stop-opacity=".064" stop-color="#8c4ff3" offset=".278"/><stop stop-opacity=".45" stop-color="#7716a8" offset=".747"/><stop stop-opacity=".6" stop-color="#6e008b" offset=".975"/></radialGradient><radialGradient gradientUnits="userSpaceOnUse" gradientTransform="translate(7978.7 8523.996)" r="27.676" cy="-8518.427" cx="-7937.731" id="g"><stop stop-color="#ffe226" offset="0"/><stop stop-color="#ffdb27" offset=".121"/><stop stop-color="#ffc82a" offset=".295"/><stop stop-color="#ffa930" offset=".502"/><stop stop-color="#ff7e37" offset=".732"/><stop stop-color="#ff7139" offset=".792"/></radialGradient><radialGradient gradientUnits="userSpaceOnUse" gradientTransform="translate(7978.7 8523.996)" r="118.081" cy="-8535.981" cx="-7915.977" id="h"><stop stop-color="#fff44f" offset=".113"/><stop stop-color="#ff980e" offset=".456"/><stop stop-color="#ff5634" offset=".622"/><stop stop-color="#ff3647" offset=".716"/><stop stop-color="#e31587" offset=".904"/></radialGradient><radialGradient gradientUnits="userSpaceOnUse" gradientTransform="matrix(.105 .995 -.653 .069 -4680.304 8470.187)" r="86.499" cy="-8522.859" cx="-7927.165" id="i"><stop stop-color="#fff44f" offset="0"/><stop stop-color="#ffe847" offset=".06"/><stop stop-color="#ffc830" offset=".168"/><stop stop-color="#ff980e" offset=".304"/><stop stop-color="#ff8b16" offset=".356"/><stop stop-color="#ff672a" offset=".455"/><stop stop-color="#ff3647" offset=".57"/><stop stop-color="#e31587" offset=".737"/></radialGradient><radialGradient gradientUnits="userSpaceOnUse" gradientTransform="translate(7978.7 8523.996)" r="73.72" cy="-8508.176" cx="-7938.383" id="j"><stop stop-color="#fff44f" offset=".137"/><stop stop-color="#ff980e" offset=".48"/><stop stop-color="#ff5634" offset=".592"/><stop stop-color="#ff3647" offset=".655"/><stop stop-color="#e31587" offset=".904"/></radialGradient><radialGradient gradientUnits="userSpaceOnUse" gradientTransform="translate(7978.7 8523.996)" r="80.686" cy="-8503.861" cx="-7918.923" id="k"><stop stop-color="#fff44f" offset=".094"/><stop stop-color="#ffe141" offset=".231"/><stop stop-color="#ffaf1e" offset=".509"/><stop stop-color="#ff980e" offset=".626"/></radialGradient><linearGradient gradientTransform="translate(3.7 -.004)" gradientUnits="userSpaceOnUse" y2="74.468" x2="6.447" y1="12.393" x1="70.786" id="a"><stop stop-color="#fff44f" offset=".048"/><stop stop-color="#ffe847" offset=".111"/><stop stop-color="#ffc830" offset=".225"/><stop stop-color="#ff980e" offset=".368"/><stop stop-color="#ff8b16" offset=".401"/><stop stop-color="#ff672a" offset=".462"/><stop stop-color="#ff3647" offset=".534"/><stop stop-color="#e31587" offset=".705"/></linearGradient><linearGradient gradientTransform="translate(3.7 -.004)" gradientUnits="userSpaceOnUse" y2="66.806" x2="15.267" y1="12.061" x1="70.013" id="l"><stop stop-opacity=".8" stop-color="#fff44f" offset=".167"/><stop stop-opacity=".634" stop-color="#fff44f" offset=".266"/><stop stop-opacity=".217" stop-color="#fff44f" offset=".489"/><stop stop-opacity="0" stop-color="#fff44f" offset=".6"/></linearGradient></defs><path d="M79.616 26.827c-1.684-4.052-5.1-8.427-7.775-9.81a40.266 40.266 0 013.925 11.764l.007.065C71.391 17.92 63.96 13.516 57.891 3.924a47.099 47.099 0 01-.913-1.484 12.24 12.24 0 01-.427-.8 7.053 7.053 0 01-.578-1.535.1.1 0 00-.088-.1.138.138 0 00-.073 0c-.005 0-.013.009-.019.01l-.028.016.015-.026c-9.735 5.7-13.038 16.252-13.342 21.53a19.387 19.387 0 00-10.666 4.11 11.587 11.587 0 00-1-.757 17.968 17.968 0 01-.109-9.473 28.705 28.705 0 00-9.329 7.21h-.018c-1.536-1.947-1.428-8.367-1.34-9.708a6.928 6.928 0 00-1.294.687 28.225 28.225 0 00-3.788 3.245 33.845 33.845 0 00-3.623 4.347v.006-.007a32.733 32.733 0 00-5.2 11.743l-.052.256a61.89 61.89 0 00-.381 2.42c0 .029-.006.056-.009.085A36.937 36.937 0 005 41.042v.2a38.759 38.759 0 0076.954 6.554c.065-.5.118-.995.176-1.5a39.857 39.857 0 00-2.514-19.47zm-44.67 30.338c.181.087.351.18.537.264l.027.017q-.282-.135-.564-.281zm8.878-23.376zm31.952-4.934v-.037l.007.04z" fill="url(#a)"/><path d="M79.616 26.827c-1.684-4.052-5.1-8.427-7.775-9.81a40.266 40.266 0 013.925 11.764v.037l.007.04a35.1 35.1 0 01-1.206 26.159c-4.442 9.53-15.194 19.3-32.024 18.825-18.185-.515-34.2-14.01-37.194-31.683-.545-2.787 0-4.2.274-6.465A28.876 28.876 0 005 41.042v.2a38.759 38.759 0 0076.954 6.554c.065-.5.118-.995.176-1.5a39.857 39.857 0 00-2.514-19.47z" fill="url(#b)"/><path d="M79.616 26.827c-1.684-4.052-5.1-8.427-7.775-9.81a40.266 40.266 0 013.925 11.764v.037l.007.04a35.1 35.1 0 01-1.206 26.159c-4.442 9.53-15.194 19.3-32.024 18.825-18.185-.515-34.2-14.01-37.194-31.683-.545-2.787 0-4.2.274-6.465A28.876 28.876 0 005 41.042v.2a38.759 38.759 0 0076.954 6.554c.065-.5.118-.995.176-1.5a39.857 39.857 0 00-2.514-19.47z" fill="url(#c)"/><path d="M60.782 31.383c.084.059.162.118.241.177a21.1 21.1 0 00-3.6-4.695C45.377 14.817 54.266.742 55.765.027l.015-.022c-9.735 5.7-13.038 16.252-13.342 21.53.452-.031.9-.07 1.362-.07a19.56 19.56 0 0116.982 9.918z" fill="url(#d)"/><path d="M43.825 33.789c-.064.964-3.47 4.289-4.661 4.289-11.021 0-12.81 6.667-12.81 6.667.488 5.614 4.4 10.238 9.129 12.684.216.112.435.213.654.312q.569.252 1.138.466a17.235 17.235 0 005.043.973c19.317.906 23.059-23.1 9.119-30.066a13.38 13.38 0 019.345 2.269A19.56 19.56 0 0043.8 21.466c-.46 0-.91.038-1.362.069a19.387 19.387 0 00-10.666 4.11c.591.5 1.258 1.169 2.663 2.554 2.63 2.59 9.375 5.275 9.39 5.59z" fill="url(#e)"/><path d="M43.825 33.789c-.064.964-3.47 4.289-4.661 4.289-11.021 0-12.81 6.667-12.81 6.667.488 5.614 4.4 10.238 9.129 12.684.216.112.435.213.654.312q.569.252 1.138.466a17.235 17.235 0 005.043.973c19.317.906 23.059-23.1 9.119-30.066a13.38 13.38 0 019.345 2.269A19.56 19.56 0 0043.8 21.466c-.46 0-.91.038-1.362.069a19.387 19.387 0 00-10.666 4.11c.591.5 1.258 1.169 2.663 2.554 2.63 2.59 9.375 5.275 9.39 5.59z" fill="url(#f)"/><path d="M29.965 24.357c.314.2.573.374.8.53a17.968 17.968 0 01-.109-9.472 28.705 28.705 0 00-9.329 7.21c.189-.005 5.811-.106 8.638 1.732z" fill="url(#g)"/><path d="M5.354 42.159c2.991 17.674 19.009 31.168 37.194 31.683 16.83.476 27.582-9.294 32.024-18.825a35.1 35.1 0 001.206-26.158v-.037c0-.03-.006-.046 0-.037l.007.065c1.375 8.977-3.191 17.674-10.329 23.555l-.022.05c-13.908 11.327-27.218 6.834-29.912 5q-.282-.135-.564-.281c-8.109-3.876-11.459-11.264-10.741-17.6a9.953 9.953 0 01-9.181-5.775 14.618 14.618 0 0114.249-.572 19.3 19.3 0 0014.552.572c-.015-.315-6.76-3-9.39-5.59-1.405-1.385-2.072-2.052-2.663-2.553a11.587 11.587 0 00-1-.758c-.23-.157-.489-.327-.8-.531-2.827-1.838-8.449-1.737-8.635-1.732h-.018c-1.536-1.947-1.428-8.367-1.34-9.708a6.928 6.928 0 00-1.294.687 28.225 28.225 0 00-3.788 3.245 33.845 33.845 0 00-3.638 4.337v.006-.007a32.733 32.733 0 00-5.2 11.743c-.019.079-1.396 6.099-.717 9.22z" fill="url(#h)"/><path d="M57.425 26.865a21.1 21.1 0 013.6 4.7c.213.16.412.32.581.476 8.787 8.1 4.183 19.55 3.84 20.365 7.138-5.881 11.7-14.578 10.329-23.555C71.391 17.92 63.96 13.516 57.891 3.924a47.099 47.099 0 01-.913-1.484 12.24 12.24 0 01-.427-.8 7.053 7.053 0 01-.578-1.535.1.1 0 00-.088-.1.138.138 0 00-.073 0c-.005 0-.013.009-.019.01l-.028.016c-1.499.71-10.388 14.786 1.66 26.834z" fill="url(#i)"/><path d="M61.6 32.036a8.083 8.083 0 00-.581-.476c-.079-.06-.157-.118-.241-.177a13.38 13.38 0 00-9.345-2.27c13.94 6.97 10.2 30.973-9.119 30.067a17.235 17.235 0 01-5.043-.973q-.569-.213-1.138-.466c-.219-.1-.438-.2-.654-.312l.027.017c2.694 1.839 16 6.332 29.912-5l.022-.05c.347-.81 4.951-12.263-3.84-20.36z" fill="url(#j)"/><path d="M26.354 44.745s1.789-6.667 12.81-6.667c1.191 0 4.6-3.325 4.661-4.29a19.3 19.3 0 01-14.552-.571 14.618 14.618 0 00-14.249.572 9.953 9.953 0 009.181 5.775c-.718 6.337 2.632 13.725 10.741 17.6.181.087.351.18.537.264-4.733-2.445-8.641-7.07-9.129-12.683z" fill="url(#k)"/><path d="M79.616 26.827c-1.684-4.052-5.1-8.427-7.775-9.81a40.266 40.266 0 013.925 11.764l.007.065C71.391 17.92 63.96 13.516 57.891 3.924a47.099 47.099 0 01-.913-1.484 12.24 12.24 0 01-.427-.8 7.053 7.053 0 01-.578-1.535.1.1 0 00-.088-.1.138.138 0 00-.073 0c-.005 0-.013.009-.019.01l-.028.016.015-.026c-9.735 5.7-13.038 16.252-13.342 21.53.452-.031.9-.07 1.362-.07a19.56 19.56 0 0116.982 9.918 13.38 13.38 0 00-9.345-2.27c13.94 6.97 10.2 30.973-9.119 30.067a17.235 17.235 0 01-5.043-.973q-.569-.213-1.138-.466c-.219-.1-.438-.2-.654-.312l.027.017q-.282-.135-.564-.281c.181.087.351.18.537.264-4.733-2.446-8.641-7.07-9.129-12.684 0 0 1.789-6.667 12.81-6.667 1.191 0 4.6-3.325 4.661-4.29-.015-.314-6.76-3-9.39-5.59-1.405-1.384-2.072-2.051-2.663-2.552a11.587 11.587 0 00-1-.758 17.968 17.968 0 01-.109-9.473 28.705 28.705 0 00-9.329 7.21h-.018c-1.536-1.947-1.428-8.367-1.34-9.708a6.928 6.928 0 00-1.294.687 28.225 28.225 0 00-3.788 3.245 33.845 33.845 0 00-3.623 4.347v.006-.007a32.733 32.733 0 00-5.2 11.743l-.052.256c-.073.34-.4 2.073-.447 2.445 0 .028 0-.03 0 0A45.094 45.094 0 005 41.042v.2a38.759 38.759 0 0076.954 6.554c.065-.5.118-.995.176-1.5a39.857 39.857 0 00-2.514-19.47zm-3.845 1.99l.007.042z" fill="url(#l)"/></svg>
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

function isHtmlPageLikely(urlString) {
  if (!urlString) return false;
  const clean = urlString.trim();
  if (!/^https?:\/\//i.test(clean)) return false;

  try {
    const parsed = new URL(clean);
    const pathname = parsed.pathname;
    
    // Get the last segment
    const segments = pathname.split('/').filter(Boolean);
    const lastSegment = segments[segments.length - 1] || "";
    
    if (!lastSegment) return true; // root of a site, definitely HTML

    // Check if there is any dot in the last segment
    if (!lastSegment.includes('.')) return true;

    // Check if it ends with known file extensions
    const knownExtensions = [
      'zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'xz', 'exe', 'msi', 'dmg', 'pkg', 'apk',
      'mp4', 'mkv', 'avi', 'mov', 'wmv', 'mp3', 'wav', 'flac', 'ogg', 'm4a',
      'pdf', 'epub', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
      'jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'ico', 'bmp', 'tiff',
      'iso', 'img', 'bin', 'torrent', 'deb', 'rpm'
    ];
    
    const parts = lastSegment.split('.');
    const ext = parts[parts.length - 1].toLowerCase();
    
    return !knownExtensions.includes(ext);
  } catch {
    return false;
  }
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

function DownloadCard({ jid, status, availableInterfaces, onToggle, onCancel, onPause, onResume, allUsedIps, isExpanded, onToggleExpand, showHint }) {
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

  const [showHintState, setShowHintState] = useState(showHint);
  const [hintOpacity, setHintOpacity] = useState(1);

  useEffect(() => {
    if (showHint) {
      const fadeTimer = setTimeout(() => {
        setHintOpacity(0);
      }, 2500);
      const removeTimer = setTimeout(() => {
        setShowHintState(false);
      }, 3000);
      return () => {
        clearTimeout(fadeTimer);
        clearTimeout(removeTimer);
      };
    }
  }, [showHint]);

  const getZeroBaseline = () => {
    const initialPoint = {};
    availableInterfaces.forEach(iface => {
      initialPoint[iface.ip_address] = 0;
    });
    return Array.from({ length: 60 }, () => ({ ...initialPoint }));
  };

  const [speedHistory, setSpeedHistory] = useState(getZeroBaseline);

  // Buffer real-time speed data (last 60 samples)
  useEffect(() => {
    if (status.status !== 'downloading' || isPaused) {
      setSpeedHistory(getZeroBaseline());
      return;
    }

    const newPoint = {};
    availableInterfaces.forEach(iface => {
      let isSelected = false;
      let speed = 0;
      if (status.type === "torrent") {
        isSelected = status.interface_ips?.includes(iface.ip_address) ?? (status.speeds && iface.ip_address in status.speeds);
        if (isSelected) {
          const speedBytes = status.speeds?.[iface.ip_address] || 0;
          speed = speedBytes / (1024 * 1024);
        }
      } else {
        const live = status.interfaces?.[iface.ip_address];
        isSelected = !!live && live.status !== "excluded" && live.status !== "cancelled";
        if (isSelected) {
          speed = live?.speed_mb_s || 0;
        }
      }
      if (isSelected) {
        newPoint[iface.ip_address] = speed;
      }
    });

    setSpeedHistory(prev => {
      const next = [...prev, newPoint];
      if (next.length > 60) {
        next.shift();
      }
      return next;
    });
  }, [status, availableInterfaces, isPaused]);

  // Log raw error message to console for debugging when failed
  useEffect(() => {
    if (status.status === 'failed' && status.error) {
      console.error(`[Download Error Debug] Job ${jid} failed with raw error:`, status.error);
    }
  }, [status.status, status.error, jid]);

  const activeIfacesList = useMemo(() => {
    return availableInterfaces.filter(iface => {
      if (status.type === "torrent") {
        return status.interface_ips?.includes(iface.ip_address) ?? (status.speeds && iface.ip_address in status.speeds);
      } else {
        const live = status.interfaces?.[iface.ip_address];
        return !!live && live.status !== "excluded" && live.status !== "cancelled";
      }
    });
  }, [availableInterfaces, status]);

  const chartData = useMemo(() => {
    if (speedHistory.length === 0) return [];
    if (speedHistory.length === 1) {
      return [speedHistory[0], speedHistory[0]];
    }
    return speedHistory;
  }, [speedHistory]);

  const showSparkline = status.status === 'downloading' && !isPaused && activeIfacesList.length > 0 && chartData.length > 0;

  const statusLabel = isPaused ? 'PAUSED' : (status.status === 'merging' ? 'MERGING...' : status.status);
  const statusClass = status.status === 'completed' ? 'completed' : (status.status === 'failed' ? 'failed' : (status.status === 'merging' ? 'merging' : (isPaused ? 'paused' : 'downloading')));

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

      <div className="iface-pills" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px' }}>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', flex: 1 }}>
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

        {status.status === 'downloading' && !isPaused && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexShrink: 0 }}>
            {showHintState && (
              <span className="sparkline-hint" style={{
                fontSize: '11px',
                color: 'var(--text-muted)',
                opacity: hintOpacity,
                transition: 'opacity 0.5s ease',
                pointerEvents: 'none'
              }}>
                speed graph
              </span>
            )}
            <button
              onClick={onToggleExpand}
              style={{
                background: 'none',
                border: 'none',
                padding: '4px',
                cursor: 'pointer',
                color: 'var(--text-muted)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                transition: 'transform 0.15s ease',
              }}
              title={isExpanded ? "Collapse speed graph" : "Expand speed graph"}
            >
              {isExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </button>
          </div>
        )}
      </div>

      <div
        style={{
          height: isExpanded && showSparkline ? '40px' : '0px',
          opacity: isExpanded && showSparkline ? 1 : 0,
          overflow: 'hidden',
          transition: 'height 150ms ease, opacity 150ms ease, margin-top 150ms ease, margin-bottom 150ms ease',
          marginTop: isExpanded && showSparkline ? '6px' : '0px',
          marginBottom: isExpanded && showSparkline ? '12px' : '0px',
          width: '100%',
        }}
      >
        {showSparkline && (
          <div className="dl-sparkline" style={{ height: '40px', width: '100%', overflow: 'hidden' }}>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart
                data={chartData}
                margin={{ top: 2, right: 0, left: 0, bottom: 2 }}
              >
                {activeIfacesList.map(iface => {
                  const tone = toneFor(shortName(iface.name, iface.interface_type));
                  const color = tone.dot;
                  return (
                    <Area
                      key={iface.ip_address}
                      type="monotone"
                      dataKey={iface.ip_address}
                      stroke={color}
                      fill={color}
                      fillOpacity={0.1}
                      strokeWidth={1.5}
                      dot={false}
                      activeDot={false}
                      isAnimationActive={false}
                    />
                  );
                })}
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      <div className="progress-track">
        <div className={`progress-fill ${statusClass}`} style={{ width: `${pct}%` }} />
      </div>

      <div className="dl-bottom">
        {isPaused ? (
          <span>Paused • {safePct.toFixed(1)}%</span>
        ) : status.status === 'failed' ? (
          <span style={{ color: 'var(--danger)', fontWeight: 500 }}>{friendlyError(status.error)}</span>
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
  const [expandedGraphs, setExpandedGraphs] = useState({});
  const hasShownGraphHintRef = useRef(false);
  const [activeVerifyJobId, setActiveVerifyJobId] = useState(null);
  const [checksumValue, setChecksumValue] = useState("");
  const [verificationLoading, setVerificationLoading] = useState(false);
  const [verificationResult, setVerificationResult] = useState(null);
  const [verificationError, setVerificationError] = useState(null);

  // Schedule state
  const [scheduleUrl, setScheduleUrl] = useState("");
  const [schedulePath, setSchedulePath] = useState("C:/Burst-Downloads/");
  const [scheduleDate, setScheduleDate] = useState("");
  const [scheduleTime, setScheduleTime] = useState("09:00");
  const [scheduleRepeat, setScheduleRepeat] = useState("once");
  const [scheduleError, setScheduleError] = useState(null);
  const [scheduleLoading, setScheduleLoading] = useState(false);
  const [schedules, setSchedules] = useState([]);
  const [missedSchedules, setMissedSchedules] = useState([]);
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

  // Clipboard monitor toast
  const [clipboardToast, setClipboardToast] = useState(null); // { url, ts }
  const clipboardToastRef = useRef([]); // recent URLs to dedupe

  // Webpage Scan Results view (in-place Option B)
  const [showBatchResultsView, setShowBatchResultsView] = useState(false);
  const [urlTypeHint, setUrlTypeHint] = useState(null); // null | "checking" | {type, title} | "error"
  const [batchResults, setBatchResults] = useState([]);
  const [batchScanning, setBatchScanning] = useState(false);
  const [batchError, setBatchError] = useState(null);
  const [batchSearchQuery, setBatchSearchQuery] = useState("");

  // yt-dlp state
  const [ytInfo, setYtInfo] = useState(null);       // null | false | { supported, title, thumbnail, ... }
  const [ytLoading, setYtLoading] = useState(false); // spinner while fetching info
  const [ytFormat, setYtFormat] = useState("");      // currently selected format_id
  const [ytStreamable, setYtStreamable] = useState(false); // Play-while-downloading sequential mode
  const ytDebounceRef = useRef(null);
  const ytCheckedUrlRef = useRef("");               // avoid duplicate fetches

  // ffmpeg auto-download progress toast
  // null | { status: 'downloading'|'done'|'error', percent: number, error: string }
  const [ffmpegToast, setFfmpegToast] = useState(null);

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
      fetch(`${API_BASE}/history`).then(r => r.json()).catch(() => null),
      fetch(`${API_BASE}/schedules`).then(r => r.json()).catch(() => null),
      fetch(`${API_BASE}/schedules/missed`).then(r => r.json()).catch(() => null),
    ])
      .then(([settingsData, activeJobsData, historyData, schedulesData, missedData]) => {
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
                type: item.type || "download",
                url: item.url || item.magnet_uri || ""
              };
            }).slice(0, 50);
          });
        }

        // Handle schedules
        if (schedulesData && schedulesData.schedules) {
          setSchedules(schedulesData.schedules);
        }
        if (missedData && missedData.missed && missedData.missed.length > 0) {
          setMissedSchedules(missedData.missed);
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
      } else if (msg.type === "clipboard_url") {
        const detectedUrl = msg.data.url;
        // Deduplicate within 60 seconds
        const now = Date.now();
        if (clipboardToastRef.current.includes(detectedUrl)) return;
        clipboardToastRef.current.push(detectedUrl);
        setTimeout(() => {
          clipboardToastRef.current = clipboardToastRef.current.filter(u => u !== detectedUrl);
        }, 60000);
        
        // Native System Notification instead of saturating the in-app UI
        if (typeof Notification !== "undefined") {
          if (Notification.permission !== "granted") {
            Notification.requestPermission();
          }
          if (Notification.permission === "granted") {
            const isMagnet = detectedUrl.startsWith("magnet:");
            const nTitle = isMagnet ? "Magnet Link Detected" : "Link Detected in Clipboard";
            const nBody = detectedUrl.length > 80 ? detectedUrl.slice(0, 80) + "..." : detectedUrl;
            try {
              const notification = new Notification(nTitle, {
                body: nBody,
                tag: "burst-clipboard",
                renotify: true
              });
              notification.onclick = () => {
                window.focus();
                setUrl(detectedUrl);
                setActiveTab("active");
                notification.close();
              };
            } catch (err) {
              console.error("[Notification API Error]:", err);
            }
          }
        }
      } else if (msg.type === "scheduled_start") {
        const { filename, job_id: jobId } = msg.data;
        setToast(`\u23F0 Scheduled download started: ${filename || "download"}`);
        if (jobId) {
          setActiveJobs(prev => prev.includes(jobId) ? prev : [...prev, jobId]);
        }
        setActiveTab("active");
        fetch(`${API_BASE}/schedules`).then(r => r.json()).then(d => setSchedules(d.schedules || [])).catch(() => {});
      } else if (msg.type === "ffmpeg_progress") {
        const { status, percent, error } = msg.data;
        if (status === "downloading") {
          setFfmpegToast({ status: "downloading", percent: percent || 0 });
        } else if (status === "done") {
          setFfmpegToast({ status: "done", percent: 100 });
          setTimeout(() => setFfmpegToast(null), 2500);
        } else if (status === "error") {
          setFfmpegToast({ status: "error", error: error || "Download failed" });
          setTimeout(() => setFfmpegToast(null), 6000);
        }
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

    // Reset yt-dlp picker if URL is cleared or changed
    if (!newUrl.trim()) {
      setYtInfo(null);
      setYtLoading(false);
      ytCheckedUrlRef.current = "";
    }

    const isTorrent = newUrl.trim().startsWith("magnet:") || newUrl.trim().endsWith(".torrent");
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
    // Extract filename from URL — but skip meaningless route segments like /watch, /shorts, /live
    const knownPageRoutes = new Set(['watch', 'shorts', 'live', 'embed', 'playlist', 'v', 'e', 'channel', 'c', 'user', 'feed']);
    const rawSegment = newUrl.split("/").pop()?.split("?")[0] || "";
    const filename = (rawSegment && !knownPageRoutes.has(rawSegment.toLowerCase()) && rawSegment.includes("."))
      ? rawSegment
      : "burst-download.bin";
    const bd = appSettings?.DOWNLOAD_PATH || localStorage.getItem("burst_default_path") || "C:/Burst-Downloads";
    const safeDir = bd.endsWith("/") || bd.endsWith("\\") ? bd : bd + "/";
    setOutputPath(safeDir + filename);
  };

  // yt-dlp: debounced URL detection (600ms)
  useEffect(() => {
    if (ytDebounceRef.current) clearTimeout(ytDebounceRef.current);

    const trimmed = url.trim();
    if (!trimmed || trimmed.startsWith("magnet:") || trimmed.endsWith(".torrent")) {
      setYtInfo(null);
      setYtLoading(false);
      return;
    }

    // Skip if already checked this URL
    if (ytCheckedUrlRef.current === trimmed) return;

    ytDebounceRef.current = setTimeout(async () => {
      ytCheckedUrlRef.current = trimmed;
      setYtLoading(true);
      setYtInfo(null);
      try {
        const resp = await fetch(`${API_BASE}/yt-dlp/info?url=${encodeURIComponent(trimmed)}`);
        const data = await resp.json();
        if (data.supported) {
          setYtInfo(data);
          setUrlTypeHint(null); // dismiss any stale "webpage" banner
          // Default: prefer 1080p, then second item, then first
          const fmts = data.formats || [];
          const prefer1080 = fmts.find(f => f.label === '1080p');
          const defaultFmt = prefer1080 || fmts[1] || fmts[0];
          setYtFormat(defaultFmt?.id || "");
        } else {
          setYtInfo(false); // explicitly not supported
        }
      } catch {
        setYtInfo(false);
      } finally {
        setYtLoading(false);
      }
    }, 600);

    return () => { if (ytDebounceRef.current) clearTimeout(ytDebounceRef.current); };
  }, [url]);

  const handleYtDlpDownload = async () => {
    if (!ytInfo || !ytFormat) return;
    const cleanUrl = url.trim();
    const selectedFmt = ytInfo.formats?.find(f => f.id === ytFormat);
    const label = selectedFmt?.label || ytFormat;

    // Always send the download directory (never a file path) to the backend.
    // The backend appends %(title)s.%(ext)s itself.
    const dlDir = (() => {
      const bd = appSettings?.DOWNLOAD_PATH || localStorage.getItem("burst_default_path") || "C:/Burst-Downloads";
      return bd.endsWith("/") || bd.endsWith("\\") ? bd : bd + "/";
    })();

    // Switch to Active tab immediately so user sees the job appear
    setUrl("");
    setYtInfo(null);
    ytCheckedUrlRef.current = "";
    setActiveTab("active");

    const effectiveIps = selectedIps.length ? selectedIps : renderedInterfaces.map(i => i.ip_address);

    const selectedFormatObj = ytInfo?.formats?.find(f => f.id === ytFormat);
    const targetFormatId = (ytStreamable && selectedFormatObj?.progressive_id) 
      ? selectedFormatObj.progressive_id 
      : ytFormat;

    try {
      const resp = await fetch(`${API_BASE}/yt-dlp/download`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: cleanUrl, format_id: targetFormatId, output_path: dlDir, label, interface_ips: effectiveIps, streamable: ytStreamable })
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || "Failed to start");
      if (data.job_id) {
        setActiveJobs(prev => prev.includes(data.job_id) ? prev : [...prev, data.job_id]);
      }
      setYtStreamable(false);
    } catch (err) {
      setToast(friendlyError(err.message));
    }
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
    if (typeof Notification !== "undefined" && Notification.permission === "default") {
      Notification.requestPermission();
    }
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
      console.error("[Download Click Error Debug]:", err);
      setToast(friendlyError(err.message));
    }
  };

  const handleDownloadClick = async () => {
    if (downloadBtnState === "checking") return;
    const targetUrl = url.trim();
    if (!targetUrl) return;

    // If the yt-dlp quality picker is active, route straight to it — no url-type check needed
    if (ytInfo && ytInfo.supported) {
      handleYtDlpDownload();
      return;
    }

    const isTorrent = targetUrl.startsWith("magnet:?") || targetUrl.endsWith(".torrent");

    if (!isTorrent) {
      // First check if this is a webpage or direct download
      setUrlTypeHint("checking");
      try {
        const typeResp = await fetch(`${API_BASE}/url-type?url=${encodeURIComponent(targetUrl)}`);
        const typeData = await typeResp.json();
        if (typeData.type === "html_page") {
          setUrlTypeHint({ type: "html_page", title: typeData.title || "Webpage" });
          return; // wait for user choice
        }
        setUrlTypeHint(null);
      } catch {
        setUrlTypeHint(null);
      }

      // Normal file download — analyze first
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
    setUrlTypeHint(null);
  };

  const handleBatchScan = async () => {
    const cleanUrl = url.trim();
    if (!cleanUrl) return;

    setShowBatchResultsView(true);
    setBatchScanning(true);
    setBatchError(null);
    setBatchResults([]);
    setUrlTypeHint(null);

    try {
      const bd = appSettings?.DOWNLOAD_PATH || localStorage.getItem("burst_default_path") || "C:/Burst-Downloads";
      const resp = await fetch(`${API_BASE}/batch-scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: cleanUrl, output_path: bd })
      });
      const data = await resp.json();
      if (data.error) {
        console.error("[Batch Scan Error Debug]:", data.error);
        setBatchError(friendlyError(data.error));
      } else if (data.urls && data.urls.length > 0) {
        setBatchResults(data.urls.map(u => ({ ...u, checked: true })));
      } else {
        setBatchError("No downloadable files found on this page.");
      }
    } catch (err) {
      console.error("[Batch Scan Exception Debug]:", err);
      setBatchError("Scan failed. Check the URL and try again.");
    } finally {
      setBatchScanning(false);
    }
  };

  const handleBatchDownload = async () => {
    const selected = batchResults.filter(f => f.checked);
    if (selected.length === 0) return;

    try {
      const bd = appSettings?.DOWNLOAD_PATH || localStorage.getItem("burst_default_path") || "C:/Burst-Downloads";
      const safeDir = bd.endsWith("/") || bd.endsWith("\\") ? bd : bd + "/";
      
      const resp = await fetch(`${API_BASE}/batch-download`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ urls: selected.map(f => f.url), output_path: safeDir })
      });

      if (!resp.ok) {
        throw new Error("Failed to queue downloads");
      }

      setToast(`Successfully queued ${selected.length} downloads`);
      setBatchResults([]);
      setBatchSearchQuery("");
      setShowBatchResultsView(false);
      setUrl("");
    } catch (err) {
      console.error("[Batch Download Error Debug]:", err);
      setToast(friendlyError(err.message || "Failed to queue downloads"));
    }
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
              type: payload.type || (activeTab === 'torrents' ? 'torrent' : 'download'),
              url: payload.url || payload.magnet_uri || ""
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

  const isValidHash = (str) => {
    const clean = str.trim();
    if (!/^[0-9a-fA-F]+$/.test(clean)) return false;
    return [32, 40, 64].includes(clean.length);
  };

  const handleVerify = async (item) => {
    const hash = checksumValue.trim();
    if (!hash) return;

    if (!isValidHash(hash)) {
      setVerificationError("That doesn't look like a valid hash");
      setVerificationResult(null);
      return;
    }

    setVerificationError(null);
    setVerificationResult(null);
    setVerificationLoading(true);

    try {
      const resp = await fetch(`${API_BASE}/verify-checksum`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          file_path: item.path,
          expected: hash
        })
      });
      const data = await resp.json();
      if (!resp.ok || data.error) {
        if (data.error === "File not found") {
          setVerificationError("File no longer exists at original path");
        } else {
          setVerificationError(data.error || "Verification failed");
        }
      } else {
        setVerificationResult(data);
      }
    } catch (err) {
      setVerificationError("Connection error — could not verify hash");
    } finally {
      setVerificationLoading(false);
    }
  };

  const handleReDownload = async (item) => {
    try {
      const isTorrent = item.type === "torrent" || (item.url && (item.url.startsWith("magnet:?") || item.url.endsWith(".torrent")));
      const endpoint = isTorrent ? `${API_BASE}/torrent/start` : `${API_BASE}/download`;
      const effectiveIps = selectedIps.length > 0 ? selectedIps : renderedInterfaces.map(i => i.ip_address);
      const body = isTorrent
        ? { magnet_uri: item.url, output_path: item.path, interface_ips: effectiveIps, bandwidth_limits: bandwidthLimits }
        : { url: item.url, output_path: item.path, interface_ips: effectiveIps, bandwidth_limits: bandwidthLimits };

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
        setToast(`Queued re-download for ${item.filename}`);
        setActiveVerifyJobId(null); // Close the drawer
        setActiveTab("active");
      }
    } catch (err) {
      console.error("[Re-download Exception Debug]:", err);
      setToast(friendlyError(err.message || "Failed to restart download"));
    }
  };

  const handleDeleteFile = async (item) => {
    try {
      const resp = await fetch(`${API_BASE}/file`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_path: item.path })
      });
      const data = await resp.json();
      if (!resp.ok || !data.success) {
        setToast(data.error || "Failed to delete file");
      } else {
        setToast(`Deleted file ${item.filename} successfully`);
        setActiveVerifyJobId(null); // Close the drawer
      }
    } catch (err) {
      console.error("[Delete File Exception Debug]:", err);
      setToast("Failed to connect to delete endpoint");
    }
  };

  // ---- Schedule helpers ----

  const formatScheduleTime = (isoStr) => {
    if (!isoStr) return "";
    try {
      const d = new Date(isoStr);
      const now = new Date();
      const todayStr = now.toDateString();
      const tomorrowStr = new Date(now.getTime() + 86400000).toDateString();
      const dStr = d.toDateString();
      const timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      if (dStr === todayStr) return `Today at ${timeStr}`;
      if (dStr === tomorrowStr) return `Tomorrow at ${timeStr}`;
      return d.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' }) + ` at ${timeStr}`;
    } catch { return isoStr; }
  };

  const extractScheduleName = (url) => {
    if (!url) return "download";
    try {
      const u = new URL(url);
      const segs = u.pathname.split("/").filter(Boolean);
      const last = segs[segs.length - 1] || u.hostname;
      return last.length > 40 ? last.slice(0, 40) + "…" : last;
    } catch {
      return url.length > 40 ? url.slice(0, 40) + "…" : url;
    }
  };

  const handleScheduleSubmit = async () => {
    setScheduleError(null);
    const cleanUrl = scheduleUrl.trim();
    const cleanPath = schedulePath.trim();

    if (!cleanUrl) { setScheduleError("URL is required"); return; }
    if (!scheduleDate || !scheduleTime) { setScheduleError("Date and time are required"); return; }

    const isoString = `${scheduleDate}T${scheduleTime}:00`;
    const fireAt = new Date(isoString);
    if (isNaN(fireAt.getTime()) || fireAt <= new Date()) {
      setScheduleError("Please pick a future time");
      return;
    }

    setScheduleLoading(true);
    try {
      const resp = await fetch(`${API_BASE}/schedule`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: cleanUrl,
          output_path: cleanPath,
          scheduled_time: isoString,
          repeat: "once",
        }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        setScheduleError(data.detail || "Failed to schedule download");
      } else {
        setSchedules(prev => [...prev, data].sort((a, b) => a.scheduled_time.localeCompare(b.scheduled_time)));
        setScheduleUrl("");
        setScheduleDate("");
        setScheduleTime("09:00");
        setScheduleRepeat("once");
        setToast("Download scheduled!");
      }
    } catch (err) {
      setScheduleError("Connection error — could not schedule download");
    } finally {
      setScheduleLoading(false);
    }
  };

  const handleCancelSchedule = async (scheduleId) => {
    try {
      await fetch(`${API_BASE}/schedules/${scheduleId}`, { method: "DELETE" });
      setSchedules(prev => prev.filter(s => s.schedule_id !== scheduleId));
    } catch {
      setToast("Failed to cancel schedule");
    }
  };

  const handleDismissMissed = async () => {
    try {
      await fetch(`${API_BASE}/schedules/missed`, { method: "DELETE" });
      setMissedSchedules([]);
    } catch { }
  };

  const handleReschedule = (entry) => {
    // Pre-fill the form with the missed schedule's data
    setScheduleUrl(entry.url || "");
    setSchedulePath(entry.output_path || "C:/Burst-Downloads/");
    setScheduleRepeat(entry.repeat || "once");
    // Default to tomorrow same time
    const tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    setScheduleDate(tomorrow.toISOString().split("T")[0]);
    const origTime = entry.scheduled_time ? entry.scheduled_time.split("T")[1]?.slice(0, 5) : "";
    setScheduleTime(origTime || "09:00");
    setActiveTab("schedule");
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
                    <span style={{ width: '20px', height: '20px', display: 'inline-flex', flexShrink: 0 }}>
                      {CHROME_SVG}
                    </span>
                    Add to Chrome
                  </button>
                  <button
                    className="extension-btn firefox"
                    onClick={() => window.open('https://addons.mozilla.org/firefox/addon/burst-download-manager', '_blank')}
                  >
                    <span style={{ width: '20px', height: '20px', display: 'inline-flex', flexShrink: 0 }}>
                      {FIREFOX_SVG}
                    </span>
                    Add to Firefox
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
            <button
              className={`nav-item ${activeTab === 'schedule' ? 'active' : ''}`}
              onClick={() => {
                setActiveTab('schedule');
                fetch(`${API_BASE}/schedules`).then(r => r.json()).then(d => setSchedules(d.schedules || [])).catch(() => {});
              }}
              title="Schedule"
              style={{ position: 'relative' }}
            >
              <Clock size={16} />
              {!isSidebarCollapsed && "Schedule"}
              {missedSchedules.length > 0 && (
                <span style={{
                  position: 'absolute',
                  top: '6px',
                  right: isSidebarCollapsed ? '6px' : '10px',
                  width: '7px',
                  height: '7px',
                  borderRadius: '50%',
                  background: 'var(--warning)',
                  flexShrink: 0,
                }} />
              )}
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
              {interfaces.length <= 1 && appSettings?.ONBOARDING_COMPLETE && !bannerDismissed && (
                <div className="interface-hint-bar" style={{
                  background: 'var(--surface)',
                  borderBottom: '1px solid var(--border)',
                  padding: '10px 16px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '10px',
                  fontSize: '13px',
                  color: 'var(--text-muted)',
                  animation: 'slideIn 0.2s ease',
                  width: '100%',
                  boxSizing: 'border-box'
                }}>
                  <Zap size={14} style={{ color: 'var(--accent)', flexShrink: 0 }} />
                  <span>Connect a second interface (Ethernet, hotspot) to start bonding speeds</span>
                  <a
                    className="hint-link"
                    onClick={() => setActiveTab('connections')}
                    style={{ color: 'var(--accent)', cursor: 'pointer', textDecoration: 'none', marginLeft: '4px', fontWeight: 500 }}
                  >
                    View Connections →
                  </a>
                  <button
                    className="close-btn"
                    onClick={() => {
                      setBannerDismissed(true);
                      localStorage.setItem("burst_banner_dismissed", "true");
                    }}
                    style={{
                      background: 'transparent',
                      border: 'none',
                      color: 'var(--text-muted)',
                      cursor: 'pointer',
                      padding: '4px',
                      marginLeft: 'auto',
                      display: 'grid',
                      placeItems: 'center',
                      borderRadius: '4px',
                      opacity: 0.6
                    }}
                  >
                    <X size={14} />
                  </button>
                </div>
              )}

              {showBatchResultsView ? (
                /* OPTION B - FULL SCREEN SCAN RESULTS */
                <div className="content-body" style={{ display: 'flex', flexDirection: 'column', gap: '16px', height: '100%', boxSizing: 'border-box', animation: 'slideIn 0.2s ease' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <button
                      onClick={() => { setShowBatchResultsView(false); setBatchResults([]); setBatchError(null); }}
                      style={{
                        background: 'transparent',
                        border: 'none',
                        color: 'var(--text)',
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        padding: '4px',
                        borderRadius: '4px',
                        transition: 'background 0.2s'
                      }}
                      title="Go Back"
                      className="action-btn"
                    >
                      <ArrowLeft size={18} />
                    </button>
                    <span className="section-label" style={{ margin: 0 }}>Webpage Scan Results</span>
                  </div>

                  <div style={{ fontSize: '12px', color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', width: '100%' }}>
                    Scanned: {url}
                  </div>

                  <div style={{
                    flex: 1,
                    display: 'flex',
                    flexDirection: 'column',
                    background: 'var(--bg)',
                    border: '1px solid var(--border)',
                    borderRadius: '6px',
                    padding: '16px',
                    boxSizing: 'border-box',
                    minHeight: 0
                  }}>
                    {batchScanning && (
                      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', flex: 1, color: 'var(--text-muted)', gap: '12px' }}>
                        <div className="spinner-small" style={{ width: '24px', height: '24px', borderWidth: '3px' }} />
                        <div style={{ fontSize: '13px' }}>Scanning page for downloadable files...</div>
                      </div>
                    )}

                    {batchError && !batchScanning && (
                      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', flex: 1, gap: '12px' }}>
                        <div style={{ color: 'var(--danger)', fontSize: '13px', textAlign: 'center' }}>{batchError}</div>
                        <button className="btn-secondary" onClick={() => { setShowBatchResultsView(false); setBatchResults([]); setBatchError(null); }}>
                          Go Back
                        </button>
                      </div>
                    )}

                    {!batchScanning && !batchError && batchResults.length > 0 && (() => {
                      const filteredFiles = batchResults.filter(file => {
                        const query = batchSearchQuery.toLowerCase();
                        return file.filename.toLowerCase().includes(query) || file.url.toLowerCase().includes(query);
                      });

                      return (
                        <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, width: '100%' }}>
                          {/* Filter and Select Links */}
                          <div style={{ display: 'flex', gap: '16px', alignItems: 'center', marginBottom: '12px', flexShrink: 0 }}>
                            <input
                              type="text"
                              placeholder="Filter scan results..."
                              value={batchSearchQuery}
                              onChange={(e) => setBatchSearchQuery(e.target.value)}
                              style={{
                                flex: 1,
                                background: 'var(--surface)',
                                border: '1px solid var(--border)',
                                borderRadius: '4px',
                                padding: '8px 12px',
                                fontSize: '13px',
                                outline: 'none',
                                color: 'var(--text)'
                              }}
                            />
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', color: 'var(--text-muted)' }}>
                              <button
                                onClick={() => setBatchResults(prev => prev.map(f => ({ ...f, checked: true })))}
                                style={{ background: 'transparent', border: 'none', color: 'var(--accent)', fontSize: '13px', cursor: 'pointer', padding: 0 }}
                              >
                                All
                              </button>
                              <span>·</span>
                              <button
                                onClick={() => setBatchResults(prev => prev.map(f => ({ ...f, checked: false })))}
                                style={{ background: 'transparent', border: 'none', color: 'var(--accent)', fontSize: '13px', cursor: 'pointer', padding: 0 }}
                              >
                                None
                              </button>
                            </div>
                          </div>

                          {/* List/Scroll area */}
                          <div style={{
                            flex: 1,
                            overflowY: 'auto',
                            border: '1px solid var(--border)',
                            borderRadius: '4px',
                            background: 'var(--surface)',
                            marginBottom: '16px',
                            minHeight: 0
                          }}>
                            {filteredFiles.length === 0 ? (
                              <div style={{ textAlign: 'center', padding: '32px', color: 'var(--text-muted)', fontSize: '13px' }}>
                                No files matching filter.
                              </div>
                            ) : (
                              filteredFiles.map((file, idx) => {
                                const originalIdx = batchResults.findIndex(f => f.url === file.url);
                                return (
                                  <label
                                    key={idx}
                                    style={{
                                      display: 'flex',
                                      alignItems: 'center',
                                      gap: '12px',
                                      padding: '10px 14px',
                                      borderBottom: '1px solid var(--border)',
                                      cursor: 'pointer',
                                      fontSize: '13px',
                                      transition: 'background 0.15s ease',
                                    }}
                                    className="batch-item-row"
                                  >
                                    <input
                                      type="checkbox"
                                      checked={file.checked}
                                      onChange={() => {
                                        setBatchResults(prev => prev.map((f, i) => i === originalIdx ? { ...f, checked: !f.checked } : f));
                                      }}
                                      style={{ cursor: 'pointer', accentColor: 'var(--accent)', width: '15px', height: '15px', flexShrink: 0 }}
                                    />
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                      <div style={{ fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--text)' }}>
                                        {file.filename}
                                      </div>
                                      <div style={{ fontSize: '11px', color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', marginTop: '2px' }}>
                                        {file.url}
                                      </div>
                                    </div>
                                  </label>
                                );
                              })
                            )}
                          </div>

                          {/* Footer Action Bar */}
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0 }}>
                            <span style={{ fontSize: '13px', color: 'var(--text-muted)' }}>
                              {batchResults.filter(f => f.checked).length} of {batchResults.length} selected
                            </span>
                            <div style={{ display: 'flex', gap: '10px' }}>
                              <button
                                onClick={() => { setShowBatchResultsView(false); setBatchResults([]); setBatchError(null); }}
                                className="btn-secondary"
                              >
                                Cancel
                              </button>
                              <button
                                onClick={handleBatchDownload}
                                disabled={batchResults.filter(f => f.checked).length === 0}
                                className="btn-primary"
                              >
                                Queue Downloads
                              </button>
                            </div>
                          </div>
                        </div>
                      );
                    })()}
                  </div>
                </div>
              ) : (
                /* NORMAL ACTIVE VIEW */
                <>
                  <div className="top-controls">
                    <div className="input-group" style={{ position: 'relative' }}>
                      <input
                        type="text"
                        className="url-input"
                        placeholder="Paste a URL, magnet link, or .torrent..."
                        value={url}
                        onChange={(e) => handleUrlChange(e.target.value)}
                      />
                      <button
                        className="btn-primary"
                        onClick={ytInfo ? handleYtDlpDownload : handleDownloadClick}
                        disabled={downloadBtnState === "checking" || ytLoading}
                      >
                        {(downloadBtnState === "checking" || ytLoading) ? <div className="spinner-small" /> : <Download size={18} />}
                        {(downloadBtnState === "checking" || ytLoading) ? "Checking..." : "Download"}
                      </button>
                    </div>

                    {/* yt-dlp quality picker — shown when a video URL is detected */}
                    {ytInfo && ytInfo.supported && (
                      <div style={{
                        display: 'flex', flexDirection: 'column', gap: '8px',
                        background: 'var(--surface)', border: '1px solid var(--border)',
                        borderRadius: '8px', padding: '12px', marginTop: '8px',
                        animation: 'slideIn 0.18s ease'
                      }}>
                        {/* Row 1: Details and Controls */}
                        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                          {/* Thumbnail */}
                          {ytInfo.thumbnail && (
                            <img
                              src={ytInfo.thumbnail}
                              alt="thumbnail"
                              style={{ width: '80px', height: '45px', objectFit: 'cover', borderRadius: '5px', flexShrink: 0 }}
                              onError={e => e.target.style.display = 'none'}
                            />
                          )}
                          {/* Title + duration */}
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{
                              fontSize: '13px', fontWeight: 600, color: 'var(--text)',
                              whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis'
                            }}>
                              {ytInfo.title}
                            </div>
                            {ytInfo.duration_str && (
                              <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '2px' }}>
                                {ytInfo.duration_str}
                              </div>
                            )}
                          </div>
                          {/* Format dropdown */}
                          <select
                            value={ytFormat}
                            onChange={e => {
                              const selectedFmt = ytInfo.formats.find(f => f.id === e.target.value);
                              if (selectedFmt && !selectedFmt.has_audio && !selectedFmt.progressive_url) {
                                setYtStreamable(false); // disable streamable if user selects a DASH format
                              }
                              setYtFormat(e.target.value);
                            }}
                            style={{
                              background: 'var(--surface-2)', border: '1px solid var(--border)',
                              borderRadius: '6px', color: 'var(--text)', fontSize: '12px',
                              padding: '5px 8px', cursor: 'pointer', flexShrink: 0,
                              outline: 'none',
                            }}
                          >
                            {ytInfo.formats.map(f => {
                              const isStreamable = f.has_audio || !!f.progressive_url;
                              if (ytStreamable && !isStreamable) return null; // hide non-streamable formats
                              return (
                                <option key={f.id} value={f.id}>
                                  {f.label === 'Audio only' ? `♫ Audio only` : `▶ ${f.label}`}
                                </option>
                              );
                            })}
                          </select>
                          {/* Download */}
                          <button
                            className="btn-primary"
                            onClick={handleYtDlpDownload}
                            style={{ flexShrink: 0, padding: '7px 16px', fontSize: '13px' }}
                          >
                            <Download size={14} /> Download
                          </button>
                          {/* Dismiss */}
                          <button
                            onClick={() => { setYtInfo(null); ytCheckedUrlRef.current = ""; }}
                            style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', padding: '4px', flexShrink: 0 }}
                            title="Dismiss"
                          >
                            <X size={14} />
                          </button>
                        </div>
                        
                        {/* Row 2: Streamable Options */}
                        <div style={{
                          display: 'flex', alignItems: 'center', gap: '8px', 
                          borderTop: '1px solid var(--border)', paddingTop: '8px',
                          marginTop: '4px'
                        }}>
                          <label style={{
                            display: 'flex', alignItems: 'center', gap: '6px',
                            cursor: 'pointer', fontSize: '11px', color: 'var(--text-muted)',
                            userSelect: 'none'
                          }}>
                            <input
                              type="checkbox"
                              checked={ytStreamable}
                              onChange={e => {
                                const checked = e.target.checked;
                                setYtStreamable(checked);
                                if (checked) {
                                  // Switch to a progressive/combined format that has audio (720p or lower combined format)
                                  const progFormat = ytInfo.formats.find(f => (f.has_audio || !!f.progressive_url) && f.label !== 'Audio only');
                                  if (progFormat) {
                                    setYtFormat(progFormat.id);
                                  }
                                }
                              }}
                              style={{ accentColor: 'var(--accent)' }}
                            />
                            <span>Watch while downloading (Play instantly during download — 720p max)</span>
                          </label>
                        </div>
                      </div>
                    )}

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

                    {urlTypeHint && urlTypeHint.type === "html_page" && !ytInfo && (
                      <div style={{
                        marginTop: '10px',
                        padding: '10px 12px',
                        background: 'var(--surface)',
                        border: '1px solid var(--border)',
                        borderRadius: '6px',
                        fontSize: '13px',
                        animation: 'slideIn 0.2s ease',
                        width: '100%',
                        boxSizing: 'border-box'
                      }}>
                        <div style={{ marginBottom: '8px', color: 'var(--text)', fontSize: '13px', fontWeight: 500 }}>
                          This looks like a webpage. Scan for downloadable files?
                        </div>
                        <div style={{ display: 'flex', gap: '8px' }}>
                          <button className="btn-primary-small" style={{ flex: 1 }} onClick={handleBatchScan}>
                            Scan for files
                          </button>
                          <button className="btn-secondary" style={{ flex: 1 }} onClick={() => {
                            setUrlTypeHint(null);
                            startDownload();
                          }}>
                            Download page directly
                          </button>
                          <button className="btn-secondary" style={{ flex: 0, padding: '0 8px' }} onClick={() => setUrlTypeHint(null)} title="Dismiss">
                            <X size={14} />
                          </button>
                        </div>
                      </div>
                    )}
                  </div>

                  <div className="content-body">
                    {activeJobs.length > 0 && <div className="section-label">Active Downloads</div>}
                    {activeJobs.length === 0 && (
                      <div className="empty-state slide-in" style={{ padding: '40px 20px' }}>
                        <div className="empty-icon-wrapper">
                          <Inbox size={32} strokeWidth={1.5} />
                        </div>
                        <h3>No active downloads</h3>
                        <p>Paste a link above to start your first speed-bonded download.</p>
                      </div>
                    )}
                    {activeJobs.map(jid => {
                      const isFirstCard = jid === activeJobs[0];
                      let showHint = false;
                      if (isFirstCard && !hasShownGraphHintRef.current) {
                        showHint = true;
                        hasShownGraphHintRef.current = true;
                      }
                      return (
                        <DownloadCard
                          key={jid}
                          jid={jid}
                          status={jobStatuses[jid]}
                          availableInterfaces={renderedInterfaces}
                          allUsedIps={allUsedIps}
                          isExpanded={!!expandedGraphs[jid]}
                          onToggleExpand={() => setExpandedGraphs(prev => ({ ...prev, [jid]: !prev[jid] }))}
                          showHint={showHint}
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
                      );
                    })}

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
                    <div key={item.id ?? Math.random()} style={{ display: 'flex', flexDirection: 'column' }}>
                      <div className="completed-row" style={{ position: 'relative' }}>
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
                        
                        {item.status !== 'failed' && (
                          <button
                            onClick={() => {
                              setChecksumValue("");
                              setVerificationResult(null);
                              setVerificationError(null);
                              setVerificationLoading(false);
                              setActiveVerifyJobId(prev => prev === item.id ? null : item.id);
                            }}
                            style={{
                              background: 'none',
                              border: 'none',
                              cursor: 'pointer',
                              color: activeVerifyJobId === item.id ? 'var(--accent)' : 'var(--text-muted)',
                              padding: '6px',
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              alignSelf: 'center',
                              transition: 'color 0.2s ease',
                              marginLeft: '12px'
                            }}
                            title="Verify Checksum"
                          >
                            <Shield size={18} />
                          </button>
                        )}
                      </div>

                      {activeVerifyJobId === item.id && (
                        <div className="slide-in" style={{
                          background: 'var(--surface-2)',
                          border: '1px solid var(--border)',
                          borderTop: 'none',
                          borderRadius: '0 0 10px 10px',
                          padding: '16px',
                          marginTop: '-13px',
                          marginBottom: '12px',
                          fontSize: '13px',
                          boxSizing: 'border-box'
                        }}>
                          <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                            <input
                              type="text"
                              placeholder="Paste MD5, SHA1, or SHA256 hash"
                              value={checksumValue}
                              onChange={(e) => {
                                setChecksumValue(e.target.value);
                                setVerificationError(null);
                                setVerificationResult(null);
                              }}
                              disabled={verificationLoading}
                              style={{
                                flex: 1,
                                background: 'var(--bg)',
                                border: '1px solid var(--border)',
                                borderRadius: '6px',
                                color: 'var(--text)',
                                padding: '8px 12px',
                                fontSize: '13px',
                                outline: 'none',
                              }}
                            />
                            <button
                              className="btn-secondary"
                              onClick={() => handleVerify(item)}
                              disabled={verificationLoading || !checksumValue.trim()}
                              style={{
                                height: '34px',
                                padding: '0 16px',
                                borderRadius: '6px',
                                fontSize: '12px',
                                display: 'flex',
                                alignItems: 'center',
                                gap: '6px',
                                flexShrink: 0
                              }}
                            >
                              {verificationLoading && (
                                <div className="spinner-small" style={{ width: '12px', height: '12px', borderWidth: '2px' }} />
                              )}
                              Verify
                            </button>
                          </div>

                          {verificationError && (
                            <div style={{ color: 'var(--danger)', marginTop: '10px', display: 'flex', alignItems: 'center', gap: '6px', fontWeight: 500 }}>
                              <AlertCircle size={14} />
                              {verificationError}
                            </div>
                          )}

                          {verificationResult && (
                            <div style={{ marginTop: '12px' }}>
                              {verificationResult.match ? (
                                <div style={{ color: 'var(--success)', display: 'flex', alignItems: 'center', gap: '6px', fontWeight: 500 }}>
                                  <CheckCircle2 size={14} />
                                  Hash matches — file is intact ({verificationResult.algorithm.toUpperCase()})
                                </div>
                              ) : (
                                <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                                  <div style={{ color: 'var(--danger)', display: 'flex', alignItems: 'center', gap: '6px', fontWeight: 500 }}>
                                    <AlertTriangle size={14} />
                                    Hash mismatch — file may be corrupted
                                  </div>
                                  <div style={{
                                    fontSize: '11px',
                                    color: 'var(--text-muted)',
                                    background: 'var(--bg)',
                                    padding: '8px 10px',
                                    borderRadius: '4px',
                                    border: '1px solid var(--border)',
                                    fontFamily: '"JetBrains Mono", monospace',
                                    wordBreak: 'break-all',
                                    marginTop: '4px'
                                  }}>
                                    <span style={{ fontWeight: 600 }}>Actual hash:</span> {verificationResult.actual}
                                  </div>
                                  <div style={{ display: 'flex', gap: '10px', marginTop: '6px' }}>
                                    <button
                                      className="btn-secondary"
                                      onClick={() => handleReDownload(item)}
                                      style={{
                                        height: '32px',
                                        padding: '0 12px',
                                        fontSize: '12px',
                                        borderRadius: '6px'
                                      }}
                                    >
                                      Re-download
                                    </button>
                                    <button
                                      className="btn-secondary"
                                      onClick={() => handleDeleteFile(item)}
                                      style={{
                                        height: '32px',
                                        padding: '0 12px',
                                        fontSize: '12px',
                                        borderRadius: '6px',
                                        color: 'var(--danger)',
                                        borderColor: 'rgba(220, 38, 38, 0.2)'
                                      }}
                                    >
                                      Delete file
                                    </button>
                                  </div>
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          )}

          {activeTab === 'schedule' && (
            <div className="schedule-page">

              {/* Missed schedules banner */}
              {missedSchedules.length > 0 && (
                <div className="schedule-missed-banner">
                  <div className="schedule-missed-banner-header">
                    <span>⚠ {missedSchedules.length} missed schedule{missedSchedules.length > 1 ? 's' : ''} while app was closed</span>
                    <button className="schedule-missed-dismiss" onClick={handleDismissMissed}>Dismiss</button>
                  </div>
                  {missedSchedules.map(entry => (
                    <div className="schedule-missed-item" key={entry.schedule_id}>
                      <Clock size={11} />
                      <span>{extractScheduleName(entry.url)}</span>
                      <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>— was {formatScheduleTime(entry.scheduled_time)}</span>
                      <button className="schedule-missed-reschedule" onClick={() => handleReschedule(entry)}>Reschedule</button>
                    </div>
                  ))}
                </div>
              )}

              {/* Add Scheduled Download */}
              <div className="schedule-section">
                <div className="schedule-section-label">Add Scheduled Download</div>
                <div className="schedule-form">
                  <input
                    id="schedule-url-input"
                    type="text"
                    className="schedule-input"
                    placeholder="https://example.com/file.zip or magnet:?xt=…"
                    value={scheduleUrl}
                    onChange={e => { setScheduleUrl(e.target.value); setScheduleError(null); }}
                  />

                  <div className="sched-row" style={{ alignItems: 'stretch' }}>
                    <input
                      id="schedule-path-input"
                      type="text"
                      className="schedule-input"
                      placeholder="Save path (e.g. C:/Burst-Downloads/)"
                      value={schedulePath}
                      onChange={e => setSchedulePath(e.target.value)}
                      style={{ flex: 1 }}
                    />
                    <button
                      className="browse-btn-icon"
                      onClick={async () => {
                        try {
                          const r = await fetch(`${API_BASE}/select-path`);
                          const d = await r.json();
                          if (d.path) setSchedulePath(d.path + "/");
                        } catch { }
                      }}
                      title="Pick folder"
                    >
                      <FolderOpen size={16} />
                    </button>
                  </div>

                  <SchedulePicker
                    date={scheduleDate}
                    time={scheduleTime}
                    onDateChange={(d) => { setScheduleDate(d); setScheduleError(null); }}
                    onTimeChange={(t) => { setScheduleTime(t); setScheduleError(null); }}
                    error={scheduleError}
                    onErrorClear={() => setScheduleError(null)}
                  />

                  <button
                    id="schedule-submit-btn"
                    className="schedule-btn"
                    onClick={handleScheduleSubmit}
                    disabled={scheduleLoading}
                    style={{ width: '100%', justifyContent: 'center' }}
                  >
                    {scheduleLoading
                      ? <div className="spinner-small" style={{ width: '13px', height: '13px', borderWidth: '2px' }} />
                      : <Clock size={14} />
                    }
                    Schedule Download
                  </button>

                  {scheduleError && (
                    <div className="schedule-error">
                      <AlertCircle size={13} />
                      {scheduleError}
                    </div>
                  )}
                </div>
              </div>

              <div className="schedule-divider" />

              {/* Upcoming list */}
              <div className="schedule-section" style={{ paddingBottom: '6px' }}>
                <div className="schedule-section-label">Upcoming</div>
              </div>
              <div className="schedule-list">
                {schedules.length === 0 ? (
                  <div className="schedule-empty">
                    <Clock size={32} strokeWidth={1.2} style={{ opacity: 0.3 }} />
                    <span>No downloads scheduled</span>
                  </div>
                ) : (
                  schedules.map(entry => (
                    <div className="schedule-row" key={entry.schedule_id}>
                      <div className="schedule-row-icon">
                        <Clock size={16} />
                      </div>
                      <div className="schedule-row-body">
                        <div className="schedule-row-name">{extractScheduleName(entry.url)}</div>
                        <div className="schedule-row-time">{formatScheduleTime(entry.scheduled_time)}</div>
                      </div>
                      <span className={`schedule-badge ${entry.repeat}`}>
                        {entry.repeat === 'once' ? 'Once' : entry.repeat === 'daily' ? 'Daily' : 'Weekly'}
                      </span>
                      <button
                        className="schedule-cancel-btn"
                        onClick={() => handleCancelSchedule(entry.schedule_id)}
                        title="Cancel schedule"
                      >
                        <X size={15} />
                      </button>
                    </div>
                  ))
                )}
              </div>
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

                  {/* CLIPBOARD MONITOR TOGGLE */}
                  <label className="setting-row" style={{ alignItems: 'flex-start' }}>
                    <div>
                      <span>Clipboard Monitor</span>
                      <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '2px' }}>Auto-detect URLs copied to clipboard</div>
                    </div>
                    <div className="setting-input-wrap" style={{ flexDirection: 'column', alignItems: 'flex-end', gap: '4px' }}>
                      <div
                        onClick={async () => {
                          const newVal = !appSettings?.CLIPBOARD_MONITOR_ENABLED;
                          setAppSettings(prev => ({ ...prev, CLIPBOARD_MONITOR_ENABLED: newVal }));
                          const resp = await fetch(`${API_BASE}/settings/clipboard-monitor?enabled=${newVal}`, {
                            method: 'POST'
                          });
                          const data = await resp.json();
                          if (!data.supported && data.reason !== "pywin32_not_installed") {
                            setToast("Clipboard monitor is Windows-only");
                            setAppSettings(prev => ({ ...prev, CLIPBOARD_MONITOR_ENABLED: false }));
                          } else if (data.reason === "pywin32_not_installed") {
                            setAppSettings(prev => ({
                              ...prev,
                              CLIPBOARD_MONITOR_ENABLED: newVal,
                              CLIPBOARD_MONITOR_REASON: "pywin32_not_installed"
                            }));
                          } else {
                            setAppSettings(prev => ({
                              ...prev,
                              CLIPBOARD_MONITOR_ENABLED: newVal,
                              CLIPBOARD_MONITOR_REASON: null
                            }));
                          }
                        }}
                        style={{
                          width: '40px', height: '22px', borderRadius: '11px', cursor: 'pointer',
                          background: appSettings?.CLIPBOARD_MONITOR_ENABLED ? 'var(--accent)' : 'var(--border)',
                          position: 'relative', transition: 'background 0.2s', flexShrink: 0
                        }}
                      >
                        <div style={{
                          position: 'absolute', top: '3px',
                          left: appSettings?.CLIPBOARD_MONITOR_ENABLED ? '21px' : '3px',
                          width: '16px', height: '16px', borderRadius: '50%',
                          background: '#fff', transition: 'left 0.2s',
                          boxShadow: '0 1px 3px rgba(0,0,0,0.3)'
                        }} />
                      </div>
                      {appSettings?.CLIPBOARD_MONITOR_REASON === "pywin32_not_installed" && (
                        <span style={{ fontSize: '10px', color: 'var(--warning, #eab308)', display: 'block', marginTop: '4px', textAlign: 'right' }}>
                          Run pip install pywin32 then restart Burst
                        </span>
                      )}
                      {!appSettings?.CLIPBOARD_MONITOR_ENABLED && appSettings?.CLIPBOARD_MONITOR_REASON !== "pywin32_not_installed" && (
                        <span style={{ fontSize: '10px', color: 'var(--text-muted)' }}>Windows only</span>
                      )}
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

      {/* ffmpeg auto-download progress toast */}
      {ffmpegToast && (
        <div style={{
          position: 'fixed',
          bottom: toast ? '70px' : '24px',
          right: '24px',
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: '10px',
          padding: '12px 16px',
          fontSize: '13px',
          zIndex: 9998,
          minWidth: '260px',
          maxWidth: '320px',
          boxShadow: '0 8px 24px rgba(0,0,0,0.18)',
          animation: 'slideIn 0.2s ease',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: ffmpegToast.status === 'downloading' ? '8px' : '0' }}>
            {ffmpegToast.status === 'downloading' && <Loader size={13} className="spin-anim" style={{ color: 'var(--accent)', flexShrink: 0 }} />}
            {ffmpegToast.status === 'done' && <CheckCircle2 size={13} style={{ color: '#22c55e', flexShrink: 0 }} />}
            {ffmpegToast.status === 'error' && <AlertTriangle size={13} style={{ color: '#ef4444', flexShrink: 0 }} />}
            <span style={{ color: 'var(--text)', fontWeight: 500 }}>
              {ffmpegToast.status === 'downloading' && `Downloading ffmpeg… ${ffmpegToast.percent}%`}
              {ffmpegToast.status === 'done' && 'ffmpeg ready ✓'}
              {ffmpegToast.status === 'error' && `ffmpeg failed: ${ffmpegToast.error}`}
            </span>
          </div>
          {ffmpegToast.status === 'downloading' && (
            <div style={{ height: '3px', background: 'var(--border)', borderRadius: '2px', overflow: 'hidden' }}>
              <div style={{
                height: '100%',
                width: `${ffmpegToast.percent}%`,
                background: 'var(--accent)',
                borderRadius: '2px',
                transition: 'width 0.3s ease',
              }} />
            </div>
          )}
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
