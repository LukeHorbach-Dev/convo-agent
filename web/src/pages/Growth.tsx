// Stage 6: weekly missions with XP, growth goals, the Organic Independence Score, collaborations and comment replies.
// Every number comes from real activity; nothing is estimated.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Award, Check, CheckCircle2, ExternalLink, Flame, Handshake, MessageCircleReply, Plus, RefreshCw, SkipForward, Sparkles, Sprout, Star, Target, Trash2, Trophy,
} from "lucide-react";
import { useState } from "react";
import { Avatar, Badge, Button, Card, cx, Empty, ErrorState, Field, Input, Modal, Note, PageHeader, Segmented, Select, Skeleton, Textarea, useConfirm, useToast } from "../components/ui";
import { api, del, patch, post } from "../lib/api";
import { PLATFORM } from "../lib/content";
import { ago, num } from "../lib/format";
import { useAppState } from "../lib/state";
import { PersonDrawer } from "./Contacts";

const Bar = ({ value, tone = "violet" }: { value: number; tone?: "violet" | "good" }) => (
  <div className="h-2 overflow-hidden rounded-full bg-bg-2"><div className={cx("h-full rounded-full transition-[width]", tone === "good" ? "bg-good" : "bg-gradient-to-r from-violet to-cyan")}
    style={{ width: `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%` }} /></div>
);

// ------------------------------------------------------------------------------------------------ missions
export function WeeklyMissions() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data: m, isLoading, error, refetch } = useQuery({ queryKey: ["missions"], queryFn: () => api("/api/hub/missions"), refetchInterval: 60000 });
  const [adding, setAdding] = useState(false);
  if (error) return <ErrorState error={error} retry={refetch} />;
  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Weekly Missions" subtitle="Goals for this week, filled in by themselves from what really happened. They reward real conversations and posting, never sending more messages."
        actions={<Button variant="primary" icon={Plus} onClick={() => setAdding(true)}>Your own mission</Button>} />
      {isLoading || !m ? <Skeleton className="h-96" /> : (<>
        <div className="grid gap-3 sm:grid-cols-3">
          <Card><div className="flex items-center gap-3"><span className="grid size-11 place-items-center rounded-2xl bg-violet-soft text-violet-2"><Trophy className="size-5" /></span>
            <div className="grow"><div className="text-[12.5px] text-muted">Level</div><div className="text-[22px] font-semibold">{m.level}</div></div>
            <div className="text-right text-[12.5px] text-muted">{num(m.xp_total)} XP</div></div><div className="mt-3"><Bar value={m.level_progress} /></div></Card>
          <Card><div className="flex items-center gap-3"><span className="grid size-11 place-items-center rounded-2xl bg-warn-soft text-warn"><Flame className="size-5" /></span>
            <div><div className="text-[12.5px] text-muted">Streak</div><div className="text-[22px] font-semibold">{m.streak} {m.streak === 1 ? "week" : "weeks"}</div></div></div>
            <p className="mt-2 text-[12px] text-faint">Weeks in a row with 3 or more missions done.</p></Card>
          <Card><div className="flex items-center gap-3"><span className="grid size-11 place-items-center rounded-2xl bg-good-soft text-good"><CheckCircle2 className="size-5" /></span>
            <div><div className="text-[12.5px] text-muted">This week</div><div className="text-[22px] font-semibold">{m.done} of {m.missions.length}</div></div></div>
            <p className="mt-2 text-[12px] text-faint">{num(m.xp_week)} XP so far. Resets on Monday.</p></Card>
        </div>
        <Card title="This week's missions" icon={Target}>
          <div className="grid gap-3">{m.missions.map((x: any) => (
            <div key={x.key} className={cx("rounded-xl border px-4 py-3", x.done ? "border-good/30 bg-good-soft/40" : "border-line bg-bg-2")}>
              <div className="flex items-center gap-3">
                {x.done ? <CheckCircle2 className="size-5 shrink-0 text-good" /> : <Target className="size-5 shrink-0 text-faint" />}
                <span className="grow text-[14px] font-medium">{x.title}</span>
                <span className="text-[12.5px] tabular text-muted">{x.metric === "waiting" ? `${x.value} waiting` : `${x.value} / ${x.target}`}</span>
                <Badge tone={x.done ? "good" : "neutral"}>+{x.xp} XP</Badge>
                {x.custom && <button aria-label="Remove mission" className="text-faint hover:text-bad" onClick={async () => { await del(`/api/hub/missions/${x.key.split(":")[1]}`); qc.invalidateQueries({ queryKey: ["missions"] }); toast("Removed."); }}><Trash2 className="size-4" /></button>}
              </div>
              {!x.done && x.metric !== "waiting" && <div className="mt-2.5 pl-8"><Bar value={x.progress} /></div>}
            </div>))}</div>
          <p className="mt-3 text-[12px] text-faint">Conversations and replies come from your Instagram downloads, so they're as fresh as your newest download.</p>
        </Card>
        <div className="grid gap-5 lg:grid-cols-2">
          <Card title="Badges" icon={Award}>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">{m.badges.map((b: any) => (
              <div key={b.id} className={cx("rounded-xl border p-3 text-center", b.earned ? "border-violet/40 bg-violet-soft" : "border-line bg-bg-2 opacity-60")} title={b.how}>
                <Award className={cx("mx-auto size-6", b.earned ? "text-violet-2" : "text-faint")} />
                <div className="mt-1.5 text-[13px] font-medium">{b.name}</div><div className="text-[11.5px] text-muted">{b.earned ? "Earned" : b.how}</div></div>))}</div>
          </Card>
          <Card title="Past weeks" icon={Trophy}>
            {m.history.length > 1 ? <div className="grid gap-2">{m.history.map((h: any) => (
              <div key={h.week} className="flex items-center gap-3 text-[13px]"><span className="w-24 shrink-0 text-muted">{new Date(h.week).toLocaleDateString(undefined, { day: "numeric", month: "short" })}</span>
                <span className="grow"><Bar value={h.done / Math.max(h.n, 1)} tone={h.done >= 3 ? "good" : "violet"} /></span><span className="w-20 text-right tabular text-muted">{h.done}/{h.n} · {h.xp} XP</span></div>))}</div>
              : <p className="text-[13px] text-muted">Your weeks show up here as they finish.</p>}
          </Card>
        </div>
        <MissionForm open={adding} onClose={() => setAdding(false)} metrics={m.metrics} />
      </>)}
    </div>
  );
}

