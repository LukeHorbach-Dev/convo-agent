// Reviewing hellos and replies in the Command Centre: read, edit, approve or skip each one, or approve all the ready
// ones at once (with Undo). Nothing here sends anything: approved messages go only when sending runs.
import { keepPreviousData, useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, CheckCheck, Hand, MessagesSquare, Search, SkipForward, Star, TriangleAlert, Undo2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Avatar, Badge, Bubble, Button, Card, cx, Empty, ErrorState, Input, Note, PageHeader, Segmented, Skeleton, Textarea, useConfirm, useToast } from "../components/ui";
import { api, post } from "../lib/api";
import { ago } from "../lib/format";

type Kind = "hello" | "reply";
type Tab = "draft" | "approved" | "other" | "sent";
type Only = "" | "interesting" | "look";
type Draft = {
  id: number; kind: Kind; username: string | null; other_name: string | null; text: string; status: string; note: string | null;
  context: Record<string, any>; summary?: string | null; interest?: number | null; concern?: string | null; concern_why?: string | null;
  recent?: { from_me: number; text: string; at: number }[]; sent_at?: string | null; updated_at: string;
};
type Summary = { waiting: number; ready: number; look: number; interesting: number; approved: number; other: number; sent: number; daily_messages: number };

const PAGE = 40;
const n = (x: number | undefined) => (x ?? 0).toLocaleString();

