// Publishing Calendar: what goes out when, for every account. Drag a post to another day to move it; drag one of
// the ready posts on the right onto a day to schedule it (at 6pm, change it in the Studio).
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarDays, ChevronLeft, ChevronRight, Plus } from "lucide-react";
import { DragEvent, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Badge, Button, Card, cx, Empty, ErrorState, IconButton, PageHeader, Segmented, Skeleton, useToast } from "../components/ui";
import { api, patch } from "../lib/api";
import { Content, PLATFORM, STATUS } from "../lib/content";

const DAY = 86400000;
const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
const key = (d: Date) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
const weekStart = (d: Date) => { const s = startOfDay(d); const dow = (s.getDay() + 6) % 7; return new Date(s.getTime() - dow * DAY); };   // Monday

export default function Calendar() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const [view, setView] = useState<"month" | "week">(() => (window.innerWidth < 768 ? "week" : "month"));
  const [anchor, setAnchor] = useState(() => startOfDay(new Date()));
  const days = useMemo(() => {
    if (view === "week") { const s = weekStart(anchor); return [...Array(7)].map((_, i) => new Date(s.getTime() + i * DAY)); }
    const first = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
    const s = weekStart(first);
    return [...Array(42)].map((_, i) => new Date(s.getFullYear(), s.getMonth(), s.getDate() + i));
  }, [view, anchor]);
  const start = days[0], end = new Date(days[days.length - 1].getTime() + DAY);
  const { data, isLoading, error, refetch } = useQuery({ queryKey: ["calendar", start.toISOString(), end.toISOString()],
    queryFn: () => api<{ items: Content[]; unscheduled: Content[] }>(`/api/hub/calendar?start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(end.toISOString())}`) });
  const byDay = useMemo(() => {
    const m = new Map<string, Content[]>();
    for (const c of data?.items || []) { const at = c.scheduled_at || c.posted_at; if (!at) continue; const k = key(new Date(at)); m.set(k, [...(m.get(k) || []), c]); }
    return m;
  }, [data]);
  const move = async (id: number, day: Date, keepTime: string | null) => {
    const t = keepTime ? new Date(keepTime) : null;
    const at = new Date(day.getFullYear(), day.getMonth(), day.getDate(), t ? t.getHours() : 18, t ? t.getMinutes() : 0);
    try { await patch(`/api/hub/content/${id}`, { scheduled_at: at.toISOString() }); qc.invalidateQueries({ queryKey: ["calendar"] }); qc.invalidateQueries({ queryKey: ["content"] });
      toast(`Scheduled for ${at.toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" })}.`); }
    catch (e) { toast((e as Error).message, "bad"); }
  };
  const onDrop = (day: Date) => (e: DragEvent) => {
    e.preventDefault();
    const raw = e.dataTransfer.getData("text/convo-post");
    if (!raw) return;
    const { id, at } = JSON.parse(raw);
    if (day < startOfDay(new Date())) return toast("That day has gone. Pick today or later.", "bad");
    move(id, day, at);
  };
  const shift = (n: number) => setAnchor((a) => view === "week" ? new Date(a.getTime() + n * 7 * DAY) : new Date(a.getFullYear(), a.getMonth() + n, 1));
  const today = key(new Date());
  const title = view === "week" ? `${days[0].toLocaleDateString(undefined, { day: "numeric", month: "short" })} – ${days[6].toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}`
    : anchor.toLocaleDateString(undefined, { month: "long", year: "numeric" });

  return (
    <div>
      <PageHeader title="Publishing Calendar" subtitle="What goes out when, on every account. Drag posts between days." actions={<Button variant="primary" icon={Plus} onClick={() => nav("/studio")}>New post</Button>} />
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_280px]">
        <Card bodyClass="!p-0">
          <div className="flex flex-wrap items-center gap-2 border-b border-line p-4">
            <IconButton icon={ChevronLeft} label="Earlier" onClick={() => shift(-1)} />
            <IconButton icon={ChevronRight} label="Later" onClick={() => shift(1)} />
            <Button size="sm" variant="ghost" onClick={() => setAnchor(startOfDay(new Date()))}>Today</Button>
            <h2 className="ml-1 grow text-[15px] font-semibold">{title}</h2>
            <Segmented value={view} onChange={setView} options={[["month", "Month"], ["week", "Week"]]} />
          </div>
          {error ? <div className="p-4"><ErrorState error={error} retry={refetch} /></div> : isLoading ? <div className="p-4"><Skeleton className="h-[480px]" /></div> : (<>
            <div className="grid grid-cols-7 border-b border-line text-center text-[11.5px] font-medium uppercase tracking-wide text-faint max-md:hidden">
              {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => <div key={d} className="py-2">{d}</div>)}</div>
            <div className={cx("grid", view === "week" ? "max-md:grid-cols-1 md:grid-cols-7" : "grid-cols-7")}>
              {days.map((d) => {
                const items = byDay.get(key(d)) || [];
                const other = view === "month" && d.getMonth() !== anchor.getMonth();
                return (
                  <div key={key(d)} onDragOver={(e) => e.preventDefault()} onDrop={onDrop(d)}
                    className={cx("min-h-[96px] border-b border-r border-line p-1.5 transition-colors", view === "week" && "md:min-h-[360px]", other && "bg-bg-2/40")}>
                    <div className={cx("mb-1 flex items-center gap-1.5 px-1 text-[12px]", key(d) === today ? "font-semibold text-violet-2" : other ? "text-faint/60" : "text-muted")}>
                      {key(d) === today ? <span className="grid size-5 place-items-center rounded-full bg-violet text-[11px] text-white">{d.getDate()}</span> : d.getDate()}
                      {view === "week" && <span className="md:hidden">{d.toLocaleDateString(undefined, { weekday: "long" })}</span>}
                    </div>
                    <div className="flex flex-col gap-1">{items.map((c) => <Chip key={c.id} c={c} compact={view === "month"} />)}</div>
                  </div>);
              })}
            </div>
          </>)}
        </Card>
        <Card title="Ready to schedule" icon={CalendarDays} subtitle="Drag one onto a day">
          {data?.unscheduled.length ? <div className="flex flex-col gap-1.5">{data.unscheduled.map((c) => <Chip key={c.id} c={c} />)}</div>
            : <Empty icon={CalendarDays} title="Nothing waiting">Posts you mark ready in the Studio show up here.</Empty>}
        </Card>
      </div>
    </div>
  );
}