function MissionForm({ open, onClose, metrics }: { open: boolean; onClose: () => void; metrics: Record<string, string> }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [title, setTitle] = useState("");
  const [metric, setMetric] = useState("conversations");
  const [target, setTarget] = useState("5");
  const add = useMutation({ mutationFn: () => post("/api/hub/missions", { title, metric, target: Number(target) }),
    onSuccess: () => { toast("Mission added."); qc.invalidateQueries({ queryKey: ["missions"] }); setTitle(""); onClose(); }, onError: (e) => toast((e as Error).message, "bad") });
  return (
    <Modal open={open} onClose={onClose} title="Your own mission" footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button>
      <Button variant="primary" loading={add.isPending} disabled={!title.trim() || !Number(target)} onClick={() => add.mutate()}>Add</Button></>}>
      <div className="grid gap-3">
        <Field label="Name it" htmlFor="m-title"><Input id="m-title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Talk to 10 new people" /></Field>
        <Field label="What counts" htmlFor="m-metric"><Select id="m-metric" value={metric} onChange={(e) => setMetric(e.target.value)}>
          {Object.entries(metrics).filter(([k]) => k !== "waiting").map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select></Field>
        <Field label="How many this week" htmlFor="m-target"><Input id="m-target" type="number" min={1} max={1000} value={target} onChange={(e) => setTarget(e.target.value)} /></Field>
      </div>
    </Modal>
  );
}

// ------------------------------------------------------------------------------------------------ goals
const GOAL_TONE: Record<string, "good" | "warn" | "bad" | "neutral" | "violet"> = { reached: "good", on_track: "good", behind: "warn", missed: "bad", too_early: "neutral", going: "violet", no_data: "neutral" };
const GOAL_LABEL: Record<string, string> = { reached: "Reached", on_track: "On track", behind: "Behind", missed: "Missed", too_early: "Too early to tell", going: "In progress", no_data: "No numbers yet" };

