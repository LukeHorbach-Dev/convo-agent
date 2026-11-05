// Follow-ups: who's waiting on you, hellos nobody answered, and your own reminders. Nothing here sends anything.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellRing, Check, Clock, Hand, MessageCircleReply, Plus } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Avatar, Badge, Button, Card, Empty, ErrorState, Field, Input, MessageText, Modal, Note, PageHeader, Segmented, Skeleton, Textarea, useToast } from "../components/ui";
import { api, del, post } from "../lib/api";
import { ago } from "../lib/format";

export type Followup = { key: string; kind: "waiting" | "no_reply" | "reminder"; id?: number; username: string | null; name: string | null;
  thread_id?: string; since: string; days: number; text: string | null; has_draft?: boolean };
type Data = { items: Followup[]; upcoming: Followup[]; counts: Record<string, number>; total: number; unknown_hellos: number; as_of: string | null;
  waiting_days: number; no_reply_days: number };

const KIND: Record<Followup["kind"], { label: string; icon: any; tone: "violet" | "cyan" | "warn" }> = {
  reminder: { label: "Reminder", icon: BellRing, tone: "warn" },
  waiting: { label: "Waiting on you", icon: MessageCircleReply, tone: "violet" },
  no_reply: { label: "No reply to your hello", icon: Hand, tone: "cyan" },
};

export function useFollowups() {
  return useQuery({ queryKey: ["followups"], queryFn: () => api<Data>("/api/hub/followups"), refetchInterval: 60000 });
}

export function useFollowupActions() {
  const qc = useQueryClient();
  const toast = useToast();
  const done = () => qc.invalidateQueries({ queryKey: ["followups"] });
  const act = async (f: Followup, what: "snooze" | "done") => {
    try { await post(`/api/hub/followups/${encodeURIComponent(f.key)}/${what}`, what === "snooze" ? { days: 3 } : {}); }
    catch (e) { return toast((e as Error).message, "bad"); }
    done();
    toast(what === "snooze" ? "Hidden for 3 days." : "Marked done.", "good",
      { label: "Undo", run: async () => { await post(`/api/hub/followups/${encodeURIComponent(f.key)}/undo`).catch(() => {}); done(); } });
  };
  return act;
}

export function FollowupRow({ f, compact }: { f: Followup; compact?: boolean }) {
  const nav = useNavigate();
  const act = useFollowupActions();
  const k = KIND[f.kind];
  const when = f.kind === "reminder" ? `Due ${ago(f.since)}` : f.kind === "waiting" ? `They wrote ${ago(f.since)}` : `Hello sent ${ago(f.since)}`;
  return (
    <div className="flex items-start gap-3 rounded-xl border border-line bg-bg-2 px-3.5 py-3">
      {f.username ? <Avatar name={f.name || f.username} size={compact ? 32 : 38} /> : <span className="grid size-8 place-items-center rounded-full bg-warn-soft"><k.icon className="size-4 text-warn" /></span>}
      <div className="min-w-0 grow">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="truncate text-[14px] font-medium text-ink">{f.name || (f.username ? `@${f.username}` : "Reminder")}</span>
          {!compact && <Badge tone={k.tone} icon={k.icon}>{k.label}</Badge>}
          <span className="text-[12px] text-faint">{when}</span>
        </div>
        {f.text && <p className="mt-0.5 line-clamp-2 text-[13px] text-muted">{f.kind === "no_reply" ? "You said: " : f.kind === "waiting" ? "" : ""}<MessageText text={f.text} /></p>}
        <div className="mt-2 flex flex-wrap gap-1.5">
          {f.kind === "waiting" && (f.has_draft
            ? <Button size="sm" variant="primary" onClick={() => nav("/review/replies")}>A reply is ready: check it</Button>
            : <Button size="sm" variant="secondary" onClick={() => nav("/conversations")}>Open chat</Button>)}
          {f.kind !== "waiting" && f.username && <Button size="sm" variant="secondary" onClick={() => nav(`/contacts?u=${encodeURIComponent(f.username!)}`)}>See them</Button>}
          <Button size="sm" variant="ghost" icon={Clock} onClick={() => act(f, "snooze")}>Later</Button>
          <Button size="sm" variant="ghost" icon={Check} onClick={() => act(f, "done")}>Done</Button>
        </div>
      </div>
    </div>
  );
}

