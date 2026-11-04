// Charts. Dark, quiet axes, violet and cyan series, and a tooltip that says exactly what each number is.
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { dayLabel } from "../lib/format";

export const SERIES = { violet: "#8b5cf6", cyan: "#22d3ee", good: "#34d399", warn: "#fbbf24", muted: "#5f6675" };

function Tip({ active, payload, label, names }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-xl border border-line-2 bg-raised px-3 py-2 text-[12.5px] shadow-[var(--shadow-pop)]">
      <div className="mb-1 text-faint">{typeof label === "string" && /^\d{4}-\d{2}-\d{2}/.test(label) ? dayLabel(label.slice(0, 10)) : label}</div>
      {payload.map((p: any) => (
        <div key={p.dataKey} className="flex items-center gap-2 text-ink-2">
          <span className="size-2 rounded-full" style={{ background: p.color }} />
          <span className="grow">{names?.[p.dataKey] || p.dataKey}</span><b className="tabular text-ink">{Number(p.value).toLocaleString()}</b>
        </div>
      ))}
    </div>
  );
}

export function AreaSeries({ data, keys, names, height = 240, stacked }: {
  data: any[]; keys: { key: string; color: string }[]; names: Record<string, string>; height?: number; stacked?: boolean;
}) {
  return (
    <div style={{ height }} className="w-full min-w-0">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
          <defs>{keys.map((k) => (
            <linearGradient key={k.key} id={`g-${k.key}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={k.color} stopOpacity={0.35} /><stop offset="100%" stopColor={k.color} stopOpacity={0} />
            </linearGradient>))}
          </defs>
          <CartesianGrid vertical={false} strokeDasharray="3 6" />
          <XAxis dataKey="date" tickLine={false} axisLine={false} tickFormatter={(d) => dayLabel(String(d).slice(0, 10))} minTickGap={28} />
          <YAxis tickLine={false} axisLine={false} allowDecimals={false} width={44} />
          <Tooltip content={<Tip names={names} />} cursor={{ stroke: "#2e3340" }} />
          {keys.map((k) => (
            <Area key={k.key} type="monotone" dataKey={k.key} stackId={stacked ? "s" : undefined} stroke={k.color} strokeWidth={2}
              fill={`url(#g-${k.key})`} dot={false} activeDot={{ r: 4, strokeWidth: 0 }} isAnimationActive={false} />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export function Bars({ data, keys, names, height = 220, x = "date" }: {
  data: any[]; keys: { key: string; color: string }[]; names: Record<string, string>; height?: number; x?: string;
}) {
  return (
    <div style={{ height }} className="w-full min-w-0">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
          <CartesianGrid vertical={false} strokeDasharray="3 6" />
          <XAxis dataKey={x} tickLine={false} axisLine={false} minTickGap={20}
            tickFormatter={(d) => (x === "date" ? dayLabel(String(d).slice(0, 10)) : d)} />
          <YAxis tickLine={false} axisLine={false} allowDecimals={false} width={44} />
          <Tooltip content={<Tip names={names} />} cursor={{ fill: "rgb(255 255 255 / 0.03)" }} />
          {keys.map((k) => <Bar key={k.key} dataKey={k.key} stackId="s" fill={k.color} radius={[4, 4, 0, 0]} isAnimationActive={false} />)}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function Spark({ values, color = SERIES.violet }: { values: number[]; color?: string }) {
  if (values.length < 2) return null;
  const max = Math.max(...values, 1), w = 96, h = 28;
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * w},${h - (v / max) * (h - 4) - 2}`).join(" ");
  return <svg width={w} height={h} aria-hidden className="overflow-visible"><polyline points={pts} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" /></svg>;
}