export function GrowthGoals() {
  const qc = useQueryClient();
  const { data, isLoading, error, refetch } = useQuery({ queryKey: ["goals"], queryFn: () => api("/api/hub/goals") });
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: () => api("/api/hub/accounts") });
  const [adding, setAdding] = useState(false);
  if (error) return <ErrorState error={error} retry={refetch} />;
  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Growth Goals" subtitle="Longer targets, measured from real numbers. Convo only says on track or behind once there's enough to judge."
        actions={<Button variant="primary" icon={Plus} onClick={() => setAdding(true)}>New goal</Button>} />
      {isLoading || !data ? <Skeleton className="h-64" /> : data.items.length ? (
        <div className="grid gap-3 lg:grid-cols-2">{data.items.map((g: any) => (
          <Card key={g.id}>
            <div className="flex items-start gap-3"><Target className="mt-0.5 size-5 text-violet-2" />
              <div className="min-w-0 grow"><div className="text-[15px] font-semibold">{g.title}</div>
                <div className="text-[12.5px] text-muted">{data.metrics[g.metric]}{g.due ? ` · by ${new Date(g.due).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}` : ""}</div></div>
              <Badge tone={GOAL_TONE[g.status]}>{GOAL_LABEL[g.status]}</Badge></div>
            <div className="mt-4 flex items-baseline justify-between text-[13px]"><span className="text-[22px] font-semibold tabular">{g.current == null ? "—" : num(g.current)}</span>
              <span className="text-muted">of {num(g.target)}</span></div>
            {g.progress != null && <div className="mt-2"><Bar value={g.progress} tone={g.status === "reached" ? "good" : "violet"} /></div>}
            <p className="mt-2 text-[12.5px] text-muted">{g.why}</p>
            <button className="mt-2 text-[12px] text-faint hover:text-bad" onClick={async () => { await del(`/api/hub/goals/${g.id}`); qc.invalidateQueries({ queryKey: ["goals"] }); }}>Remove</button>
          </Card>))}</div>
      ) : <Card><Empty icon={Target} title="No goals yet" action={<Button variant="primary" icon={Plus} onClick={() => setAdding(true)}>Set your first goal</Button>}>
          For example: 1,000 followers on a launch account by March, or 2 posts a week.</Empty></Card>}
      <GoalForm open={adding} onClose={() => setAdding(false)} metrics={data?.metrics || {}} accounts={accounts.data?.items || []} />
    </div>
  );
}

function GoalForm({ open, onClose, metrics, accounts }: { open: boolean; onClose: () => void; metrics: Record<string, string>; accounts: any[] }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [f, setF] = useState({ title: "", metric: "followers", account_id: "", target: "", due: "" });
  const add = useMutation({ mutationFn: () => post("/api/hub/goals", { ...f, target: Number(f.target), account_id: Number(f.account_id) || null, due: f.due || null }),
    onSuccess: () => { toast("Goal set."); qc.invalidateQueries({ queryKey: ["goals"] }); onClose(); }, onError: (e) => toast((e as Error).message, "bad") });
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  return (
    <Modal open={open} onClose={onClose} title="New goal" footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button>
      <Button variant="primary" loading={add.isPending} disabled={!Number(f.target) || (f.metric === "followers" && !f.account_id)} onClick={() => add.mutate()}>Set goal</Button></>}>
      <div className="grid gap-3">
        <Field label="What to measure" htmlFor="g-metric"><Select id="g-metric" value={f.metric} onChange={set("metric")}>{Object.entries(metrics).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select></Field>
        {f.metric === "followers" && <Field label="Which account" htmlFor="g-acct"><Select id="g-acct" value={f.account_id} onChange={set("account_id")}><option value="">Choose…</option>
          {accounts.map((a) => <option key={a.id} value={a.id}>@{a.handle} ({a.platform_name})</option>)}</Select></Field>}
        <Field label="Target" htmlFor="g-target"><Input id="g-target" type="number" min={1} value={f.target} onChange={set("target")} /></Field>
        <Field label="By (optional)" htmlFor="g-due"><Input id="g-due" type="date" value={f.due} onChange={set("due")} /></Field>
        <Field label="Name (optional)" htmlFor="g-title"><Input id="g-title" value={f.title} onChange={set("title")} placeholder="1k on TikTok" /></Field>
      </div>
    </Modal>
  );
}

