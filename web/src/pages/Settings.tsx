import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Brain, CheckCircle2, CircleDashed, Database, FlaskConical, KeyRound, Lock, LogIn, Minus, Plug, Plus, Power, RefreshCw, Save, Trash2, Type, XCircle,
} from "lucide-react";
import { ReactNode, useEffect, useState } from "react";
import { Badge, Button, Card, cx, Dot, ErrorState, Field, IconButton, Input, Note, PageHeader, Select, Skeleton, Textarea, Toggle, useConfirm, useToast } from "../components/ui";
import { api, post } from "../lib/api";
import { ago, money, plural } from "../lib/format";
import { useAppState, useReadingPrefs } from "../lib/state";

function Row({ title, hint, children }: { title: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="grid gap-3 border-b border-line py-4 first:pt-0 last:border-0 last:pb-0 md:grid-cols-[1fr_minmax(220px,320px)] md:items-center">
      <div><div className="text-[14px] font-medium text-ink">{title}</div>{hint && <div className="mt-0.5 text-[12.5px] text-muted">{hint}</div>}</div>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

function useSave() {
  const toast = useToast();
  const qc = useQueryClient();
  return useMutation({ mutationFn: (body: any) => post("/api/settings", body), onSuccess: () => { toast("Saved."); qc.invalidateQueries(); },
    onError: (e) => toast((e as Error).message, "bad") });
}

// ------------------------------------------------------------------------------------------------ Integrations
const STATUS: Record<string, [any, string, string]> = {
  connected: [CheckCircle2, "text-good", "Connected"], not_configured: [XCircle, "text-warn", "Not set up"], error: [XCircle, "text-bad", "Needs attention"],
  unknown: [CircleDashed, "text-muted", "Not checked yet"], planned: [CircleDashed, "text-violet-2", "Coming"],
};

export function Integrations() {
  const { data, isLoading, error, refetch } = useQuery({ queryKey: ["integrations"], queryFn: () => api("/api/hub/integrations") });
  if (error) return <ErrorState error={error} retry={refetch} />;
  const items: any[] = data?.items || [];
  return (
    <div>
      <PageHeader title="Integrations" subtitle="What Convo is connected to, checked for real, and what each platform's official API allows." />
      {isLoading ? <Skeleton className="h-96" /> : (
        <div className="grid gap-4 lg:grid-cols-2">{items.map((i) => {
          const [Icon, color, label] = STATUS[i.status] || STATUS.unknown;
          return (
            <Card key={i.id}>
              <div className="flex items-start gap-3"><Plug className="mt-0.5 size-5 text-violet-2" />
                <div className="min-w-0 grow"><div className="flex flex-wrap items-center gap-2"><span className="text-[15px] font-semibold">{i.name}</span>
                  {i.status === "planned" && <Badge tone="violet">Stage {i.stage}</Badge>}</div>
                  <p className="mt-1 text-[13.5px] text-muted">{i.detail}</p></div>
                <span className={cx("flex shrink-0 items-center gap-1.5 text-[12.5px] font-medium", color)}><Icon className="size-4" />{label}</span>
              </div>
              {i.can && <div className="mt-4 grid gap-3 sm:grid-cols-2 text-[13px]">
                <div><div className="mb-1 text-[11.5px] font-semibold uppercase tracking-wide text-good">Allowed</div>{i.can.map((c: string) => <div key={c} className="text-ink-2">• {c}</div>)}</div>
                <div><div className="mb-1 text-[11.5px] font-semibold uppercase tracking-wide text-faint">Not allowed</div>{i.cannot.map((c: string) => <div key={c} className="text-muted">• {c}</div>)}</div>
              </div>}
            </Card>);
        })}</div>)}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ API Connections
export function ApiConnections() {
  const st = useAppState().data;
  const save = useSave();
  const [key, setKey] = useState("");
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="API Connections" subtitle="Keys and logins stay on this Mac and are never shown on screen again after you save them." />
      <Card title="Anthropic (Claude)" icon={KeyRound} subtitle="Writes replies, powers Ask and the strategist, and uses Instagram for sending"
        action={st?.ai.on ? <Badge tone="good">Connected</Badge> : <Badge tone="warn">Not set up</Badge>}>
        <form className="flex flex-col gap-2 sm:flex-row" onSubmit={(e) => { e.preventDefault(); if (key.trim()) save.mutate({ ai_key: key.trim() }, { onSuccess: () => setKey("") }); }}>
          <Input type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder={st?.ai.on ? "•••••••• saved. Paste a new key to replace it" : "sk-ant-api03-…"} aria-label="Anthropic API key" />
          <Button variant="primary" icon={Save} loading={save.isPending} disabled={!key.trim()}>Save key</Button>
        </form>
        <p className="mt-2 text-[12.5px] text-muted">From console.anthropic.com, API keys. Never paste a key into a chat.</p>
      </Card>
      {[["YouTube", 4, "A Google Cloud project with the YouTube Data API and YouTube Analytics API, and an OAuth client for a desktop app."],
        ["Instagram (official API)", 5, "A Meta developer app with Instagram API access, and your account switched to Creator."],
        ["TikTok", 5, "A TikTok for Developers app with Login Kit, Display API and Content Posting API."]].map(([name, stage, needs]) => (
        <Card key={name as string} title={name as string} icon={Lock} action={<Badge tone="violet">Stage {stage}</Badge>}>
          <p className="text-[13.5px] text-muted">{needs}</p>
          <p className="mt-2 text-[12.5px] text-faint">When we reach stage {stage}, you'll get click-by-click steps, and the sign-in button appears here.</p>
        </Card>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ AI Preferences
const WRITERS: [string, string][] = [["claude-opus-5-5", "Opus: the best writer"], ["claude-sonnet-5-5", "Sonnet: very good, half the price"], ["claude-haiku-5-5", "Haiku: quick and very cheap, plainer"]];

export function AIPreferences() {
  const st = useAppState().data;
  const save = useSave();
  if (!st) return <Skeleton className="h-96" />;
  const s = st.settings, ai = st.ai, est = ai.estimates[s.writer_model] || ai.estimates["claude-opus-5-5"];
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="AI Preferences" subtitle="Which models write for you, what they cost, and what the AI knows about you." />
      <Card title="Writing" icon={Brain}>
        <Row title="Who writes your replies" hint={`About ${money(est.overnight)} a reply overnight, ${money(est.now)} straight away`}>
          <Select value={s.writer_model} onChange={(e) => save.mutate({ writer_model: e.target.value })}>{WRITERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select></Row>
        <Row title="Write overnight at half price" hint="Usually ready within the hour, always by tomorrow">
          <Toggle checked={!!s.write_overnight} onChange={(v) => save.mutate({ write_overnight: v })} label="Write overnight" /></Row>
        <Row title="How Claude uses Instagram" hint="It looks at the page and clicks; the app types only what you approved">
          <Select value={s.eyes_model} onChange={(e) => save.mutate({ eyes_model: e.target.value })}>
            <option value="claude-haiku-5-5">Haiku: about 1¢ a message (recommended)</option><option value="claude-sonnet-5-5">Sonnet: steadier on odd pages, about 15¢</option></Select></Row>
        <Row title="Daily AI limit" hint="Writing and sending pause for the day once this much is spent">
          <BudgetInput value={s.daily_ai_budget_usd} onSave={(v) => save.mutate({ daily_ai_budget_usd: v })} /></Row>
        <p className="mt-4 text-[12.5px] text-muted">Spent: {money(ai.spend_today)} today, {money(ai.spend_total)} in total. Writing {plural(ai.drafts, "reply", "replies")}: {money(ai.drafts_usd)} ·
          Using Instagram ({plural(ai.web_steps, "look")}): {money(ai.web_usd)} · Reports and Ask: {money(ai.other_usd)}.</p>
      </Card>
      <AboutMe />
    </div>
  );
}

function BudgetInput({ value, onSave }: { value: number; onSave: (v: number) => void }) {
  const [v, setV] = useState(String(value));
  useEffect(() => setV(String(value)), [value]);
  return <div className="flex items-center gap-2"><span className="text-faint">$</span><Input type="number" min={0.5} max={100} step={0.5} value={v} onChange={(e) => setV(e.target.value)}
    onBlur={() => Number(v) !== value && onSave(Number(v))} aria-label="Daily AI limit in dollars" /></div>;
}

function AboutMe() {
  const toast = useToast();
  const qc = useQueryClient();
  const { data, isLoading, error, refetch } = useQuery({ queryKey: ["about"], queryFn: () => api("/api/about") });
  const [facts, setFacts] = useState<any[]>([]);
  const [hellos, setHellos] = useState(""), [voice, setVoice] = useState(""), [never, setNever] = useState(""), [newFact, setNewFact] = useState("");
  useEffect(() => { if (data) { setFacts(data.facts); setHellos(data.hellos.join("\n")); setVoice(data.voice); setNever(data.never); } }, [data]);
  const save = useMutation({ mutationFn: () => post("/api/about", { facts, hellos: hellos.split("\n"), voice, never }),
    onSuccess: () => { toast("Saved your About me."); qc.invalidateQueries({ queryKey: ["about"] }); }, onError: (e) => toast((e as Error).message, "bad") });
  if (error) return <ErrorState error={error} retry={refetch} />;
  if (isLoading) return <Skeleton className="h-80" />;
  return (
    <Card title="About me and your voice" subtitle="What the AI knows about you. It only mentions facts on this list, and never the private ones."
      action={<Button variant="primary" icon={Save} loading={save.isPending} onClick={() => save.mutate()}>Save</Button>}>
      <div className="flex flex-col gap-2">
        {facts.map((f, i) => (
          <div key={f.id || i} className="flex items-center gap-2">
            <Input value={f.text} onChange={(e) => setFacts(facts.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)))} aria-label="Fact" />
            <label className="flex shrink-0 items-center gap-2 text-[12.5px] text-muted" title="Private facts are never used in messages">
              <Toggle checked={!!f.private} onChange={(v) => setFacts(facts.map((x, j) => (j === i ? { ...x, private: v } : x)))} label="Private" /><Lock className="size-3.5" /></label>
            <IconButton icon={Trash2} label="Remove fact" onClick={() => setFacts(facts.filter((_, j) => j !== i))} />
          </div>))}
        <form className="mt-1 flex gap-2" onSubmit={(e) => { e.preventDefault(); if (newFact.trim()) { setFacts([...facts, { text: newFact.trim(), private: false }]); setNewFact(""); } }}>
          <Input value={newFact} onChange={(e) => setNewFact(e.target.value)} placeholder="Add something, e.g. I've started playing football again" /><Button icon={Plus}>Add</Button></form>
      </div>
      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <Field label="Your hellos" hint="One per line. One of these goes to each new person." htmlFor="hellos"><Textarea id="hellos" rows={6} value={hellos} onChange={(e) => setHellos(e.target.value)} /></Field>
        <Field label="Never message" hint="One username per line. No hello and no reply, ever." htmlFor="never"><Textarea id="never" rows={6} value={never} onChange={(e) => setNever(e.target.value)} /></Field>
      </div>
      <div className="mt-4"><Field label="How you write" hint="The AI copies this, plus how you write in your own chats." htmlFor="voice"><Textarea id="voice" rows={8} value={voice} onChange={(e) => setVoice(e.target.value)} /></Field></div>
    </Card>
  );
}

// ------------------------------------------------------------------------------------------------ Account Settings
export function AccountSettings() {
  const st = useAppState().data;
  const save = useSave();
  const toast = useToast();
  const confirm = useConfirm();
  const qc = useQueryClient();
  const read = useReadingPrefs();
  const backups = useQuery({ queryKey: ["backups"], queryFn: () => api("/api/hub/backups") });
  const [pace, setPace] = useState<any>(null);
  useEffect(() => { if (st && !pace) setPace({ ...st.settings }); }, [st, pace]);
  const check = useMutation({ mutationFn: () => post("/api/check"), onSuccess: () => toast("Running the check. It takes a couple of minutes."), onError: (e) => toast((e as Error).message, "bad") });
  const checkResult = useQuery({ queryKey: ["check", st?.check.at, st?.check.running], queryFn: () => api("/api/check"), enabled: !!st?.check.at });
  const login = useMutation({ mutationFn: () => post("/api/instagram/login"), onSuccess: () => toast("Sign in in the Instagram window, then press Done."), onError: (e) => toast((e as Error).message, "bad") });
  const done = useMutation({ mutationFn: () => post("/api/instagram/login/done"), onSuccess: (r: any) => toast(r.signed_in ? "Signed in to Instagram." : "Instagram still shows the sign-in page.", r.signed_in ? "good" : "bad") });
  const practice = useMutation({ mutationFn: (real: boolean) => post("/api/practice/start", { real_ai: real }), onSuccess: (r: any) => window.open(r.url, "_blank"), onError: (e) => toast((e as Error).message, "bad") });
  const backup = useMutation({ mutationFn: () => post("/api/hub/backups"), onSuccess: () => { toast("Backed up."); qc.invalidateQueries({ queryKey: ["backups"] }); } });
  if (!st || !pace) return <Skeleton className="h-96" />;
  const set = (k: string) => (e: any) => setPace({ ...pace, [k]: e.target.value });
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Account Settings" subtitle="Sending pace and schedule, your Instagram sign-in, reading, and your data." />
      <Card title="Sending" icon={Plug} action={<Button variant="primary" icon={Save} loading={save.isPending} onClick={() => save.mutate({
        daily_messages: Number(pace.daily_messages), message_gap_s: Number(pace.message_gap_s), daily_unfollows: Number(pace.daily_unfollows),
        unfollow_gap_s: Number(pace.unfollow_gap_s), reply_window_days: Number(pace.reply_window_days), send_at: pace.send_at || "", quiet_from: pace.quiet_from || "", quiet_to: pace.quiet_to || "" })}>Save</Button>}>
        <Note tone="neutral" className="mb-4">Instagram doesn't publish a safe number of messages. These are your own limits; Convo also stops by itself whenever Instagram warns it.</Note>
        <Row title="Messages a day" hint="The rest go tomorrow by themselves"><Input type="number" min={0} max={300} value={pace.daily_messages} onChange={set("daily_messages")} /></Row>
        <Row title="Seconds between messages"><Input type="number" min={0} max={600} value={pace.message_gap_s} onChange={set("message_gap_s")} /></Row>
        <Row title="Unfollows a day"><Input type="number" min={0} max={300} value={pace.daily_unfollows} onChange={set("daily_unfollows")} /></Row>
        <Row title="Seconds between unfollows"><Input type="number" min={0} max={600} value={pace.unfollow_gap_s} onChange={set("unfollow_gap_s")} /></Row>
        <Row title="Reply to chats from the last" hint="Days"><Input type="number" min={1} value={pace.reply_window_days} onChange={set("reply_window_days")} /></Row>
        <Row title="Send by itself at" hint="Leave empty for off"><Input type="time" value={pace.send_at || ""} onChange={set("send_at")} /></Row>
        <Row title="Quiet hours" hint="Nothing is sent between these times"><div className="flex items-center gap-2"><Input type="time" value={pace.quiet_from || ""} onChange={set("quiet_from")} aria-label="From" />
          <span className="text-faint">to</span><Input type="time" value={pace.quiet_to || ""} onChange={set("quiet_to")} aria-label="To" /></div></Row>
      </Card>

      <Card title="Instagram" icon={LogIn}>
        <Row title="Sign in" hint="Opens Instagram in Convo's own browser. Sign in there, then press Done.">
          <div className="flex gap-2"><Button icon={LogIn} loading={login.isPending} onClick={() => login.mutate()}>Open sign-in</Button><Button variant="ghost" loading={done.isPending} onClick={() => done.mutate()}>Done</Button></div></Row>
        <Row title="Self-check" hint="Claude tries every step on your account without sending anything. About 5¢.">
          <Button icon={RefreshCw} loading={check.isPending || st.check.running} onClick={() => check.mutate()}>{st.check.running ? "Checking…" : "Run the check"}</Button></Row>
        {checkResult.data?.report && <pre className="mt-2 max-h-72 overflow-auto rounded-xl border border-line bg-bg-2 p-4 text-[12px] leading-relaxed whitespace-pre-wrap text-ink-2">{checkResult.data.report.split("\n\n== ")[0]}</pre>}
        <Row title="Keep the Instagram window tucked away" hint="Not recommended: a minimised window can stop working. Behind other windows is fine.">
          <Toggle checked={!!st.settings.tuck_window} onChange={(v) => save.mutate({ tuck_window: v })} label="Tuck away" /></Row>
      </Card>

      {!st.practice && <Card title="Practice" icon={FlaskConical} subtitle="A pretend Instagram with made-up people, including automatic sending. Your real account is never touched.">
        <div className="flex flex-wrap gap-2"><Button variant="primary" loading={practice.isPending && practice.variables === false} onClick={() => practice.mutate(false)}>Open practice (free)</Button>
          <Button disabled={!st.ai.on} loading={practice.isPending && practice.variables === true} onClick={() => practice.mutate(true)}>With real Claude (about 10¢)</Button></div>
      </Card>}

      <Card title="Reading" icon={Type}>
        <Row title="Text size"><div className="flex items-center gap-2"><IconButton icon={Minus} label="Smaller" onClick={() => read.setScale(Math.max(0.85, +(read.scale - 0.05).toFixed(2)))} className="border border-line" />
          <span className="tabular w-12 text-center text-[13px]">{Math.round(read.scale * 100)}%</span>
          <IconButton icon={Plus} label="Bigger" onClick={() => read.setScale(Math.min(1.4, +(read.scale + 0.05).toFixed(2)))} className="border border-line" /></div></Row>
        <Row title="Easy-read font" hint="Wider letters and more space between lines"><Toggle checked={read.easy} onChange={read.setEasy} label="Easy-read font" /></Row>
      </Card>

      <Card title="Your data" icon={Database} subtitle="Everything stays on this Mac. Convo makes a full copy before every update and before removing anything."
        action={<Button icon={Save} loading={backup.isPending} onClick={() => backup.mutate()}>Back up now</Button>}>
        {backups.data?.items?.length ? backups.data.items.slice(0, 6).map((b: any) => (
          <div key={b.name} className="flex items-center gap-3 border-b border-line py-2 text-[13px] last:border-0"><Dot tone="good" />
            <span className="min-w-0 grow truncate text-ink-2">{b.name}</span><span className="shrink-0 text-faint">{(b.bytes / 1e6).toFixed(1)} MB · {ago(b.at)}</span></div>))
          : <p className="text-[13px] text-muted">No backups yet.</p>}
      </Card>

      <Card><div className="flex flex-wrap items-center justify-between gap-3"><span className="text-[13px] text-muted">Convo {st.version} for @{st.you}{st.practice ? " (practice)" : ""}</span>
        <Button variant="danger-soft" icon={Power} onClick={async () => {
          if (!await confirm({ title: "Quit Convo?", text: "Sending and overnight checks stop until you open Convo again. This window comes back by itself when you do.", ok: "Quit", danger: true })) return;
          await post("/api/quit").catch(() => {});
        }}>Quit Convo</Button></div></Card>
    </div>
  );
}