function Chip({ c, compact }: { c: Content; compact?: boolean }) {
  const nav = useNavigate();
  const color = c.platforms[0] ? PLATFORM[c.platforms[0]].color : "#8b5cf6";
  const at = c.scheduled_at || c.posted_at;
  const draggable = c.status !== "posted";
  return (
    <button draggable={draggable} onDragStart={(e) => e.dataTransfer.setData("text/convo-post", JSON.stringify({ id: c.id, at: c.scheduled_at }))}
      onClick={() => nav(`/studio/${c.id}`)} title={`${c.title || "Untitled"} · ${at ? new Date(at).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }) + " · " : ""}${STATUS[c.status].label}`}
      className={cx("flex w-full items-center gap-1.5 rounded-lg border border-line bg-raised px-1.5 py-1 text-left text-[12px] hover:border-line-2", draggable && "cursor-grab active:cursor-grabbing",
        c.status === "posted" && "opacity-60", c.status === "due" && "border-warn/50")}>
      <span className="h-3.5 w-1 shrink-0 rounded-full" style={{ background: color }} />
      {at && !compact && <span className="shrink-0 tabular text-faint">{new Date(at).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}</span>}
      <span className="min-w-0 truncate text-ink-2">{c.title || "Untitled"}</span>
      {!compact && <span className="ml-auto shrink-0"><Badge tone={STATUS[c.status].tone}>{STATUS[c.status].label}</Badge></span>}
    </button>
  );
}