// ------------------------------------------------------------------------------------------------ independence
export function OrganicIndependence() {
  const { data, isLoading, error, refetch } = useQuery({ queryKey: ["independence"], queryFn: () => api("/api/hub/independence") });
  if (error) return <ErrorState error={error} retry={refetch} />;
  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Organic Independence" subtitle="How self-sustaining your main brand is becoming, so you know when to lean on the launch network less." />
      {isLoading || !data ? <Skeleton className="h-80" /> : (<>
        <div className="grid gap-5 lg:grid-cols-[320px_minmax(0,1fr)]">
          <Card>
            <div className="flex flex-col items-center py-2 text-center">
              <Ring value={data.score} />
              <div className="mt-3 text-[13px] text-muted">{data.score == null ? "Not enough measured yet" : `From ${data.measured} measured part${data.measured === 1 ? "" : "s"}`}</div>
            </div>
            <Note className="mt-3">{data.advice}</Note>
          </Card>
          <div className="grid content-start gap-3 sm:grid-cols-2">{data.parts.map((p: any) => (
            <Card key={p.id}>
              <div className="flex items-center justify-between gap-2"><span className="text-[14px] font-semibold">{p.name}</span>
                {p.score == null ? <Badge>Not measured</Badge> : <span className="text-[20px] font-semibold tabular">{p.score}</span>}</div>
              {p.score != null && <div className="mt-2"><Bar value={p.score / 100} /></div>}
              <p className="mt-2 text-[12.5px] text-ink-2">{p.detail}</p><p className="mt-1 text-[11.5px] text-faint">{p.source}</p>
            </Card>))}</div>
        </div>
        <Card title="How it's worked out" icon={Sprout}><p className="text-[13.5px] text-ink-2">Each part is a 0 to 100 score from numbers a platform or your own data really gives.
          The overall score is their average, and only appears when at least two parts could be measured, so a missing number never looks like a bad one.</p></Card>
      </>)}
    </div>
  );
}

function Ring({ value }: { value: number | null }) {
  const r = 52, c = 2 * Math.PI * r, v = value ?? 0;
  return (
    <svg viewBox="0 0 128 128" className="size-40" role="img" aria-label={value == null ? "No score yet" : `Score ${value} of 100`}>
      <circle cx="64" cy="64" r={r} fill="none" stroke="var(--color-bg-2)" strokeWidth="12" />
      {value != null && <circle cx="64" cy="64" r={r} fill="none" stroke="url(#ringg)" strokeWidth="12" strokeLinecap="round" strokeDasharray={`${(v / 100) * c} ${c}`} transform="rotate(-90 64 64)" />}
      <defs><linearGradient id="ringg" x1="0" x2="1"><stop offset="0" stopColor="#8b5cf6" /><stop offset="1" stopColor="#22d3ee" /></linearGradient></defs>
      <text x="64" y="72" textAnchor="middle" className="fill-ink text-[28px] font-semibold">{value ?? "—"}</text>
    </svg>
  );
}

// ------------------------------------------------------------------------------------------------ collaborations
const STAGE: Record<string, string> = { idea: "Idea", reached_out: "Reached out", talking: "Talking", agreed: "Agreed", done: "Done", not_now: "Not now" };

