// Scheduled Posts: what's due now (with the words to paste and the file), what's coming, and what went out.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, CheckCircle2, Clock, Copy, FolderOpen, Link2, Plus, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Badge, Button, Card, Empty, ErrorState, Input, Note, PageHeader, Skeleton, useToast } from "../components/ui";
import { api, patch, post } from "../lib/api";
import { Content, pasteText, PLATFORM, preview, when } from "../lib/content";
import { ago } from "../lib/format";

export default function Scheduled() {
  const nav = useNavigate();
  const { data, isLoading, error, refetch } = useQuery({ queryKey: ["content", "scheduled"], refetchInterval: 30000,
    queryFn: () => api<{ items: Content[] }>("/api/hub/content?status=scheduled,due,posted&limit=200") });
  if (error) return <ErrorState error={error} retry={refetch} />;
  const items = data?.items || [];
  const due = items.filter((c) => c.status === "due");
  const coming = items.filter((c) => c.status === "scheduled").sort((a, b) => (a.scheduled_at || "").localeCompare(b.scheduled_at || ""));
  const posted = items.filter((c) => c.status === "posted").slice(0, 20);
  return (
    <div>
      <PageHeader title="Scheduled Posts" subtitle="When a post's time comes, Convo tells you with the words ready to paste. Once your platforms are connected (stages 4 and 5), it can post for you."
        actions={<Button variant="primary" icon={Plus} onClick={() => nav("/studio")}>New post</Button>} />
      {isLoading ? <Skeleton className="h-80" /> : (
        <div className="grid gap-5">
          {due.length > 0 && <Card title={`Time to post (${due.length})`} icon={TriangleAlert}>
            <div className="grid gap-3">{due.map((c) => <DueCard key={c.id} c={c} />)}</div></Card>}
          <Card title="Coming up" icon={CalendarClock}>
            {coming.length ? <div className="flex flex-col divide-y divide-line">{coming.map((c) => <Row key={c.id} c={c} />)}</div>
              : <Empty icon={CalendarClock} title="Nothing scheduled">Schedule a post from the Content Studio or drag one onto the calendar.</Empty>}
          </Card>
          <Card title="Posted recently" icon={CheckCircle2}>
            {posted.length ? <div className="flex flex-col divide-y divide-line">{posted.map((c) => <Row key={c.id} c={c} />)}</div>
              : <p className="text-[13px] text-muted">Posts you mark posted show up here.</p>}
          </Card>
        </div>
      )}
    </div>
  );
}

function Row({ c }: { c: Content }) {
  const nav = useNavigate();
  const img = preview(c);
  return (
    <button onClick={() => nav(`/studio/${c.id}`)} className="flex items-center gap-3 py-3 text-left hover:opacity-90">
      <span className="size-12 shrink-0 overflow-hidden rounded-lg bg-bg-2">{img && <img src={img} alt="" className="size-full object-cover" />}</span>
      <span className="min-w-0 grow"><span className="block truncate text-[14px] font-medium">{c.title || "Untitled"}</span>
        <span className="flex items-center gap-1.5 text-[12.5px] text-muted">{c.platforms.map((p) => <span key={p} className="size-2 rounded-full" style={{ background: PLATFORM[p].color }} />)}
          {c.status === "posted" ? `Posted ${ago(c.posted_at)}` : when(c.scheduled_at)}</span></span>
      {c.status === "posted" ? <Badge tone="good">Posted</Badge> : <Badge tone="violet" icon={Clock}>{when(c.scheduled_at).split(",")[0]}</Badge>}
    </button>
  );
}

function DueCard({ c }: { c: Content }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [url, setUrl] = useState("");
  const copy = async (p: keyof typeof PLATFORM) => { try { await navigator.clipboard.writeText(pasteText(c.versions[p], p)); toast(`Copied for ${PLATFORM[p].name}.`); } catch { toast("Couldn't copy here.", "bad"); } };
  const posted = async () => {
    try { await patch(`/api/hub/content/${c.id}`, { status: "posted", post_url: url.trim() }); qc.invalidateQueries({ queryKey: ["content"] }); toast("Marked posted. Nice."); }
    catch (e) { toast((e as Error).message, "bad"); }
  };
  const img = preview(c);
  return (
    <div className="flex flex-col gap-4 rounded-2xl border border-warn/30 bg-warn-soft/40 p-4 sm:flex-row">
      <div className="size-28 shrink-0 overflow-hidden rounded-xl bg-bg-2">{img && <img src={img} alt="" className="size-full object-cover" />}</div>
      <div className="min-w-0 grow">
        <div className="text-[15px] font-semibold">{c.title || "Untitled"}</div>
        <div className="text-[12.5px] text-muted">Was due {ago(c.scheduled_at)}</div>
        <div className="mt-3 flex flex-wrap gap-2">
          {c.platforms.map((p) => <Button key={p} size="sm" icon={Copy} disabled={!pasteText(c.versions[p], p)} onClick={() => copy(p)}>Copy for {PLATFORM[p].name}</Button>)}
          {c.media && <Button size="sm" icon={FolderOpen} onClick={() => post(`/api/hub/content/${c.id}/reveal`).catch((e) => toast(e.message, "bad"))}>Show the file</Button>}
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <Input className="min-w-[220px] grow" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="Link to the post (optional)" aria-label="Link to the post" />
          <Button variant="primary" icon={Link2} onClick={posted}>Mark posted</Button>
        </div>
        {!c.platforms.some((p) => pasteText(c.versions[p], p)) && <Note className="mt-3">No words yet: open it in the Studio to write them.</Note>}
      </div>
    </div>
  );
}
