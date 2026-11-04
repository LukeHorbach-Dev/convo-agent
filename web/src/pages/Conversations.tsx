import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Check, MessagesSquare, Search, SkipForward, Star, TriangleAlert, Undo2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Avatar, Badge, Bubble, Button, Card, cx, Empty, ErrorState, Input, MessageText, Note, PageHeader, Segmented, Skeleton, Textarea, useToast } from "../components/ui";
import { api, post } from "../lib/api";
import { ago } from "../lib/format";

type Show = "waiting" | "all" | "requests" | "groups";

export default function Conversations() {
  const [show, setShow] = useState<Show>("waiting");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["conversations", show, q], placeholderData: keepPreviousData,
    queryFn: () => api(`/api/hub/conversations?show=${show}&q=${encodeURIComponent(q)}&limit=80`),
  });
  const items: any[] = data?.items || [];
  useEffect(() => { if (!open && items[0] && window.innerWidth >= 1024) setOpen(items[0].id); }, [items, open]);
  return (
    <div>
      <PageHeader title="Conversations" subtitle="Every chat from your Instagram downloads, with the replies Convo has written for you." />
      <div className="grid gap-4 lg:grid-cols-[380px_1fr]">
        <Card className={cx(open && "max-lg:hidden")} bodyClass="!p-0">
          <div className="flex flex-col gap-3 border-b border-line p-4">
            <div className="relative"><Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-faint" />
              <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search a name" className="pl-9" aria-label="Search conversations" /></div>
            <Segmented value={show} onChange={(v) => { setShow(v); setOpen(null); }}
              options={[["waiting", "Waiting on you"], ["all", "All"], ["requests", "Requests"], ["groups", "Groups"]]} />
          </div>
          <div className="max-h-[calc(100dvh-300px)] min-h-[300px] overflow-y-auto p-2">
            {error ? <div className="p-2"><ErrorState error={error} retry={refetch} /></div>
              : isLoading ? [...Array(6)].map((_, i) => <Skeleton key={i} className="m-2 h-14" />)
              : items.length ? items.map((t) => (
                <button key={t.id} onClick={() => setOpen(t.id)}
                  className={cx("flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left transition-colors",
                    open === t.id ? "bg-raised shadow-[inset_0_0_0_1px_var(--color-line-2)]" : "hover:bg-hover/60")}>
                  <Avatar name={t.other_name || t.username || t.title} size={38} />
                  <span className="min-w-0 grow">
                    <span className="flex items-center gap-2"><span className="truncate text-[14px] font-medium text-ink">{t.other_name || t.title || t.username}</span>
                      {t.interest >= 3 && <Star className="size-3.5 shrink-0 fill-good text-good" />}
                      {t.concern === "high" && <TriangleAlert className="size-3.5 shrink-0 text-bad" />}
                      <span className="ml-auto shrink-0 text-[11.5px] text-faint">{ago(t.last_at)}</span></span>
                    <span className="block truncate text-[12.5px] text-muted">{t.last_from_me ? "You: " : ""}{t.last_text ? <MessageText text={t.last_text} /> : "—"}</span>
                  </span>
                  {t.draft_status === "draft" && !t.last_from_me && <span className="size-2 shrink-0 rounded-full bg-violet" title="A reply is ready to check" />}
                </button>))
              : <Empty icon={MessagesSquare} title={show === "waiting" ? "Nobody's waiting on you" : "No conversations"}>
                  {show === "waiting" ? "When people write and you haven't answered, they show up here." : "Load an Instagram download to see your chats."}</Empty>}
          </div>
          {data && <div className="border-t border-line px-4 py-2.5 text-[12px] text-faint">{data.total.toLocaleString()} conversations</div>}
        </Card>
        <div className={cx(!open && "max-lg:hidden")}>
          {open ? <Thread id={open} onBack={() => setOpen(null)} /> : <Card><Empty icon={MessagesSquare} title="Pick a conversation" /></Card>}
        </div>
      </div>
    </div>
  );
}

