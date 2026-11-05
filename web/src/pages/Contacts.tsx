import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, BellRing, Download, ExternalLink, Search, Sparkles, Star, TriangleAlert, Users } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { ReminderForm } from "./Followups";
import { useEffect, useState } from "react";
import { Avatar, Badge, Bubble, Button, Card, cx, Drawer, Empty, ErrorState, Input, PageHeader, Segmented, Skeleton, Textarea, useToast } from "../components/ui";
import { api, download, post } from "../lib/api";
import { ago, num } from "../lib/format";
import { useAppState } from "../lib/state";

type Show = "following" | "followers" | "chats" | "interesting" | "concerning" | "all";

export default function Contacts() {
  const toast = useToast();
  const [show, setShow] = useState<Show>("following");
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [off, setOff] = useState(0);
  const [params, setParams] = useSearchParams();
  const [person, setPersonState] = useState<string | null>(params.get("u"));
  const setPerson = (u: string | null) => { setPersonState(u); if (!u && params.get("u")) setParams({}, { replace: true }); };
  useEffect(() => { const t = setTimeout(() => { setDebounced(q); setOff(0); }, 220); return () => clearTimeout(t); }, [q]);
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["people", show, debounced, off], placeholderData: keepPreviousData,
    queryFn: () => api(`/api/people?show=${show}&sort=${show === "interesting" ? "interest" : "recent"}&q=${encodeURIComponent(debounced)}&offset=${off}&limit=50`),
  });
  const items: any[] = data?.items || [];
  return (
    <div>
      <PageHeader title="Contacts" subtitle="Everyone from your Instagram downloads, with what Convo has learned about them."
        actions={<Button icon={Download} onClick={() => download("/api/people.csv", "convo-people.csv").catch((e) => toast(e.message, "bad"))}>Spreadsheet</Button>} />
      <Card bodyClass="!p-0">
        <div className="flex flex-col gap-3 border-b border-line p-4 md:flex-row md:items-center">
          <div className="relative md:w-80"><Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-faint" />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search a name or username" className="pl-9" aria-label="Search contacts" /></div>
          <Segmented value={show} onChange={(v) => { setShow(v); setOff(0); }}
            options={[["following", "Following"], ["followers", "Followers"], ["chats", "Chatted"], ["interesting", "Interesting"], ["concerning", "Closer look"], ["all", "Everyone"]]} />
        </div>
        {error ? <div className="p-4"><ErrorState error={error} retry={refetch} /></div> : isLoading ? <div className="p-4"><Skeleton className="h-72" /></div> : items.length ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-[13.5px]">
              <thead className="text-[11.5px] uppercase tracking-wide text-faint"><tr className="border-b border-line">
                <th className="px-5 py-2.5 font-semibold">Person</th><th className="px-3 py-2.5 font-semibold">About them</th>
                <th className="px-3 py-2.5 font-semibold">Relationship</th><th className="px-5 py-2.5 text-right font-semibold">Status</th></tr></thead>
              <tbody>{items.map((p) => (
                <tr key={p.username} onClick={() => setPerson(p.username)} className="cursor-pointer border-b border-line/60 last:border-0 hover:bg-hover/40">
                  <td className="px-5 py-3"><div className="flex items-center gap-3"><Avatar name={p.display_name || p.username} size={34} />
                    <div className="min-w-0"><div className="truncate font-medium text-ink">{p.display_name || p.username}</div><div className="truncate text-[12px] text-muted">@{p.username}</div></div></div></td>
                  <td className="max-w-[340px] px-3 py-3 text-muted"><span className="line-clamp-2">{p.summary || p.note || "—"}</span></td>
                  <td className="px-3 py-3 text-muted whitespace-nowrap">{p.i_follow && p.follows_me ? "Mutual" : p.i_follow ? "You follow" : p.follows_me ? "Follows you" : "—"}</td>
                  <td className="px-5 py-3"><div className="flex justify-end gap-1.5">
                    {p.interest >= 3 && <Badge tone="good" icon={Star}>You'd click</Badge>}
                    {p.interest === 2 && <Badge tone="good">Interesting</Badge>}
                    {p.concern === "high" && <Badge tone="bad" icon={TriangleAlert}>Careful</Badge>}
                    {p.hello === "sent" && <Badge tone="violet">Hello sent</Badge>}
                    {p.hello === "draft" && <Badge tone="neutral">Hello queued</Badge>}
                    {p.unfollow && <Badge tone="neutral">{p.unfollow === "done" ? "Unfollowed" : "Unfollow queued"}</Badge>}
                  </div></td>
                </tr>))}</tbody>
            </table>
          </div>
        ) : <Empty icon={Users} title="Nobody here">Load an Instagram download, or try another filter.</Empty>}
        {data?.total > 0 && (
          <div className="flex items-center justify-between border-t border-line px-5 py-3 text-[12.5px] text-muted">
            <span>{num(off + 1)}–{num(Math.min(off + 50, data.total))} of {num(data.total)}</span>
            <div className="flex gap-2"><Button size="sm" disabled={off === 0} onClick={() => setOff(Math.max(0, off - 50))}>Previous</Button>
              <Button size="sm" disabled={off + 50 >= data.total} onClick={() => setOff(off + 50)}>Next</Button></div>
          </div>)}
      </Card>
      <PersonDrawer username={person} onClose={() => setPerson(null)} />
    </div>
  );
}

