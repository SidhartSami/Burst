import React, { useState, useRef, useEffect } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

const MONTHS = [
  "January","February","March","April","May","June",
  "July","August","September","October","November","December"
];
const DOW = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];

function getDaysInMonth(year, month) {
  return new Date(year, month + 1, 0).getDate();
}
function getFirstDayOfMonth(year, month) {
  return new Date(year, month, 1).getDay();
}

/* ─── Calendar popover ─── */
function CalendarPopover({ date, onSelect, onClose }) {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const initDate = date ? new Date(date + "T00:00:00") : today;
  const [viewYear, setViewYear] = useState(initDate.getFullYear());
  const [viewMonth, setViewMonth] = useState(initDate.getMonth());

  const buildGrid = () => {
    const daysInMonth = getDaysInMonth(viewYear, viewMonth);
    const firstDay = getFirstDayOfMonth(viewYear, viewMonth);
    const days = [];
    const prevDays = getDaysInMonth(viewYear, viewMonth - 1);
    for (let i = firstDay - 1; i >= 0; i--)
      days.push({ day: prevDays - i, offset: -1 });
    for (let d = 1; d <= daysInMonth; d++)
      days.push({ day: d, offset: 0 });
    const remaining = 42 - days.length;
    for (let d = 1; d <= remaining; d++)
      days.push({ day: d, offset: 1 });
    return days;
  };

  const getDateStr = (day, offset) => {
    let y = viewYear, m = viewMonth + offset;
    if (m < 0)  { m = 11; y--; }
    if (m > 11) { m = 0;  y++; }
    return `${y}-${String(m + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
  };

  const isPast = (day, offset) => new Date(getDateStr(day, offset) + "T00:00:00") < today;

  const prevMonth = () => {
    let m = viewMonth - 1, y = viewYear;
    if (m < 0) { m = 11; y--; }
    setViewMonth(m); setViewYear(y);
  };
  const nextMonth = () => {
    let m = viewMonth + 1, y = viewYear;
    if (m > 11) { m = 0; y++; }
    setViewMonth(m); setViewYear(y);
  };

  const grid = buildGrid();

  return (
    <div className="sched-cal-pop">
      <div className="sched-cal-head">
        <button className="sched-cal-nav" onClick={prevMonth}><ChevronLeft size={14} /></button>
        <span className="sched-cal-title">{MONTHS[viewMonth]} {viewYear}</span>
        <button className="sched-cal-nav" onClick={nextMonth}><ChevronRight size={14} /></button>
      </div>
      <div className="sched-cal-grid">
        {DOW.map(d => <div key={d} className="sched-cal-dow">{d}</div>)}
        {grid.map((item, i) => {
          const ds = getDateStr(item.day, item.offset);
          const past = isPast(item.day, item.offset);
          const selected = ds === date;
          const isToday = ds === new Date().toISOString().split("T")[0];
          return (
            <button
              key={i}
              className={[
                "sched-cal-day",
                item.offset !== 0 ? "other-month" : "",
                past ? "past" : "",
                selected ? "selected" : "",
                isToday && !selected ? "today" : "",
              ].filter(Boolean).join(" ")}
              onClick={() => { if (!past) { onSelect(ds); onClose(); } }}
              disabled={past}
            >
              {item.day}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/* ─── Chevron SVG (tiny up/down arrows) ─── */
const Up   = () => <svg width="9" height="6" viewBox="0 0 9 6" fill="currentColor"><path d="M4.5 0L9 6H0z"/></svg>;
const Down = () => <svg width="9" height="6" viewBox="0 0 9 6" fill="currentColor"><path d="M4.5 6L0 0h9z"/></svg>;

/* ─── Single time unit (HH or MM) ─── */
function TimeUnit({ value, min, max, onChange }) {
  const pad = (n) => String(n).padStart(2, "0");
  const num = Math.min(max, Math.max(min, parseInt(value) || 0));

  const inc = () => onChange(num >= max ? min : num + 1);
  const dec = () => onChange(num <= min ? max : num - 1);

  return (
    <div className="sched-tunit">
      <button className="sched-tarrow" onClick={inc} tabIndex={-1}><Up /></button>
      <input
        className="sched-tnum"
        type="number"
        min={min} max={max}
        value={pad(num)}
        onChange={e => onChange(Math.min(max, Math.max(min, parseInt(e.target.value) || 0)))}
      />
      <button className="sched-tarrow" onClick={dec} tabIndex={-1}><Down /></button>
    </div>
  );
}

/* ─── Main SchedulePicker ─── */
export default function SchedulePicker({ date, time, onDateChange, onTimeChange, error, onErrorClear }) {
  const [calOpen, setCalOpen] = useState(false);
  const wrapRef = useRef(null);

  useEffect(() => {
    const handler = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target))
        setCalOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const formatDate = () => {
    if (!date) return "Pick a date";
    try {
      const d = new Date(date + "T00:00:00");
      return d.toLocaleDateString("en-US", { weekday: "short", month: "long", day: "numeric", year: "numeric" });
    } catch { return date; }
  };

  const [hStr, mStr] = (time || "09:00").split(":");
  const hNum = Math.min(23, Math.max(0, parseInt(hStr) || 0));
  const mNum = Math.min(59, Math.max(0, parseInt(mStr) || 0));
  const pad  = (n) => String(n).padStart(2, "0");

  const emitH = (h) => { onTimeChange(`${pad(h)}:${pad(mNum)}`); if (onErrorClear) onErrorClear(); };
  const emitM = (m) => { onTimeChange(`${pad(hNum)}:${pad(m)}`); if (onErrorClear) onErrorClear(); };

  return (
    /* Outer row — date flex-grows, time stays fixed width, both stretch to same height */
    <div ref={wrapRef} className="sched-row">

      {/* ── Date button ── */}
      <div style={{ flex: 1, position: "relative" }}>
        <button
          className={`sched-date-btn${error ? " has-error" : ""}`}
          onClick={() => { setCalOpen(v => !v); if (onErrorClear) onErrorClear(); }}
        >
          {/* calendar icon */}
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
            <rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>
          </svg>
          <span className="sched-date-label">{formatDate()}</span>
          {/* chevron */}
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="var(--text-muted)" strokeWidth="2" style={{ flexShrink: 0 }}>
            <polyline points={calOpen ? "18 15 12 9 6 15" : "6 9 12 15 18 9"} />
          </svg>
        </button>

        {calOpen && (
          <CalendarPopover
            date={date}
            onSelect={(d) => { onDateChange(d); if (onErrorClear) onErrorClear(); onErrorClear && onErrorClear(); }}
            onClose={() => setCalOpen(false)}
          />
        )}
      </div>

      {/* ── Time picker — same height as date button, side by side ── */}
      <div className={`sched-time-wrap${error ? " has-error" : ""}`}>
        <TimeUnit value={hNum} min={0} max={23} onChange={emitH} />
        <span className="sched-tcolon">:</span>
        <TimeUnit value={mNum} min={0} max={59} onChange={emitM} />
        <span className="sched-tampm">{hNum < 12 ? "AM" : "PM"}</span>
      </div>

    </div>
  );
}
