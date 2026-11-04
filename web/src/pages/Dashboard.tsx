import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity, ArrowRight, ArrowUpRight, CalendarClock, CheckCircle2, CircleDashed, Download, Eye, Hand, Lightbulb, ListTodo,
  MessagesSquare, Plug, Send, Sparkles, Target, TrendingUp, Users,
} from "lucide-react";
import { ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { AreaSeries, SERIES } from "../components/charts";
import { ActivityRow } from "../components/Shell";
import { Avatar, Badge, Button, Card, cx, Empty, ErrorState, Skeleton, useToast } from "../components/ui";
import { api, post } from "../lib/api";
import { ago, compact, greeting, longDate, num, pct, plural } from "../lib/format";
import { useAccount, useAppState } from "../lib/state";

export function Stat({ label, value, sub, icon: Icon, tone = "violet", unavailable, onClick, spark }: {
  label: string; value?: ReactNode; sub?: ReactNode; icon: any; tone?: "violet" | "cyan" | "good" | "warn"; unavailable?: string;
  onClick?: () => void; spark?: ReactNode;
}) {
  const ring = { violet: "text-violet-2 bg-violet-soft", cyan: "text-cyan bg-cyan-soft", good: "text-good bg-good-soft", warn: "text-warn bg-warn-soft" }[tone];
  return (
    <button onClick={onClick} disabled={!onClick}
      className="group flex min-w-0 flex-col rounded-[var(--radius-card)] border border-line bg-surface p-4 text-left shadow-[var(--shadow-card)] transition-colors enabled:hover:border-line-2 enabled:hover:bg-raised disabled:cursor-default">
      <div className="flex items-center gap-2.5">
        <span className={cx("grid size-8 place-items-center rounded-lg", unavailable ? "bg-hover text-faint" : ring)}><Icon className="size-4" /></span>
        <span className="text-[12.5px] font-medium text-muted">{label}</span>
        {onClick && <ArrowUpRight className="ml-auto size-4 text-faint opacity-0 transition-opacity group-hover:opacity-100" />}
      </div>
      {unavailable ? (
        <><div className="mt-3 text-[22px] font-semibold tracking-[-0.02em] text-faint">—</div>
          <div className="mt-1 text-[12px] leading-snug text-faint max-sm:hidden">{unavailable}</div></>
      ) : (
        <div className="mt-3 flex items-end justify-between gap-2">
          <div className="min-w-0"><div className="tabular text-[26px] font-semibold leading-none tracking-[-0.03em] text-ink">{value}</div>
            {sub && <div className="mt-1.5 text-[12px] text-muted">{sub}</div>}</div>{spark}
        </div>
      )}
    </button>
  );
}

const ACTION_ROUTES: Record<string, string> = {
  open_replies: "/conversations", open_hellos: "/hello", settings_ai: "/api-connections", integrations: "/integrations",
  help_download: "/integrations",
};

export default function Dashboard() {
  const nav = useNavigate();
  const toast = useToast();
  const qc = useQueryClient();
  const [acct] = useAccount();
  const st = useAppState().data;
  const { data: o, isLoading, error, refetch } = useQuery({ queryKey: ["overview"], queryFn: () => api("/api/hub/overview"), refetchInterval: 15000 });
  const act = useMutation({
    mutationFn: async (a: string) => {
      if (a === "load_download") return post("/api/import").then(() => "Loading your download…");
      if (a === "send") return post("/api/send/start", { just_one: false }).then(() => "Sending what you approved.");
      return "";
    },
    onSuccess: (m) => { if (m) toast(m); qc.invalidateQueries(); }, onError: (e) => toast((e as Error).message, "bad"),
  });
  const run = (a: string) => (ACTION_ROUTES[a] ? nav(ACTION_ROUTES[a]) : act.mutate(a));

  if (error) return <ErrorState error={error} retry={refetch} />;
  if (isLoading || !o) return <DashboardSkeleton />;

  const selected = acct !== "all" ? o.accounts.find((a: any) => String(a.id) === acct) : null;
  const k = o.kpis;
  const series = o.activity_series.map((d: any) => ({ ...d, sent: d.sent_hellos + d.sent_replies }));
  const sent30 = series.reduce((s: number, d: any) => s + d.sent, 0), got30 = series.reduce((s: number, d: any) => s + d.received, 0);
  const tasks = o.next_steps.filter((s: any) => s.kind !== "later");
  const later = o.next_steps.filter((s: any) => s.kind === "later");
  const name = (o.me.name || "").split(" ")[0] || "Luke";
  return (
    <div className="flex flex-col gap-6">
      {/* top */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-[13px] font-medium text-muted">{longDate()}</p>
          <h1 className="mt-1 text-[28px] font-semibold tracking-[-0.03em]">{greeting()}, {name}</h1>
          <p className="mt-1 text-[14px] text-muted">
            {selected ? <>Showing <b className="text-ink-2">@{selected.handle}</b> on {selected.platform_name}.</>
              : <>{plural(o.accounts.length, "account")} · {o.platforms.filter((p: any) => p.connected).length} of {o.platforms.length} platforms connected</>}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button icon={Download} onClick={() => act.mutate("load_download")} loading={act.isPending && act.variables === "load_download"} disabled={!!st?.job.name}>
            {o.practice ? "New practice download" : "Load download"}</Button>
          <Button variant="primary" icon={Hand} onClick={() => nav("/hello")}>Hello Message</Button>
        </div>
      </div>

      {selected && selected.role !== "main" ? <AccountFocus a={selected} /> : (<>
        {/* the headline numbers */}
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
          <Stat label="Followers" icon={Users} value={num(k.followers.value)} onClick={() => nav("/accounts")}
            sub={k.followers.value != null ? <>Instagram · from your download {ago(k.followers.as_of)}</> : undefined}
            unavailable={k.followers.value == null ? "Load an Instagram download to see this." : undefined} />
          <Stat label="Weekly follower growth" icon={TrendingUp} tone="good"
            value={k.followers.change_7d != null ? `${k.followers.change_7d >= 0 ? "+" : ""}${num(k.followers.change_7d)}` : undefined}
            sub="From two downloads a week apart"
            unavailable={k.followers.change_7d == null ? "Needs two downloads at least a week apart, or a platform connection." : undefined} />
          <Stat label="Total views" icon={Eye} tone="cyan" unavailable="Arrives when YouTube, Instagram or TikTok is connected (stages 4 and 5)." onClick={() => nav("/integrations")} />
          <Stat label="Engagement" icon={Activity} tone="cyan" unavailable="Likes, comments and shares come with the platform connections." onClick={() => nav("/integrations")} />
          <Stat label="Organic reach" icon={Target} tone="cyan" unavailable="Needs Instagram, TikTok or YouTube analytics (stages 4 and 5)." onClick={() => nav("/integrations")} />
        </div>

        <p className="-mt-3 text-[12px] text-faint sm:hidden">Views, engagement and reach arrive when the platforms are connected (stages 4 and 5).</p>

        {/* charts */}
        <div className="grid gap-4 xl:grid-cols-3">
          <Card className="xl:col-span-2" title="Conversations, last 30 days" icon={MessagesSquare}
            subtitle={`${num(sent30)} sent by Convo · ${num(got30)} received (from your downloads)`}
            action={<Button size="sm" variant="ghost" onClick={() => nav("/analytics")}>Analytics<ArrowRight className="size-3.5" /></Button>}>
            {sent30 + got30 ? (
              <AreaSeries data={series} keys={[{ key: "received", color: SERIES.cyan }, { key: "sent", color: SERIES.violet }]}
                names={{ received: "Received", sent: "Sent by Convo" }} />
            ) : <Empty icon={MessagesSquare} title="No messages in the last 30 days yet">Load a recent download, or send your first approved messages.</Empty>}
          </Card>
          <Card title="Platforms" icon={Plug} subtitle="Connected accounts and their followers">
            <div className="flex flex-col gap-2">
              {o.platforms.map((p: any) => (
                <div key={p.id} className="flex items-center gap-3 rounded-xl border border-line bg-bg-2 px-3 py-2.5">
                  <span className="size-2.5 rounded-full" style={{ background: p.color }} />
                  <span className="grow text-[14px]">{p.name}<span className="ml-2 text-[12px] text-faint">{plural(p.accounts, "account")}</span></span>
                  {p.followers != null ? <span className="tabular text-[14px] font-semibold">{compact(p.followers)}</span>
                    : p.connected ? <Badge tone="good">Connected</Badge> : <Badge tone="neutral">Stage {p.stage}</Badge>}
                </div>
              ))}
            </div>
            <FollowerHistory points={o.followers_series} />
          </Card>
        </div>

        {/* audience + this week */}
        <div className="grid gap-4 lg:grid-cols-3">
          <Card title="Hello Message" icon={Hand} action={<Button size="sm" variant="ghost" onClick={() => nav("/hello")}>Open<ArrowRight className="size-3.5" /></Button>}>
            <div className="grid grid-cols-3 gap-2">
              <Mini label="Waiting approval" value={compact(o.hello.waiting_approval)} />
              <Mini label="Sent" value={num(o.hello.sent)} />
              <Mini label="Reply rate" value={o.hello.reply_rate == null ? "—" : pct(o.hello.reply_rate)} hint={o.hello.reply_rate == null ? "after the first hellos" : `${o.hello.replied} replied`} />
            </div>
            <p className="mt-3 text-[12.5px] text-muted">Nothing in the queue is sent until you approve it.</p>
          </Card>
          <Card title="Conversations" icon={MessagesSquare} action={<Button size="sm" variant="ghost" onClick={() => nav("/conversations")}>Open<ArrowRight className="size-3.5" /></Button>}>
            <div className="grid grid-cols-3 gap-2">
              <Mini label="Waiting on you" value={num(k.waiting_on_you)} />
              <Mini label="Replies to check" value={num(o.replies.to_check)} />
              <Mini label="All chats" value={num(k.conversations)} />
            </div>
            <p className="mt-3 text-[12.5px] text-muted">{o.replies.writing ? `${o.replies.writing} being written overnight at half price.` : "Replies are written for you after each download."}</p>
          </Card>
          <Card title="This week" icon={CheckCircle2} subtitle="Missions with XP and streaks arrive in stage 6"
            action={<Button size="sm" variant="ghost" onClick={() => nav("/missions")}>Missions<ArrowRight className="size-3.5" /></Button>}>
            {(() => { const top = Math.max(k.sent_7d, k.received_7d, o.hello.sent_7d, 1); return <>
              <WeekBar label="Messages sent" value={k.sent_7d} goal={top} />
              <WeekBar label="Messages received" value={k.received_7d} goal={top} />
              <WeekBar label="Hellos sent" value={o.hello.sent_7d} goal={top} /></>; })()}
          </Card>
        </div>

        {/* lower */}
        <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-4">
          <Card title="Outstanding tasks" icon={ListTodo} className="xl:col-span-2">
            {tasks.length ? (
              <div className="flex flex-col gap-2">{tasks.map((t: any) => (
                <button key={t.title} onClick={() => run(t.action)}
                  className="group flex items-center gap-3 rounded-xl border border-line bg-bg-2 px-3.5 py-3 text-left hover:border-line-2 hover:bg-raised">
                  <CircleDashed className="size-4 shrink-0 text-violet-2" />
                  <span className="min-w-0 grow"><span className="block text-[14px] font-medium text-ink">{t.title}</span>
                    <span className="block text-[12.5px] text-muted">{t.detail}</span></span>
                  <ArrowRight className="size-4 shrink-0 text-faint transition-transform group-hover:translate-x-0.5" />
                </button>))}</div>
            ) : <Empty icon={CheckCircle2} title="All caught up">Nothing needs you right now.</Empty>}
          </Card>
          <Card title="Upcoming" icon={CalendarClock}>
            <Upcoming st={st} />
          </Card>
          <Card title="Recommendations" icon={Lightbulb} subtitle="Worked out from your data">
            <div className="flex flex-col gap-2.5">
              {later.slice(0, 3).map((t: any) => (
                <button key={t.title} onClick={() => run(t.action)} className="text-left">
                  <span className="block text-[13.5px] font-medium text-ink hover:text-violet-2">{t.title}</span>
                  <span className="block text-[12px] leading-snug text-muted">{t.detail}</span>
                </button>))}
              <Button size="sm" variant="secondary" icon={Sparkles} className="mt-1 self-start" onClick={() => nav("/strategist")}>Ask the AI Strategist</Button>
            </div>
          </Card>
        </div>

        <Card title="Recent activity" icon={Activity} action={<Button size="sm" variant="ghost" onClick={() => nav("/notifications")}>See all<ArrowRight className="size-3.5" /></Button>}>
          {o.recent.length ? <div className="divide-y divide-line">{o.recent.map((i: any) => <ActivityRow key={i.id} item={i} />)}</div>
            : <Empty title="Nothing yet" icon={Activity}>Activity shows up here as Convo works.</Empty>}
        </Card>
      </>)}
    </div>
  );
}

function Mini({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="rounded-xl border border-line bg-bg-2 px-3 py-2.5">
      <div className="tabular text-[20px] font-semibold tracking-[-0.02em]">{value}</div>
      <div className="text-[11.5px] leading-tight text-muted">{label}</div>
      {hint && <div className="mt-0.5 text-[11px] text-faint">{hint}</div>}
    </div>
  );
}

function WeekBar({ label, value, goal }: { label: string; value: number; goal: number }) {
  const w = Math.min(100, (100 * value) / goal);
  return (
    <div className="mb-3 last:mb-0">
      <div className="mb-1.5 flex justify-between text-[13px]"><span className="text-ink-2">{label}</span><span className="tabular font-semibold">{num(value)}</span></div>
      <div className="h-1.5 overflow-hidden rounded-full bg-hover"><div className="h-full rounded-full bg-gradient-to-r from-violet to-cyan" style={{ width: `${w}%` }} /></div>
    </div>
  );
}

function FollowerHistory({ points }: { points: { at: string; followers: number | null }[] }) {
  const have = points.filter((p) => p.followers != null);
  return (
    <div className="mt-4 rounded-xl border border-dashed border-line-2 px-3 py-3 text-[12.5px] text-muted">
      {have.length >= 2
        ? <AreaSeries height={110} data={have.map((p) => ({ date: p.at, followers: p.followers }))} keys={[{ key: "followers", color: SERIES.violet }]} names={{ followers: "Followers" }} />
        : <>Follower history: {have.length ? "1 measurement so far" : "none yet"}. Each download adds a point, so the growth line fills in over time.</>}
    </div>
  );
}

function Upcoming({ st }: { st: any }) {
  const nav = useNavigate();
  if (!st) return <Skeleton className="h-24" />;
  const items: { icon: any; title: string; detail: string; to: string }[] = [];
  if (st.counts.approved) items.push({ icon: Send, title: `${plural(st.counts.approved, "message")} approved`,
    detail: st.settings.send_at ? `Sends by itself at ${st.settings.send_at}` : "Sends when you press Send", to: "/hello" });
  if (st.overnight) items.push({ icon: Sparkles, title: `${plural(st.overnight.count, "reply", "replies")} being written`, detail: "At half price, usually within the hour", to: "/conversations" });
  if (st.settings.quiet_from && st.settings.quiet_to) items.push({ icon: CalendarClock, title: "Quiet hours", detail: `${st.settings.quiet_from} to ${st.settings.quiet_to}, nothing is sent`, to: "/automations" });
  return items.length ? (
    <div className="flex flex-col gap-3">{items.map((i) => (
      <button key={i.title} onClick={() => nav(i.to)} className="flex items-start gap-3 text-left">
        <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-cyan-soft text-cyan"><i.icon className="size-4" /></span>
        <span><span className="block text-[13.5px] font-medium">{i.title}</span><span className="block text-[12px] text-muted">{i.detail}</span></span>
      </button>))}
      <p className="text-[12px] text-faint">Scheduled posts join this list in stage 3.</p>
    </div>
  ) : <Empty icon={CalendarClock} title="Nothing scheduled">Approved messages and, from stage 3, scheduled posts appear here.</Empty>;
}

function AccountFocus({ a }: { a: any }) {
  const nav = useNavigate();
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-4">
        <Avatar name={a.handle} size={52} />
        <div className="min-w-0 grow"><div className="text-[18px] font-semibold">@{a.handle}</div>
          <div className="text-[13px] text-muted">{a.platform_name} · {a.role === "launch" ? "Launch network" : "Other account"}{a.niche ? ` · ${a.niche}` : ""}</div>
          {a.purpose && <p className="mt-2 text-[14px] text-ink-2">{a.purpose}</p>}</div>
        <Button onClick={() => nav(a.role === "launch" ? "/launch" : "/accounts")}>Manage</Button>
      </div>
      <div className="mt-5 grid gap-3 sm:grid-cols-3">
        <Stat label="Followers" icon={Users} unavailable={a.status_note || "Not connected yet"} />
        <Stat label="Views" icon={Eye} tone="cyan" unavailable="Arrives with the platform connection" />
        <Stat label="Referrals to your main account" icon={TrendingUp} tone="good" unavailable={a.referral_link ? "Tracked from the link in this account's bio (stage 6)" : "Add a tracking link to this account"} />
      </div>
    </Card>
  );
}

function DashboardSkeleton() {
  return (
    <div className="flex flex-col gap-6">
      <Skeleton className="h-16 w-80" />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">{[...Array(5)].map((_, i) => <Skeleton key={i} className="h-[112px]" />)}</div>
      <div className="grid gap-4 xl:grid-cols-3"><Skeleton className="h-[320px] xl:col-span-2" /><Skeleton className="h-[320px]" /></div>
    </div>
  );
}