export function Collaborations() {
  const qc = useQueryClient();
  const toast = useToast();
  const [person, setPerson] = useState<string | null>(null);
  const [adding, setAdding] = useState<{ name: string; username?: string } | null>(null);
  const people = useQuery({ queryKey: ["people", "collab"], queryFn: () => api("/api/people?show=interesting&sort=interest&limit=30") });
  const { data, isLoading } = useQuery({ queryKey: ["collabs"], queryFn: () => api("/api/hub/collabs") });
  const move = async (id: number, stage: string) => { await patch(`/api/hub/collabs/${id}`, { stage }); qc.invalidateQueries({ queryKey: ["collabs"] }); toast(`Moved to ${STAGE[stage]}.`); };
  const items: any[] = data?.items || [];
  const tracked = new Set(items.map((i) => i.username));
  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Collaboration Opportunities" subtitle="Ideas for working with people, and where each one is up to." actions={<Button variant="primary" icon={Plus} onClick={() => setAdding({ name: "" })}>Add one</Button>} />
      <Card title="Your collaborations" icon={Handshake} bodyClass="!p-0">
        {isLoading ? <div className="p-5"><Skeleton className="h-40" /></div> : items.length ? (
          <div className="divide-y divide-line">{items.map((c) => (
            <div key={c.id} className="flex flex-wrap items-center gap-3 px-5 py-3">
              <Avatar name={c.name} size={36} />
              <div className="min-w-0 grow"><div className="text-[14px] font-medium">{c.name}{c.username && <span className="ml-2 text-[12.5px] text-muted">@{c.username}</span>}</div>
                <div className="truncate text-[12.5px] text-muted">{c.idea || c.notes || "No idea written yet"} · {ago(c.updated_at)}</div></div>
              <Select aria-label={`Stage for ${c.name}`} value={c.stage} onChange={(e) => move(c.id, e.target.value)} className="w-40">{Object.entries(STAGE).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select>
            </div>))}</div>
        ) : <Empty icon={Handshake} title="Nothing tracked yet">Add an idea, or start from someone you'd click with below.</Empty>}
      </Card>
      <Card title="People you'd click with" icon={Star} subtitle="From your real conversations">
        {people.isLoading ? <Skeleton className="h-40" /> : people.data?.items.length ? (
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">{people.data.items.map((p: any) => (
            <div key={p.username} className="flex items-start gap-3 rounded-xl border border-line bg-bg-2 px-3 py-2.5">
              <button onClick={() => setPerson(p.username)}><Avatar name={p.display_name || p.username} size={34} /></button>
              <button onClick={() => setPerson(p.username)} className="min-w-0 grow text-left"><span className="block truncate text-[13.5px] font-medium">@{p.username}</span>
                <span className="line-clamp-2 text-[12px] text-muted">{p.interest_why || p.summary}</span></button>
              {tracked.has(p.username) ? <Badge tone="good">Tracked</Badge> : <Button size="sm" variant="ghost" icon={Plus} aria-label={`Add @${p.username}`} onClick={() => setAdding({ name: p.display_name || p.username, username: p.username })} />}
            </div>))}</div>
        ) : <Empty icon={Star} title="None yet">After replies are written, people with real common ground appear here.</Empty>}
      </Card>
      <CollabForm init={adding} onClose={() => setAdding(null)} />
      <PersonDrawer username={person} onClose={() => setPerson(null)} />
    </div>
  );
}

function CollabForm({ init, onClose }: { init: { name: string; username?: string } | null; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [idea, setIdea] = useState("");
  const [name, setName] = useState("");
  const add = useMutation({ mutationFn: () => post("/api/hub/collabs", { name: name || init?.name, username: init?.username, idea }),
    onSuccess: () => { toast("Added."); qc.invalidateQueries({ queryKey: ["collabs"] }); setIdea(""); setName(""); onClose(); }, onError: (e) => toast((e as Error).message, "bad") });
  return (
    <Modal open={!!init} onClose={onClose} title={init?.username ? `Collaborate with @${init.username}` : "New collaboration"}
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" loading={add.isPending} disabled={!(name || init?.name)} onClick={() => add.mutate()}>Add</Button></>}>
      <div className="grid gap-3">
        {!init?.username && <Field label="Who" htmlFor="co-name"><Input id="co-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Name or @username" /></Field>}
        <Field label="The idea" htmlFor="co-idea"><Textarea id="co-idea" rows={3} value={idea} onChange={(e) => setIdea(e.target.value)} placeholder="A joint video, a shout-out swap…" /></Field>
      </div>
    </Modal>
  );
}

// ------------------------------------------------------------------------------------------------ engagement
export function EngagementAssistant() {
  const qc = useQueryClient();
  const toast = useToast();
  const ai = useAppState().data?.ai.on;
  const [show, setShow] = useState<"new" | "done">("new");
  const { data, isLoading, error, refetch } = useQuery({ queryKey: ["comments", show], queryFn: () => api(`/api/hub/comments?show=${show}`) });
  const refresh = useMutation({ mutationFn: () => post("/api/hub/comments/refresh"), onSuccess: (r) => { toast(r.new ? `${r.new} new comments.` : "No new comments."); qc.invalidateQueries({ queryKey: ["comments"] }); },
    onError: (e) => toast((e as Error).message, "bad") });
  if (error) return <ErrorState error={error} retry={refetch} />;
  const none = data && !data.connected.instagram && !data.connected.youtube;
  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Engagement Assistant" subtitle="New comments on your Instagram and YouTube posts, with replies in your voice for you to check. Nothing is posted until you press Send."
        actions={!none && <Button icon={RefreshCw} loading={refresh.isPending} onClick={() => refresh.mutate()}>Check for comments</Button>} />
      {none ? <Card><Empty icon={MessageCircleReply} title="Connect Instagram or YouTube first">Comments arrive once one is connected (Integrations). TikTok's API doesn't share comments.</Empty></Card> : (
        <Card bodyClass="!p-0">
          <div className="border-b border-line p-4"><Segmented value={show} onChange={setShow} options={[["new", `To answer${data ? ` (${data.counts.new + data.counts.draft + data.counts.failed})` : ""}`], ["done", "Answered or skipped"]]} /></div>
          {isLoading || !data ? <div className="p-5"><Skeleton className="h-40" /></div> : data.items.length ? (
            <div className="divide-y divide-line">{data.items.map((c: any) => <CommentRow key={`${c.platform}:${c.comment_id}`} c={c} ai={!!ai} />)}</div>
          ) : <Empty icon={CheckCircle2} title={show === "new" ? "All answered" : "Nothing here yet"}>New comments are checked every 6 hours, or press Check for comments.</Empty>}
        </Card>)}
    </div>
  );
}

