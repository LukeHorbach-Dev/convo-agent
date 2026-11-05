import { useQuery } from "@tanstack/react-query";
import { BarChart3, Hand, MessagesSquare, Star, Users } from "lucide-react";
import { useState } from "react";
import { AreaSeries, Bars, SERIES } from "../components/charts";
import { Avatar, Badge, Card, Empty, ErrorState, PageHeader, Segmented, Skeleton } from "../components/ui";
import { api } from "../lib/api";
import { num, pct } from "../lib/format";
import { PlatformPanel } from "../components/PlatformPanel";
import { YouTubePanel } from "../components/YouTubePanel";
import { Stat } from "./Dashboard";

export default function Analytics() {
  const [days, setDays] = useState<"7" | "30" | "90">("30");
  const { data, isLoading, error, refetch } = useQuery({ queryKey: ["analytics", days], queryFn: () => api(`/api/hub/analytics?days=${days}`) });
  if (error) return <ErrorState error={error} retry={refetch} />;
  const s = data?.series || [];
  const sent = s.reduce((n: number, d: any) => n + d.sent_hellos + d.sent_replies, 0), got = s.reduce((n: number, d: any) => n + d.received, 0);
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Analytics" subtitle="What's real today: your Instagram audience and conversations. Views, reach and engagement join as platforms connect."
        actions={<Segmented value={days} onChange={setDays} options={[["7", "7 days"], ["30", "30 days"], ["90", "90 days"]]} />} />
      {isLoading || !data ? <Skeleton className="h-96" /> : (<>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat label="Following" icon={Users} value={num(data.relationships.following)} sub="From your latest download" />
          <Stat label="Followers" icon={Users} tone="cyan" value={num(data.relationships.followers)} sub={`${num(data.relationships.mutual)} mutual`} />
          <Stat label={`Messages, ${days} days`} icon={MessagesSquare} tone="good" value={num(sent + got)} sub={`${num(sent)} sent · ${num(got)} received`} />
          <Stat label="Hello reply rate" icon={Hand} tone="violet" value={data.hello.reply_rate == null ? "—" : pct(data.hello.reply_rate)}
            sub={`${num(data.hello.replied)} of ${num(data.hello.sent)} wrote back`} />
        </div>
        <div className="grid gap-4 xl:grid-cols-3">
          <Card className="xl:col-span-2" title="Messages per day" icon={MessagesSquare} subtitle="Sent by Convo, and received (from your downloads)">
            {sent + got ? <Bars data={s} keys={[{ key: "received", color: SERIES.cyan }, { key: "sent_replies", color: SERIES.violet }, { key: "sent_hellos", color: "#c4b5fd" }]}
              names={{ received: "Received", sent_replies: "Replies sent", sent_hellos: "Hellos sent" }} height={260} />
              : <Empty icon={MessagesSquare} title="No messages in this period" />}
          </Card>
          <Card title="Who you talk to" icon={Star} subtitle="How the AI rated people it has written for">
            {(["3", "2", "1", "0"] as const).map((k) => {
              const total = Object.values(data.interest).reduce((a: number, b: any) => a + b, 0) as number;
              const v = data.interest[k] as number;
              const label = { "3": "You'd click", "2": "Interesting", "1": "Neutral", "0": "Not much there" }[k];
              return <div key={k} className="mb-3 last:mb-0"><div className="mb-1 flex justify-between text-[13px]"><span className="text-ink-2">{label}</span><span className="tabular">{num(v)}</span></div>
                <div className="h-1.5 rounded-full bg-hover"><div className="h-full rounded-full bg-gradient-to-r from-violet to-cyan" style={{ width: total ? `${(100 * v) / total}%` : 0 }} /></div></div>;
            })}
          </Card>
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          <Card title="Most active conversations" icon={MessagesSquare} subtitle={`Last ${days} days`}>
            {data.top_conversations.length ? data.top_conversations.map((t: any) => (
              <div key={t.username || t.other_name} className="flex items-center gap-3 border-b border-line py-2.5 last:border-0">
                <Avatar name={t.other_name || t.username} size={32} />
                <span className="min-w-0 grow truncate text-[14px]">{t.other_name || t.username}{t.username && <span className="ml-2 text-[12px] text-muted">@{t.username}</span>}</span>
                <span className="tabular text-[13px] text-muted">{num(t.messages)} msgs</span>
              </div>)) : <Empty title="No conversations in this period" />}
          </Card>
          <Card title="Followers over time" icon={BarChart3} subtitle="One point per Instagram download">
            {data.followers.filter((f: any) => f.followers != null).length >= 2
              ? <AreaSeries data={data.followers.map((f: any) => ({ date: f.at, followers: f.followers }))} keys={[{ key: "followers", color: SERIES.violet }]} names={{ followers: "Followers" }} height={220} />
              : <Empty icon={BarChart3} title="Not enough points yet">Each download adds one. With two or more, the line appears here.</Empty>}
          </Card>
        </div>
        <Card title="Which numbers the platforms make available" subtitle="Convo only shows numbers a platform actually gives. Nothing here is estimated.">
          <div className="overflow-x-auto"><table className="w-full min-w-[560px] text-[13.5px]">
            <thead><tr className="border-b border-line text-left text-[11.5px] uppercase tracking-wide text-faint">
              <th className="py-2 pr-3 font-semibold">Metric</th><th className="py-2 pr-3 font-semibold">Instagram</th><th className="py-2 pr-3 font-semibold">TikTok</th><th className="py-2 font-semibold">YouTube</th></tr></thead>
            <tbody>{data.availability.map((r: any) => (
              <tr key={r.metric} className="border-b border-line/60 last:border-0"><td className="py-2.5 pr-3 text-ink-2">{r.metric}</td>
                {["instagram", "tiktok", "youtube"].map((p) => <td key={p} className="py-2.5 pr-3">
                  <Badge tone={r[p] === "API" ? "good" : r[p] === "Not available" ? "neutral" : "warn"}>{r[p]}</Badge></td>)}</tr>))}</tbody>
          </table></div>
        </Card>
      <PlatformPanel pid="instagram" />
      <PlatformPanel pid="tiktok" />
      <YouTubePanel />
      </>)}
    </div>
  );
}