export default function Review({ kind }: { kind: Kind }) {
  const what = kind === "hello" ? "hellos" : "replies";
  const qc = useQueryClient();
  const toast = useToast();
  const confirm = useConfirm();
  const [tab, setTab] = useState<Tab>("draft");
  const [only, setOnly] = useState<Only>("");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [gone, setGone] = useState<Set<number>>(new Set());        // acted on: hidden until the list reloads
  useEffect(() => { const t = setTimeout(() => setQuery(q.trim()), 250); return () => clearTimeout(t); }, [q]);
  useEffect(() => { setPicked(new Set()); setGone(new Set()); }, [tab, only, query]);

  const summary = useQuery({ queryKey: ["review-summary", kind], queryFn: () => api<Summary>(`/api/hub/drafts/summary?kind=${kind}`), refetchInterval: 10000 });
  const list = useInfiniteQuery({
    queryKey: ["review", kind, tab, only, query], placeholderData: keepPreviousData, initialPageParam: 0,
    queryFn: ({ pageParam }) => api<{ total: number; items: Draft[] }>(
      `/api/drafts?kind=${kind}&status=${tab}&only=${tab === "draft" ? only : ""}&q=${encodeURIComponent(query)}&offset=${pageParam}&limit=${PAGE}`),
    getNextPageParam: (last, pages) => (pages.length * PAGE < last.total ? pages.length * PAGE : undefined),
  });
  const items = useMemo(() => (list.data?.pages.flatMap((p) => p.items) || []).filter((d) => !gone.has(d.id)), [list.data, gone]);
  const total = list.data?.pages[0]?.total ?? 0;
  const s = summary.data;

  const refresh = () => { qc.invalidateQueries({ queryKey: ["review-summary", kind] }); qc.invalidateQueries({ queryKey: ["state"] }); qc.invalidateQueries({ queryKey: ["hello"] }); };
  const reload = () => { setGone(new Set()); qc.invalidateQueries({ queryKey: ["review", kind] }); refresh(); };
  const hide = (ids: number[]) => setGone((g) => new Set([...g, ...ids]));
  const UNDO: Record<string, string> = { approve: "unapprove", skip: "restore", unapprove: "approve", restore: "skip" };

  async function act(ids: number[], action: string, text?: string) {
    try {
      if (ids.length === 1 && text !== undefined) await post(`/api/drafts/${ids[0]}`, { action, text });
      else await post("/api/drafts", { ids, action });
    } catch (e) { toast((e as Error).message, "bad"); return; }
    hide(ids);
    setPicked((p) => { const x = new Set(p); ids.forEach((i) => x.delete(i)); return x; });
    refresh();
    const word = { approve: "Approved", skip: "Skipped", unapprove: "Back to check", restore: "Back to check" }[action] || "Done";
    toast(`${word}${ids.length > 1 ? `: ${ids.length.toLocaleString()} ${what}` : "."}`, "good",
      { label: "Undo", run: async () => { await post("/api/drafts", { ids, action: UNDO[action] }).catch(() => {}); reload(); } });
  }

  async function approveAllReady() {
    const ready = s?.ready || 0;
    if (!ready) return toast(`No ${what} are ready to approve here.`, "info");
    const big = ready > 50;
    if (!await confirm({
      title: `Approve all ${n(ready)} ready ${what}?`,
      text: <div className="grid gap-2">
        <p>They'll go out only when sending runs, one at a time with gaps, and at most {n(s?.daily_messages)} a day (your limit). Stop now stops it at any moment.</p>
        <p>{kind === "hello" ? "Instagram restricts automated and bulk messaging, so a smaller batch is the safer choice." : "Replies that need a proper look are left for you to do one by one."}</p>
        <p>You can undo this straight afterwards.</p>
        {big && <p className="font-medium text-ink">Type APPROVE to confirm.</p>}
      </div>,
      ok: `Approve ${n(ready)}`, typed: big ? "APPROVE" : undefined,
    })) return;
    try {
      const r = await post<{ approved: number; ids: number[] }>("/api/hub/drafts/approve-ready", { kind });
      reload();
      toast(`Approved ${n(r.approved)} ${what}.`, "good", { label: "Undo", run: async () => { await post("/api/drafts", { ids: r.ids, action: "unapprove" }).catch(() => {}); reload(); } });
    } catch (e) { toast((e as Error).message, "bad"); }
  }

  const allPicked = items.length > 0 && items.every((d) => picked.has(d.id));
  return (
    <div>
      <PageHeader title={kind === "hello" ? "Review hellos" : "Review replies"}
        subtitle={kind === "hello" ? "Your hellos to people you follow and have never messaged. Read, edit, approve or skip. Nothing sends until sending runs."
          : "Replies Convo wrote in your voice. Read, edit, approve or skip. Nothing sends until sending runs."}
        actions={<>
          <Link to={kind === "hello" ? "/review/replies" : "/review/hellos"}><Button icon={kind === "hello" ? MessagesSquare : Hand}>{kind === "hello" ? "Replies" : "Hellos"}</Button></Link>
          {tab === "draft" && <Button variant="primary" icon={CheckCheck} disabled={!s?.ready} onClick={approveAllReady}>Approve all ready{s ? ` (${n(s.ready)})` : ""}</Button>}
        </>} />

      <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {([["draft", "Waiting for you", s?.waiting], ["approved", "Approved", s?.approved], ["other", "Skipped and problems", s?.other], ["sent", "Sent", s?.sent]] as [Tab, string, number | undefined][]).map(([t, label, count]) => (
          <button key={t} onClick={() => setTab(t)} className={cx("rounded-2xl border px-4 py-3 text-left transition-colors",
            tab === t ? "border-violet/50 bg-violet-soft" : "border-line bg-surface hover:border-line-2")}>
            <div className="text-[12.5px] text-muted">{label}</div>
            <div className="text-[22px] font-semibold tabular tracking-[-0.02em]">{summary.isLoading ? "…" : n(count)}</div>
          </button>
        ))}
      </div>

      <Card bodyClass="!p-0">
        <div className="flex flex-wrap items-center gap-3 border-b border-line p-4">
          <div className="relative min-w-[200px] grow"><Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-faint" />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search a name or @username" className="pl-9" aria-label={`Search ${what}`} /></div>
          {tab === "draft" && <Segmented<Only> value={only} onChange={setOnly}
            options={[["", "All"], ["interesting", `You'd click${s ? ` (${n(s.interesting)})` : ""}`], ["look", `Needs a look${s ? ` (${n(s.look)})` : ""}`]]} />}
          {(tab === "draft" || tab === "approved") && items.length > 0 && (
            <label className="flex items-center gap-2 text-[13px] text-muted"><input type="checkbox" className="size-4 accent-violet" checked={allPicked}
              onChange={() => setPicked(allPicked ? new Set() : new Set(items.map((d) => d.id)))} />Select shown</label>)}
        </div>

        <div className="divide-y divide-line">
          {list.error ? <div className="p-4"><ErrorState error={list.error} retry={() => list.refetch()} /></div>
            : list.isLoading ? [...Array(4)].map((_, i) => <div key={i} className="p-4"><Skeleton className="h-28" /></div>)
            : items.length ? items.map((d) => <Row key={d.id} d={d} tab={tab} picked={picked.has(d.id)}
                onPick={(v) => setPicked((p) => { const x = new Set(p); v ? x.add(d.id) : x.delete(d.id); return x; })} onAct={act} />)
            : <Empty icon={kind === "hello" ? Hand : MessagesSquare} title={query ? "Nobody by that name here" : tab === "draft" ? `No ${what} waiting` : `Nothing here`}>
                {tab === "draft" && !query ? (kind === "hello" ? "New hellos are prepared after each Instagram download." : "Replies are written after each download.") : null}</Empty>}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-4 py-3 text-[12.5px] text-faint">
          <span>Showing {n(items.length)} of {n(Math.max(0, total - gone.size))}</span>
          {list.hasNextPage && <Button size="sm" loading={list.isFetchingNextPage} onClick={() => list.fetchNextPage()}>Show {PAGE} more</Button>}
        </div>
      </Card>

      {picked.size > 0 && (
        <div className="safe-bottom fixed inset-x-0 bottom-16 z-30 flex justify-center px-4 lg:bottom-6 lg:pl-[266px]">
          <div className="flex animate-rise flex-wrap items-center gap-2 rounded-2xl border border-line-2 bg-raised px-4 py-2.5 shadow-[var(--shadow-pop)]">
            <span className="mr-1 text-[13.5px] font-medium">{n(picked.size)} selected</span>
            {tab === "draft" ? <>
              <Button size="sm" variant="primary" icon={Check} onClick={() => act([...picked], "approve")}>Approve</Button>
              <Button size="sm" icon={SkipForward} onClick={() => act([...picked], "skip")}>Skip</Button>
            </> : <Button size="sm" icon={Undo2} onClick={() => act([...picked], "unapprove")}>Undo approval</Button>}
            <Button size="sm" variant="ghost" icon={X} onClick={() => setPicked(new Set())} aria-label="Clear selection">Clear</Button>
          </div>
        </div>
      )}
    </div>
  );
}

