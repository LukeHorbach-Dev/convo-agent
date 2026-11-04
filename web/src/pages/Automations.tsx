import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Clock, ShieldCheck, Workflow, Zap } from "lucide-react";
import { useState } from "react";
import { Badge, Card, Dot, ErrorState, Input, PageHeader, Segmented, Skeleton, Toggle, useToast } from "../components/ui";
import { api, post } from "../lib/api";
import { ago } from "../lib/format";

export default function Automations() {
  const toast = useToast();
  const qc = useQueryClient();
  const [show, setShow] = useState<"all" | "live" | "planned">("all");
  const { data, isLoading, error, refetch } = useQuery({ queryKey: ["automations"], queryFn: () => api("/api/hub/automations") });
  const save = useMutation({ mutationFn: (body: any) => post("/api/settings", body), onSuccess: () => { toast("Saved."); qc.invalidateQueries(); },
    onError: (e) => toast((e as Error).message, "bad") });
  if (error) return <ErrorState error={error} retry={refetch} />;
  const items: any[] = (data?.items || []).filter((x: any) => show === "all" || x.status === show);
  const toggle = (a: any, on: boolean) => {
    if (a.id === "overnight") save.mutate({ write_overnight: on });
    if (a.id === "send_at") save.mutate({ send_at: on ? (a.config.send_at || "10:00") : "" });
    if (a.id === "quiet") save.mutate(on ? { quiet_from: a.config.quiet_from || "23:00", quiet_to: a.config.quiet_to || "08:00" } : { quiet_from: "", quiet_to: "" });
  };
  return (
    <div>
      <PageHeader title="Automation Centre" subtitle="Everything Convo can do by itself, in one place. Each one says whether it needs your approval."
        actions={<Segmented value={show} onChange={setShow} options={[["all", "All"], ["live", "Working now"], ["planned", "Coming"]]} />} />
      {isLoading ? <Skeleton className="h-96" /> : (
        <div className="grid gap-3 lg:grid-cols-2">{items.map((a) => (
          <Card key={a.id}>
            <div className="flex items-start gap-3">
              <span className={`grid size-9 shrink-0 place-items-center rounded-xl ${a.status === "live" ? "bg-violet-soft text-violet-2" : "bg-hover text-faint"}`}>
                {a.status === "live" ? <Zap className="size-4" /> : <Workflow className="size-4" />}</span>
              <div className="min-w-0 grow">
                <div className="flex flex-wrap items-center gap-2"><span className="text-[15px] font-semibold">{a.name}</span>
                  {a.needs_approval ? <Badge tone="cyan" icon={ShieldCheck}>Your approval</Badge> : <Badge tone="neutral">Automatic</Badge>}
                  {a.status === "planned" && <Badge tone="violet">Stage {a.stage}</Badge>}</div>
                <p className="mt-1 text-[13.5px] text-muted">{a.description}</p>
              </div>
              {a.status === "live" && !a.always_on && <Toggle checked={a.enabled} onChange={(v) => toggle(a, v)} label={a.name} disabled={save.isPending} />}
              {a.always_on && <Badge tone="good">Always on</Badge>}
            </div>
            {a.status === "live" && (
              <div className="mt-4 flex flex-col gap-3 border-t border-line pt-3">
                {a.id === "send_at" && a.enabled && <TimeRow label="Time" value={a.config.send_at} onSave={(v) => save.mutate({ send_at: v })} />}
                {a.id === "quiet" && a.enabled && <div className="flex flex-wrap gap-3">
                  <TimeRow label="From" value={a.config.quiet_from} onSave={(v) => save.mutate({ quiet_from: v })} />
                  <TimeRow label="To" value={a.config.quiet_to} onSave={(v) => save.mutate({ quiet_to: v })} /></div>}
                {a.id === "budget" && <BudgetRow value={a.config.daily_ai_budget_usd} onSave={(v) => save.mutate({ daily_ai_budget_usd: v })} />}
                <div className="flex flex-wrap gap-x-5 gap-y-1 text-[12.5px] text-muted">
                  <span className="flex items-center gap-1.5"><Dot tone={a.enabled ? "good" : "neutral"} />{a.enabled ? "On" : "Off"}</span>
                  <span>Last: {a.last_run ? `${ago(a.last_run.at)} · ${a.last_run.text.slice(0, 70)}${a.last_run.text.length > 70 ? "…" : ""}` : "not yet"}</span>
                  {a.next_run && <span className="flex items-center gap-1"><Clock className="size-3.5" />Next: {new Date(a.next_run).toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" })}</span>}
                </div>
              </div>
            )}
          </Card>))}</div>
      )}
      <p className="mt-6 text-[12.5px] text-faint">Coming automations switch on here as their stage ships. None of them will say they work until the platform permissions and keys they need are set up.</p>
    </div>
  );
}

function TimeRow({ label, value, onSave }: { label: string; value: string | null; onSave: (v: string) => void }) {
  const [v, setV] = useState(value || "");
  return <label className="flex items-center gap-2 text-[13px] text-ink-2">{label}
    <Input type="time" value={v} onChange={(e) => setV(e.target.value)} onBlur={() => v && v !== value && onSave(v)} className="h-9 w-32" /></label>;
}

function BudgetRow({ value, onSave }: { value: number; onSave: (v: number) => void }) {
  const [v, setV] = useState(String(value));
  return <label className="flex items-center gap-2 text-[13px] text-ink-2">Limit per day ($)
    <Input type="number" min={0.5} max={100} step={0.5} value={v} onChange={(e) => setV(e.target.value)}
      onBlur={() => Number(v) !== value && onSave(Number(v))} className="h-9 w-28" /></label>;
}
