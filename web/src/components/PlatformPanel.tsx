// Instagram's and TikTok's real numbers once connected: followers, posts and their views, likes, comments and shares,
// and (Instagram) reach by day.
import { useQuery } from "@tanstack/react-query";
import { Eye, Heart, Image, Users } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import { PLATFORM, PlatformId } from "../lib/content";
import { ago, num } from "../lib/format";
import { Stat } from "../pages/Dashboard";
import { AreaSeries, SERIES } from "./charts";
import { ConnectDrawer } from "./Connect";
import { Button, Card } from "./ui";

export function PlatformPanel({ pid }: { pid: Exclude<PlatformId, "youtube"> }) {
  const { data } = useQuery({ queryKey: ["platform", pid], queryFn: () => api(`/api/connect/${pid}/overview`), refetchInterval: 60000 });
  const [setup, setSetup] = useState(false);
  const P = PLATFORM[pid];
  if (!data) return null;
  if (!data.account) return (
    <Card title={P.name} subtitle={pid === "instagram" ? "Followers, reach, views, likes, saves and shares" : "Followers, likes and each video's views"}>
      <div className="flex flex-wrap items-center justify-between gap-3"><p className="text-[13.5px] text-muted">Connect {P.name} to see its real numbers here (about 15 minutes, once).</p>
        <Button variant="primary" onClick={() => setSetup(true)}>Connect {P.name}</Button></div>
      <ConnectDrawer pid={pid} open={setup} onClose={() => setSetup(false)} />
    </Card>
  );
  const recent: any[] = data.recent || [];
  const views = recent.reduce((n, p) => n + (p.views || 0), 0);
  const likes = recent.reduce((n, p) => n + (p.likes || 0), 0);
  const reachDays = (data.days || []).filter((d: any) => d.reach != null);
  return (
    <div className="grid gap-4">
      <div className="flex items-center gap-2"><span className="size-2.5 rounded-full" style={{ background: P.color }} /><h2 className="text-[16px] font-semibold">{P.name} · @{data.account.handle}</h2>
        <span className="text-[12.5px] text-faint">updated {ago(data.last_sync)}</span></div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Followers" icon={Users} tone="cyan" value={num(data.followers ?? 0)} sub={`Following ${num(data.following ?? 0)}`} />
        <Stat label={`Views, last ${recent.length} posts`} icon={Eye} value={num(views)} sub="As the platform reports them" />
        <Stat label={`Likes, last ${recent.length} posts`} icon={Heart} tone="good" value={num(likes)} sub={data.likes_total != null ? `${num(data.likes_total)} all time` : "Across those posts"} />
        <Stat label="Posts" icon={Image} tone="violet" value={num(data.posts ?? 0)} sub="On the account" />
      </div>
      {reachDays.length > 0 && <Card title="Reach by day" subtitle="Accounts that saw your posts, last 28 days">
        <AreaSeries data={reachDays} keys={[{ key: "reach", color: SERIES.cyan }]} names={{ reach: "Reach" }} height={200} /></Card>}
      <Card title="Latest posts" bodyClass="!p-0">
        <div className="divide-y divide-line">{recent.map((v) => (
          <a key={v.post_id} href={v.url} target="_blank" rel="noreferrer" className="flex items-center gap-3 px-5 py-3 hover:bg-hover/40">
            <span className="size-12 shrink-0 overflow-hidden rounded-lg bg-bg-2">{v.thumbnail && <img src={v.thumbnail} alt="" className="size-full object-cover" referrerPolicy="no-referrer" />}</span>
            <span className="min-w-0 grow"><span className="block truncate text-[14px] font-medium">{v.title || "(no caption)"}</span><span className="text-[12px] text-faint">{ago(v.published_at)}</span></span>
            <span className="hidden shrink-0 gap-4 text-[12.5px] tabular text-muted sm:flex">
              {v.views != null && <span>{num(v.views)} views</span>}<span>{num(v.likes ?? 0)} likes</span><span>{num(v.comments ?? 0)} comments</span>{v.shares != null && <span>{num(v.shares)} shares</span>}</span>
          </a>))}</div>
      </Card>
    </div>
  );
}
