import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCcw, Send, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button, Card, cx, Note, PageHeader } from "../components/ui";
import { api, post } from "../lib/api";
import { money } from "../lib/format";
import { prefs, useAppState } from "../lib/state";

type Msg = { who: "you" | "convo"; text: string; meta?: string; pending?: boolean };

const SUGGESTIONS = [
  "What's working this month?", "How many hellos got a reply?", "What's waiting on me today?",
  "Give me 5 post ideas for my main account and save them", "What's on my content plan this week?", "Who's the most interesting person I've talked to this week?",
];

export default function Strategist() {
  const qc = useQueryClient();
  const ai = useAppState().data?.ai.on;
  const [log, setLog] = useState<Msg[]>(() => { try { return JSON.parse(sessionStorage.getItem("convo.strategist") || "[]"); } catch { return []; } });
  const [q, setQ] = useState("");
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { sessionStorage.setItem("convo.strategist", JSON.stringify(log.filter((m) => !m.pending).slice(-40))); end.current?.scrollIntoView({ behavior: "smooth" }); }, [log]);
  const m = useMutation({
    mutationFn: (text: string) => post("/api/ask", { text }),
    onMutate: (text) => setLog((l) => [...l, { who: "you", text }, { who: "convo", text: "", pending: true }]),
    onSuccess: (r) => { setLog((l) => [...l.slice(0, -1), { who: "convo", text: r.answer, meta: [r.actions?.length ? `Did: ${r.actions.join(", ")}` : "", r.cost ? money(r.cost) : "free"].filter(Boolean).join(" · ") }]); qc.invalidateQueries(); },
    onError: (e) => setLog((l) => [...l.slice(0, -1), { who: "convo", text: `That didn't work: ${(e as Error).message}` }]),
  });
  const ask = (t: string) => { if (t.trim() && !m.isPending) { m.mutate(t.trim()); setQ(""); } };
  void prefs;
  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_300px]">
    <div className="flex flex-col gap-4">
      <PageHeader title="AI Strategist" subtitle="Ask about your people and messages, or tell Convo what to do. It answers from your real data only."
        actions={log.length > 0 && <Button variant="ghost" icon={RotateCcw} onClick={async () => { setLog([]); await post("/api/ask/reset").catch(() => {}); }}>New chat</Button>} />
      {!ai && <Note tone="warn">Questions need your AI key (API Connections). Without it, Convo understands: status, stop, pause, send, send one.</Note>}
      <Card bodyClass="!p-0">
        <div className="flex min-h-[46dvh] flex-col gap-4 px-5 py-6">
          {log.length ? log.map((x, i) => (
            <div key={i} className={cx("max-w-[80%] rounded-2xl px-4 py-3 text-[14.5px] leading-relaxed whitespace-pre-wrap",
              x.who === "you" ? "self-end rounded-br-md bg-violet text-white" : "self-start rounded-bl-md border border-line bg-raised text-ink-2")}>
              {x.pending ? <span className="flex gap-1">{[0, 1, 2].map((d) => <span key={d} className="size-2 animate-bounce rounded-full bg-faint" style={{ animationDelay: `${d * 0.15}s` }} />)}</span> : x.text}
              {x.meta && <div className="mt-1.5 text-[11.5px] opacity-60">{x.meta}</div>}
            </div>
          )) : (
            <div className="m-auto max-w-lg text-center">
              <div className="mx-auto mb-4 grid size-14 place-items-center rounded-2xl bg-gradient-to-br from-violet/30 to-cyan/20"><Sparkles className="size-6 text-violet-2" /></div>
              <h2 className="text-[17px] font-semibold">Your strategist</h2>
              <p className="mt-1.5 text-[13.5px] text-muted">It knows your people, chats, hellos, follow-ups and content plan, with the real numbers on the right.
                It never makes up a statistic: views and reach arrive when YouTube, Instagram and TikTok are connected.</p>
            </div>)}
          <div ref={end} />
        </div>
        <div className="border-t border-line p-4">
          <div className="mb-3 flex flex-wrap gap-2">{SUGGESTIONS.map((s) => (
            <button key={s} onClick={() => setQ(s.replace("…", ""))} className="rounded-full border border-line-2 px-3 py-1 text-[12.5px] text-muted hover:border-violet hover:text-violet-2">{s}</button>))}</div>
          <form onSubmit={(e) => { e.preventDefault(); ask(q); }} className="flex gap-2">
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask or tell Convo anything" aria-label="Ask the AI Strategist"
              className="h-11 grow rounded-xl border border-line-2 bg-bg-2 px-4 text-[14.5px] outline-none focus:border-violet focus:ring-4 focus:ring-violet/15" />
            <Button variant="primary" size="lg" icon={Send} loading={m.isPending}>Ask</Button>
          </form>
          <p className="mt-2 text-[12px] text-faint">Simple commands are free; questions cost about 1 to 2¢. Convo only sends exact words you type, to people you name.</p>
        </div>
      </Card>
    </div>
    <Facts />
    </div>
  );
}

/** The numbers the strategist works from, so you can see them too. */
function Facts() {
  const { data } = useQuery({ queryKey: ["analytics", 30], queryFn: () => api("/api/hub/analytics?days=30") });
  const plan = useQuery({ queryKey: ["content-stats"], queryFn: () => api("/api/hub/content-stats") });
  if (!data) return <Card title="Your numbers"><div className="h-40" /></Card>;
  const t = (k: string) => data.series.reduce((n: number, d: any) => n + d[k], 0);
  const h = data.hello, r = data.relationships, p = plan.data?.by_status;
  const rows: [string, string][] = [
    ["Messages you sent (30 days)", String(t("sent_hellos") + t("sent_replies"))], ["Messages you got (30 days)", String(t("received"))],
    ["Hellos sent", String(h.sent)], ["Hello reply rate", h.reply_rate == null ? "Not yet" : `${Math.round(h.reply_rate * 100)}%`],
    ["Followers (newest download)", r.followers ? String(r.followers) : "—"], ["People you've chatted with", String(r.chatted)],
    ["Posts scheduled", p ? String(p.scheduled + p.due) : "—"], ["Posted this week", plan.data ? String(plan.data.posted_7d) : "—"],
  ];
  return (
    <Card title="Your numbers" subtitle="Real, from your data (last 30 days)">
      <dl className="grid gap-2.5">{rows.map(([k, v]) => (
        <div key={k} className="flex items-baseline justify-between gap-3 border-b border-line pb-2 last:border-0 last:pb-0">
          <dt className="text-[13px] text-muted">{k}</dt><dd className="tabular text-[14px] font-semibold">{v}</dd></div>))}</dl>
      <p className="mt-3 text-[12px] text-faint">Views, reach and watch time aren't measured until your platforms are connected.</p>
    </Card>
  );
}
