import { useMutation, useQueryClient } from "@tanstack/react-query";
import { RotateCcw, Send, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button, Card, cx, Note, PageHeader } from "../components/ui";
import { post } from "../lib/api";
import { money } from "../lib/format";
import { prefs, useAppState } from "../lib/state";

type Msg = { who: "you" | "convo"; text: string; meta?: string; pending?: boolean };

const SUGGESTIONS = [
  "What's waiting on me today?", "Who's the most interesting person I've talked to this week?", "Which chats need a careful look?",
  "Write a reply to @… about football", "How many hellos have I sent, and how many replied?", "Status",
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
              <p className="mt-1.5 text-[13.5px] text-muted">Today it knows your Instagram people, chats and messages. As YouTube, Instagram and TikTok connect (stages 4 and 5),
                it adds views, reach and what to post next. It always says when something is a guess rather than a number.</p>
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
  );
}