function Row({ d, tab, picked, onPick, onAct }: { d: Draft; tab: Tab; picked: boolean; onPick: (v: boolean) => void; onAct: (ids: number[], action: string, text?: string) => void }) {
  const [text, setText] = useState(d.text);
  const [saving, setSaving] = useState(false);
  const toast = useToast();
  useEffect(() => setText(d.text), [d.id, d.text]);
  const ctx = d.context || {};
  const name = d.other_name || d.username || "Someone";
  const look = ctx.needs_look || ctx.manual || d.note;
  const editable = tab === "draft" || tab === "approved";
  const save = async () => {
    if (text === d.text || !editable) return;
    setSaving(true);
    try { await post(`/api/drafts/${d.id}`, { action: "save", text }); d.text = text; toast("Saved your words."); }
    catch (e) { toast((e as Error).message, "bad"); } finally { setSaving(false); }
  };
  return (
    <div className={cx("flex gap-3 px-4 py-4 sm:gap-4", picked && "bg-violet-soft/40")}>
      {editable && <input type="checkbox" className="mt-3 size-4 shrink-0 accent-violet" checked={picked} onChange={(e) => onPick(e.target.checked)} aria-label={`Select ${name}`} />}
      <Avatar name={name} size={40} className="mt-0.5 max-sm:hidden" />
      <div className="min-w-0 grow">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="truncate text-[14.5px] font-semibold">{name}</span>
          {d.username && d.other_name && <span className="truncate text-[12.5px] text-muted">@{d.username}</span>}
          {(d.interest ?? ctx.interest ?? 0) >= 3 && <Badge tone="good" icon={Star}>You'd click</Badge>}
          {(d.interest ?? ctx.interest ?? 0) === 2 && <Badge tone="violet">Worth a look</Badge>}
          {(d.concern || ctx.concern) === "high" && <Badge tone="bad" icon={TriangleAlert}>Careful</Badge>}
          {tab === "sent" && d.sent_at && <span className="ml-auto text-[12px] text-faint">Sent {ago(d.sent_at)}</span>}
        </div>
        {d.summary && <p className="mt-0.5 line-clamp-2 text-[12.5px] text-muted">{d.summary}</p>}
        {look && tab === "draft" && <Note tone="warn" className="mt-2">{d.note || (ctx.manual ? "This one is left for you (Convo didn't write it)." : "Needs a proper look before approving.")}</Note>}
        {(d.concern || ctx.concern) === "high" && (d.concern_why || ctx.concern_why) && <Note tone="bad" className="mt-2">{d.concern_why || ctx.concern_why}</Note>}
        {d.recent && d.recent.length > 0 && (
          <div className="mt-3 flex flex-col gap-1 rounded-xl bg-bg-2/70 p-3">
            {d.recent.slice(-4).map((m, i) => <Bubble key={i} mine={!!m.from_me} text={m.text} />)}
          </div>
        )}
        {editable ? (
          <Textarea className="mt-3" rows={2} value={text} onChange={(e) => setText(e.target.value)} onBlur={save} aria-label={`Message to ${name}`} placeholder="Write the message" />
        ) : <p className="mt-2 whitespace-pre-wrap rounded-xl bg-bg-2/70 px-3 py-2 text-[14px] text-ink-2">{d.text || "—"}</p>}
        <div className="mt-2.5 flex flex-wrap items-center gap-2">
          {tab === "draft" && <>
            <Button size="sm" variant="primary" icon={Check} disabled={!text.trim()} onClick={() => onAct([d.id], "approve", text)}>Approve</Button>
            <Button size="sm" variant="ghost" icon={SkipForward} onClick={() => onAct([d.id], "skip")}>Skip</Button>
          </>}
          {tab === "approved" && <Button size="sm" icon={Undo2} onClick={() => onAct([d.id], "unapprove", text)}>Undo approval</Button>}
          {tab === "other" && <Button size="sm" icon={Undo2} onClick={() => onAct([d.id], "restore")}>Back to check</Button>}
          {tab === "other" && d.note && <span className="text-[12.5px] text-muted">{d.note}</span>}
          {saving && <span className="text-[12px] text-faint">Saving…</span>}
        </div>
      </div>
    </div>
  );
}
