import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Hand, MessagesSquare, Pause, Play, Send, ShieldCheck, Square } from "lucide-react";
import { Link } from "react-router-dom";
import { Bars, SERIES } from "../components/charts";
import { Badge, Button, Card, ErrorState, Note, PageHeader, Skeleton, useToast } from "../components/ui";
import { api, post } from "../lib/api";
import { num, pct, plural } from "../lib/format";
import { useAppState } from "../lib/state";
import { Stat } from "./Dashboard";
import { useRunActions } from "../components/RunControl";

export default function Hello() {
  const toast = useToast();
  const qc = useQueryClient();
  const st = useAppState().data;
  const { data: h, error, refetch } = useQuery({ queryKey: ["hello"], queryFn: () => api("/api/hub/hello"), refetchInterval: 8000 });
  const m = useMutation({
    mutationFn: ({ path, body }: { path: string; body?: any }) => post(path, body),
    onSuccess: (_, v) => { toast(v.path.includes("pause") ? "Paused." : v.path.includes("stop") ? "Stopped." : v.body?.just_one ? "Sending one. Check it in Instagram." : "Sending what you approved."); qc.invalidateQueries(); },
    onError: (e) => toast((e as Error).message, "bad"),
  });
  const { stop } = useRunActions();
  if (error) return <ErrorState error={error} retry={refetch} />;
  const sending = st ? st.sender.active : h?.sender.active;          // the live state, so buttons change at once
  const stopped = st ? st.sender.stopped && !st.sender.active : h?.sender.stopped;
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Hello Message"
        subtitle="Say hello to people you follow and have never messaged, and answer people who write to you. Every message waits for your approval."
        actions={<>
          {sending ? <Button icon={Pause} onClick={() => m.mutate({ path: "/api/send/pause" })}>Pause</Button>
            : <><Button icon={Play} disabled={!st?.counts.approved} onClick={() => m.mutate({ path: "/api/send/start", body: { just_one: true } })}>Send just one</Button>
                <Button variant="primary" icon={Send} disabled={!st?.counts.approved && !st?.counts.unfollow_queued}
                  onClick={() => m.mutate({ path: "/api/send/start", body: { just_one: false } })}>Send everything approved{st?.counts.approved ? ` (${st.counts.approved})` : ""}</Button></>}
          {stopped ? <span className="inline-flex h-9 items-center gap-1.5 rounded-xl bg-bad-soft px-3 text-sm font-medium text-bad"><Square className="size-3.5 fill-current" />Stopped</span>
            : <Button variant={sending ? "danger" : "danger-soft"} icon={Square} loading={stop.isPending} onClick={() => stop.mutate()}>Stop</Button>}
        </>} />

      {!h ? <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{[...Array(4)].map((_, i) => <Skeleton key={i} className="h-[112px]" />)}</div> : (<>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat label="Waiting for your approval" icon={Hand} value={num(h.waiting_approval)} sub="Kept exactly as they are until you decide" />
          <Stat label="Approved, ready to go" icon={ShieldCheck} tone="good" value={num(h.approved)} sub={h.send_at ? `Sends by itself at ${h.send_at}` : "Goes when you press Send"} />
          <Stat label="Hellos sent" icon={Send} tone="cyan" value={num(h.sent)} sub={`${num(h.sent_7d)} this week`} />
          <Stat label="Reply rate" icon={Hand} tone="violet" value={h.reply_rate == null ? "—" : pct(h.reply_rate)}
            sub={h.reply_rate == null ? "Shows once hellos have gone and a new download is loaded" : `${plural(h.replied, "person", "people")} wrote back`} />
        </div>

        <Card title={sending ? "Sending now" : "Sending"} subtitle={st?.sender.status || h.sender.status}
          action={<Badge tone={sending ? "good" : stopped ? "bad" : "neutral"}>{sending ? "Running" : stopped ? "Stopped by you" : "Not running"}</Badge>}>
          <Bars data={h.series} keys={[{ key: "sent_hellos", color: SERIES.violet }, { key: "sent_replies", color: SERIES.cyan }]}
            names={{ sent_hellos: "Hellos sent", sent_replies: "Replies sent" }} height={200} />
        </Card>

        <div className="grid gap-4 lg:grid-cols-2">
          <Card title="Review and approve" subtitle="The full Hello workspace: read, edit, approve or skip each message">
            <div className="flex flex-wrap gap-2">
              <Link to="/review/hellos"><Button variant="primary" icon={Hand}>Review hellos ({num(h.waiting_approval)})</Button></Link>
              <Link to="/review/replies"><Button icon={MessagesSquare}>Review replies ({num(st?.counts.replies_to_check || 0)})</Button></Link>
            </div>
            <p className="mt-3 text-[12.5px] text-muted">Search, filter to the people you'd click with, edit, approve or skip, one by one or many at once, with Undo.
              The classic screens are still at the bottom of the sidebar.</p>
          </Card>
          <Card title="Safeguards" icon={ShieldCheck}>
            <ul className="flex flex-col gap-2 text-[13.5px] text-ink-2">
              <li>• Nothing is sent without your approval. Your queue is never changed for you.</li>
              <li>• Before every hello, Claude checks the chat is still empty; before every reply, that they haven't written again.</li>
              <li>• Up to {num(st?.settings.daily_messages)} messages a day, with {st?.settings.message_gap_s}s between them. Change it in Account Settings.</li>
              <li>• If Instagram shows any "try again later" warning, sending stops for 6 hours by itself.</li>
            </ul>
          </Card>
        </div>
        <Note tone="warn">Instagram doesn't let any app start conversations through its official API, so hellos go through Convo's own browser,
          one at a time with your approval. Instagram's rules restrict automated and bulk messaging; Convo stops by itself when Instagram asks it to.</Note>
      </>)}
    </div>
  );
}
