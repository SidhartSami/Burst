function percent(value, total) {
  if (!total) return 0;
  return Math.min(100, (value / total) * 100);
}

export default function DownloadProgress({ status }) {
  if (!status) return null;
  const ifaceValues = Object.values(status.interfaces || {});
  const overallPct = percent(status.total_downloaded || 0, status.expected_size || 0);

  return (
    <div className="rounded-xl border border-zinc-800 bg-panel p-4">
      <h2 className="mb-3 text-sm font-semibold">Progress</h2>
      <div className="mb-4">
        <div className="mb-1 flex justify-between text-xs text-zinc-400">
          <span>Overall</span>
          <span>{overallPct.toFixed(1)}%</span>
        </div>
        <div className="h-2 w-full rounded bg-zinc-800">
          <div className="h-2 rounded bg-accent" style={{ width: `${overallPct}%` }} />
        </div>
      </div>

      <div className="space-y-3">
        {ifaceValues.map((item) => {
          const size = item.chunk_end - item.chunk_start + 1;
          const pct = percent(item.downloaded, size);
          return (
            <div key={`${item.ip_address}-${item.chunk_start}`}>
              <div className="mb-1 flex justify-between text-xs text-zinc-400">
                <span>
                  {item.name} ({item.ip_address}) - {item.status}
                </span>
                <span>
                  {pct.toFixed(1)}% | {Number(item.speed_mb_s || 0).toFixed(2)} MB/s
                </span>
              </div>
              <div className="h-2 w-full rounded bg-zinc-800">
                <div className="h-2 rounded bg-success" style={{ width: `${pct}%` }} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