export function PersonDrawer({ username, onClose }: { username: string | null; onClose: () => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const ai = useAppState().data?.ai.on;
  const { data: p, isLoading, error } = useQuery({ queryKey: ["person", username], enabled: !!username,
    queryFn: () => api(`/api/people/${encodeURIComponent(username!)}`) });
  const [note, setNote] = useState("");
  useEffect(() => setNote(p?.note || ""), [p?.note, p?.username]);
  const refresh = () => qc.invalidateQueries({ queryKey: ["person", username] });
  const saveNote = useMutation({ mutationFn: () => post(`/api/people/${encodeURIComponent(username!)}/note`, { note }), onSuccess: () => { toast("Note saved"); refresh(); } });
  const never = useMutation({ mutationFn: (on: boolean) => post(`/api/people/${encodeURIComponent(username!)}/never`, { on }),
    onSuccess: (_, on) => { toast(on ? "Convo won't message them." : "They can get messages again."); refresh(); qc.invalidateQueries({ queryKey: ["people"] }); } });
  const report = useMutation({ mutationFn: () => post(`/api/people/${encodeURIComponent(username!)}/report`), onSuccess: () => { toast("Report written"); refresh(); },
    onError: (e) => toast((e as Error).message, "bad") });
  const msgs = p ? p.chats.flatMap((c: any) => c.messages).slice(-40) : [];
  const r = p?.report;
  const [remind, setRemind] = useState(false);
  return (
    <Drawer open={!!username} onClose={onClose} title={username ? `@${username}` : ""}
      header={p && <div className="flex items-center gap-3"><Avatar name={p.display_name || p.username} size={48} />
        <div className="min-w-0"><h2 className="truncate text-[17px] font-semibold">{p.display_name || p.username}</h2>
          <a className="inline-flex items-center gap-1 text-[13px] text-muted hover:text-violet-2" href={`https://www.instagram.com/${p.username}/`} target="_blank" rel="noreferrer">
            @{p.username}<ExternalLink className="size-3" /></a></div></div>}>
      {error ? <ErrorState error={error} /> : isLoading || !p ? <Skeleton className="h-64" /> : (
        <div className="flex flex-col gap-5">
          <div className="flex flex-wrap gap-1.5">
            {p.i_follow ? <Badge>You follow them</Badge> : null}{p.follows_me ? <Badge>Follows you</Badge> : null}
            {p.interest >= 3 && <Badge tone="good" icon={Star}>You'd click</Badge>}
            {p.concern === "high" && <Badge tone="bad">Careful</Badge>}{p.never && <Badge tone="bad" icon={Ban}>Never message</Badge>}
          </div>
          <div><Button size="sm" icon={BellRing} onClick={() => setRemind(true)}>Remind me about them</Button>
            <ReminderForm username={p.username} open={remind} onClose={() => setRemind(false)} /></div>
          {p.summary && <p className="text-[14px] text-ink-2">{p.summary}{p.interest_why && <span className="text-muted"> · {p.interest_why}</span>}</p>}
          {p.concern_why && p.concern !== "none" && <div className="rounded-xl bg-bad-soft px-3 py-2 text-[13px] text-ink-2">{p.concern_why}</div>}
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="primary" icon={Sparkles} loading={report.isPending} disabled={!ai} onClick={() => report.mutate()}>{r ? "New report" : "Write a report (≈1¢)"}</Button>
            <Button size="sm" variant={p.never ? "secondary" : "danger-soft"} icon={Ban} onClick={() => never.mutate(!p.never)}>{p.never ? "Allow messages" : "Never message"}</Button>
          </div>
          {r && (
            <Card title="Report" subtitle={ago(r.at)} icon={Sparkles}>
              <p className="text-[14px] text-ink-2">{r.overview}</p>
              {([["They talk about", r.talks_about], ["In common with you", r.common_ground], ["Their questions you haven't answered", r.open_questions], ["Be careful about", r.careful]] as [string, string[]][])
                .filter(([, v]) => v?.length).map(([h, v]) => (
                  <div key={h} className="mt-3"><div className="text-[11.5px] font-semibold uppercase tracking-wide text-faint">{h}</div>
                    <ul className="mt-1 text-[13.5px] text-ink-2">{v.map((x) => <li key={x}>• {x}</li>)}</ul></div>))}
              {r.next_idea && <div className="mt-3"><div className="text-[11.5px] font-semibold uppercase tracking-wide text-faint">An idea for next</div>
                <div className="mt-1.5 flex"><Bubble mine text={r.next_idea} /></div></div>}
            </Card>
          )}
          <div>
            <label htmlFor="note" className="mb-1.5 block text-[13px] font-medium text-ink-2">Your note about them</label>
            <Textarea id="note" rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Only you see this. The AI reads it when writing to them." />
            {note !== (p.note || "") && <Button size="sm" className="mt-2" loading={saveNote.isPending} onClick={() => saveNote.mutate()}>Save note</Button>}
          </div>
          {p.memory?.length > 0 && <div><div className="mb-1.5 text-[13px] font-medium text-ink-2">What Convo remembers</div>
            <ul className="text-[13.5px] text-muted">{p.memory.slice(-10).map((m: string) => <li key={m}>• {m}</li>)}</ul></div>}
          <div>
            <div className="mb-2 text-[13px] font-medium text-ink-2">Chat {msgs.length ? <span className="text-faint">(last {msgs.length})</span> : null}</div>
            {msgs.length ? <div className={cx("flex flex-col gap-1.5")}>{msgs.map((m: any, i: number) => <Bubble key={i} mine={!!m.from_me} text={m.text} time={ago(m.at)} />)}</div>
              : <p className="text-[13px] text-muted">No chat yet.</p>}
          </div>
          {p.drafts?.length > 0 && <div><div className="mb-2 text-[13px] font-medium text-ink-2">Messages from Convo</div>
            {p.drafts.map((d: any) => <p key={d.id} className="mb-1 text-[13px] text-muted"><Badge className="mr-2">{d.status}</Badge>{d.text || "(empty)"}</p>)}</div>}
        </div>
      )}
    </Drawer>
  );
}