function Thread({ id, onBack }: { id: string; onBack: () => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const { data, isLoading, error, refetch } = useQuery({ queryKey: ["conversation", id], queryFn: () => api(`/api/hub/conversations/${encodeURIComponent(id)}`) });
  const draft = data?.drafts.find((d: any) => ["draft", "approved"].includes(d.status) && d.kind === "reply");
  const [text, setText] = useState("");
  useEffect(() => setText(draft?.text || ""), [draft?.id, draft?.text]);
  const m = useMutation({
    mutationFn: (action: string) => post(`/api/drafts/${draft.id}`, { action, text }),
    onSuccess: (_, a) => { toast(a === "approve" ? "Approved. It goes when sending runs." : a === "skip" ? "Skipped." : a === "unapprove" ? "Back to check." : "Saved."); qc.invalidateQueries(); },
    onError: (e) => toast((e as Error).message, "bad"),
  });
  if (error) return <ErrorState error={error} retry={refetch} />;
  if (isLoading || !data) return <Card><Skeleton className="h-[420px]" /></Card>;
  const t = data.thread, p = data.person, ctx = draft ? JSON.parse(draft.context || "{}") : {};
  return (
    <Card bodyClass="!p-0">
      <div className="flex items-center gap-3 border-b border-line px-5 py-4">
        <button className="lg:hidden" onClick={onBack} aria-label="Back to conversations"><ArrowLeft className="size-5 text-muted" /></button>
        <Avatar name={t.other_name || t.username} size={40} />
        <div className="min-w-0 grow"><div className="truncate text-[15px] font-semibold">{t.other_name || t.title}</div>
          <div className="truncate text-[12.5px] text-muted">{t.username ? `@${t.username}` : "username not known yet"}{p?.summary ? ` · ${p.summary}` : ""}</div></div>
        {p?.interest >= 3 && <Badge tone="good" icon={Star}>You'd click</Badge>}
        {p?.concern === "high" && <Badge tone="bad">Careful</Badge>}
      </div>
      <div className="flex max-h-[calc(100dvh-420px)] min-h-[260px] flex-col gap-1.5 overflow-y-auto px-5 py-4">
        {data.messages.slice(-60).map((m: any, i: number) => <Bubble key={i} mine={!!m.from_me} text={m.text} time={ago(m.at)} />)}
      </div>
      <div className="border-t border-line bg-bg-2/60 px-5 py-4">
        {draft ? (<>
          {draft.note && <Note tone="warn" className="mb-3">{draft.note}</Note>}
          {ctx.concern === "high" && ctx.concern_why && <Note tone="bad" className="mb-3">{ctx.concern_why}</Note>}
          <label className="mb-1.5 block text-[12px] font-semibold uppercase tracking-wide text-violet-2" htmlFor="reply">Your reply {draft.status === "approved" && <Badge tone="good" className="ml-2 normal-case">Approved</Badge>}</label>
          <Textarea id="reply" rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder="Type your reply" />
          <div className="mt-3 flex flex-wrap gap-2">
            {draft.status === "draft" ? <>
              <Button variant="primary" icon={Check} loading={m.isPending && m.variables === "approve"} onClick={() => m.mutate("approve")}>Approve</Button>
              <Button variant="ghost" icon={SkipForward} onClick={() => m.mutate("skip")}>Skip</Button>
              {text !== draft.text && <Button variant="ghost" onClick={() => m.mutate("save")}>Save words</Button>}
            </> : <Button icon={Undo2} onClick={() => m.mutate("unapprove")}>Undo approval</Button>}
          </div>
        </>) : (
          <p className="text-[13px] text-muted">{t.last_from_me ? "You wrote last. Nothing to reply to." : t.is_request ? "A message request: accept it in the Instagram app before replying."
            : t.is_group ? "Group chats aren't answered by Convo." : "No reply written yet. Replies are written after each download (or press Write replies now in Quick actions)."}</p>
        )}
      </div>
    </Card>
  );
}
