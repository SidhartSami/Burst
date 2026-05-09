export default function InterfaceCard({ iface, selected, onToggle }) {
  const active = selected.includes(iface.ip_address);
  return (
    <div
      className={`rounded-xl border p-4 transition ${
        active ? "border-success bg-panel" : "border-zinc-800 bg-panel/80"
      }`}
    >
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-zinc-100">{iface.name}</h3>
        <span className="rounded-full bg-accent/20 px-2 py-1 text-xs text-accent">
          {iface.interface_type}
        </span>
      </div>
      <p className="text-xs text-zinc-400">IP: {iface.ip_address}</p>
      <p className="mt-1 text-xs text-zinc-400">
        Speed: {Number(iface.speed_mb_s || 0).toFixed(2)} MB/s
      </p>

      <label className="mt-4 flex cursor-pointer items-center justify-between text-xs">
        <span className={active ? "text-success" : "text-zinc-400"}>
          {active ? "Selected" : "Excluded"}
        </span>
        <input
          type="checkbox"
          className="h-4 w-4 accent-accent"
          checked={active}
          onChange={() => onToggle(iface.ip_address)}
        />
      </label>
    </div>
  );
}