export function ReminderForm({ username, open, onClose }: { username?: string | null; open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const tomorrow = new Date(Date.now() + 86400000).toISOString().slice(0, 10);
  const [user, setUser] = useState(username || "");
  const [note, setNote] = useState("");
  const [date, setDate] = useState(tomorrow);
  const m = useMutation({
    mutationFn: () => post("/api/hub/reminders", { username: username || user || null, note, due_at: date }),
    onSuccess: () => { toast("Reminder set. Convo tells you on the day (9am)."); qc.invalidateQueries({ queryKey: ["followups"] }); setNote(""); onClose(); },
    onError: (e) => toast((e as Error).message, "bad"),
  });
  return (
    <Modal open={open} onClose={onClose} title={username ? `Remind me about @${username}` : "New reminder"}
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" loading={m.isPending} disabled={!note.trim() || !date} onClick={() => m.mutate()}>Set reminder</Button></>}>
      <div className="grid gap-3">
        {!username && <Field label="About someone? (optional)" htmlFor="r-user"><Input id="r-user" value={user} onChange={(e) => setUser(e.target.value)} placeholder="@username" /></Field>}
        <Field label="What to remind you" htmlFor="r-note"><Textarea id="r-note" rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Ask how the show went" autoFocus /></Field>
        <Field label="When" htmlFor="r-date"><Input id="r-date" type="date" value={date} min={new Date().toISOString().slice(0, 10)} onChange={(e) => setDate(e.target.value)} /></Field>
      </div>
    </Modal>
  );
}

export default function Followups() {
  const { data, isLoading, error, refetch } = useFollowups();
  const [show, setShow] = useState<"all" | Followup["kind"]>("all");
  const [adding, setAdding] = useState(false);
  const qc = useQueryClient();
  if (error) return <ErrorState error={error} retry={refetch} />;
  const all = (data?.items || []).filter((f) => show === "all" || f.kind === show);
  // On "All", only the first few unanswered hellos: there can be hundreds, and they matter least.
  const capped = show === "all" ? all.filter((f, i, xs) => f.kind !== "no_reply" || xs.slice(0, i).filter((x) => x.kind === "no_reply").length < 5) : all;
  const hiddenNoReply = all.length - capped.length;
  const items = capped;
  return (
    <div>
      <PageHeader title="Follow-ups" subtitle="People waiting on you, hellos nobody answered, and reminders you set. Nothing here sends anything: each one is for you to decide."
        actions={<Button variant="primary" icon={Plus} onClick={() => setAdding(true)}>New reminder</Button>} />
      {isLoading || !data ? <Skeleton className="h-80" /> : (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
          <Card bodyClass="!p-0">
            <div className="border-b border-line p-4">
              <Segmented value={show} onChange={setShow} options={[["all", `All (${data.total})`], ["reminder", `Reminders (${data.counts.reminder})`],
                ["waiting", `Waiting on you (${data.counts.waiting})`], ["no_reply", `No reply (${data.counts.no_reply})`]]} />
            </div>
            <div className="flex flex-col gap-2 p-4">
              {items.length ? items.map((f) => <FollowupRow key={f.key} f={f} />)
                : <Empty icon={Check} title="Nothing to follow up">You're on top of everything{show === "all" ? "" : " here"}.</Empty>}
              {hiddenNoReply > 0 && <Button variant="ghost" className="self-start" onClick={() => setShow("no_reply")}>See all {data!.counts.no_reply.toLocaleString()} hellos with no reply</Button>}
            </div>
          </Card>
          <div className="grid content-start gap-4">
            <Card title="Coming up" icon={BellRing}>
              {data.upcoming.length ? <div className="flex flex-col gap-2">{data.upcoming.map((r) => (
                <div key={r.key} className="flex items-start gap-2 text-[13px]">
                  <span className="min-w-0 grow"><span className="text-ink">{r.text}</span>
                    <span className="block text-[12px] text-faint">{new Date(r.since).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })}{r.username ? ` · @${r.username}` : ""}</span></span>
                  <button className="text-[12px] text-faint hover:text-bad" onClick={async () => { await del(`/api/hub/reminders/${r.id}`); qc.invalidateQueries({ queryKey: ["followups"] }); }}>Remove</button>
                </div>))}</div>
                : <p className="text-[13px] text-muted">No reminders set. Use New reminder, or "Remind me" on anyone in Contacts.</p>}
            </Card>
            <Note>Worked out from your Instagram downloads{data.as_of ? ` (newest: ${ago(data.as_of)})` : ""}. "Waiting on you" means they wrote last over {data.waiting_days} days ago;
              "No reply" means your hello went {data.no_reply_days}+ days ago with no answer.
              {data.unknown_hellos > 0 && ` ${data.unknown_hellos.toLocaleString()} hellos went out after your newest download: load a new one to see who answered.`}</Note>
          </div>
        </div>
      )}
      <ReminderForm open={adding} onClose={() => setAdding(false)} />
    </div>
  );
}