function CommentRow({ c, ai }: { c: any; ai: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const confirm = useConfirm();
  const [text, setText] = useState(c.reply_text || "");
  const base = `/api/hub/comments/${c.platform}/${encodeURIComponent(c.comment_id)}`;
  const done = () => qc.invalidateQueries({ queryKey: ["comments"] });
  const write = useMutation({ mutationFn: () => post(`${base}/write`), onSuccess: (r) => { setText(r.reply_text || ""); if (r.reply_note) toast(r.reply_note, "info"); },
    onError: (e) => toast((e as Error).message, "bad") });
  const send = async () => {
    if (!await confirm({ title: "Post this reply?", text: <>It's posted publicly under their comment on Instagram:<span className="mt-2 block rounded-lg bg-bg-2 p-3 text-ink">{text}</span></>, ok: "Post reply" })) return;
    try { await post(`${base}/send`, { text }); toast("Replied."); done(); } catch (e) { toast((e as Error).message, "bad"); }
  };
  const act = async (a: string) => { await post(`${base}/${a}`, a === "save" ? { text } : {}); done(); };
  const P = PLATFORM[c.platform as "instagram" | "youtube"];
  return (
    <div className="px-5 py-4">
      <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-muted"><span className="size-2 rounded-full" style={{ background: P.color }} />{P.name}
        <span>on</span><span className="truncate font-medium text-ink-2">{c.post_title || "your post"}</span><span>· {ago(c.at)}</span>
        {c.reply_status === "sent" && <Badge tone="good">Replied</Badge>}{c.reply_status === "skipped" && <Badge>Skipped</Badge>}{c.reply_status === "failed" && <Badge tone="bad">Didn't send</Badge>}</div>
      <p className="mt-1.5 text-[14px]"><b className="font-semibold">{c.username || "Someone"}</b> {c.text}</p>
      {c.reply_note && <Note tone="warn" className="mt-2">{c.reply_note}</Note>}
      {c.reply_status !== "sent" && c.reply_status !== "skipped" ? (<>
        <Textarea className="mt-3" rows={2} value={text} onChange={(e) => setText(e.target.value)} onBlur={() => text !== (c.reply_text || "") && act("save")} placeholder="Your reply" aria-label={`Reply to ${c.username}`} />
        <div className="mt-2 flex flex-wrap gap-2">
          <Button size="sm" icon={Sparkles} loading={write.isPending} disabled={!ai} onClick={() => write.mutate()} title={ai ? "About 1¢" : "Needs your AI key"}>{text ? "Write again" : "Write a reply"}</Button>
          {c.platform === "instagram" ? <Button size="sm" variant="primary" icon={Check} disabled={!text.trim()} onClick={send}>Send</Button>
            : <><a href={c.post_url} target="_blank" rel="noreferrer" onClick={() => text && navigator.clipboard.writeText(text).catch(() => {})}><Button size="sm" variant="primary" icon={ExternalLink}>Reply on YouTube</Button></a>
              <Button size="sm" variant="ghost" icon={Check} onClick={() => act("replied")}>I replied</Button></>}
          <Button size="sm" variant="ghost" icon={SkipForward} onClick={() => act("skip")}>Skip</Button>
        </div>
        {c.platform === "youtube" && <p className="mt-1.5 text-[11.5px] text-faint">Reply on YouTube copies your words, then opens the comment so you can paste them.</p>}
      </>) : c.reply_text ? <p className="mt-2 rounded-lg bg-bg-2 px-3 py-2 text-[13.5px] text-ink-2">You: {c.reply_text}</p>
        : <Button size="sm" variant="ghost" className="mt-2" onClick={() => act("restore")}>Answer it after all</Button>}
    </div>
  );
}
