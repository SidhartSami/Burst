import {
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";

const COLORS = ["#3b82f6", "#22c55e", "#eab308", "#f97316", "#a855f7", "#06b6d4"];

export default function SpeedGraph({ points, interfaceIps }) {
  if (!points?.length) return null;
  return (
    <div className="rounded-xl border border-zinc-800 bg-panel p-4">
      <h2 className="mb-3 text-sm font-semibold">Live Speed</h2>
      <div className="h-72 w-full">
        <ResponsiveContainer>
          <LineChart data={points}>
            <XAxis dataKey="t" stroke="#71717a" />
            <YAxis stroke="#71717a" />
            <Tooltip />
            <Legend />
            <Line type="monotone" dataKey="combined" stroke="#ffffff" dot={false} />
            {interfaceIps.map((ip, idx) => (
              <Line
                key={ip}
                type="monotone"
                dataKey={ip}
                stroke={COLORS[idx % COLORS.length]}
                dot={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
