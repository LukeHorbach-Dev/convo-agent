// YouTube's real numbers, once it's connected: subscribers, views and watch time by day, where views came from,
// and the latest videos.
import { useQuery } from "@tanstack/react-query";
import { Eye, PlayCircle, Timer, Users } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import { ago, num } from "../lib/format";
import { Stat } from "../pages/Dashboard";
import { AreaSeries, SERIES } from "./charts";
import { ConnectDrawer } from "./Connect";
import { Button, Card } from "./ui";

const SOURCE: Record<string, string> = { YT_SEARCH: "YouTube search", RELATED_VIDEO: "Suggested videos", SUBSCRIBER: "Subscriptions feed", BROWSE: "Home and browse",
  EXT_URL: "Other websites", NO_LINK_OTHER: "Direct or unknown", PLAYLIST: "Playlists", SHORTS: "Shorts feed", NOTIFICATION: "Notifications", YT_CHANNEL: "Your channel page",
  END_SCREEN: "End screens", YT_OTHER_PAGE: "Other YouTube pages", ADVERTISING: "Ads", HASHTAGS: "Hashtags", SOUND_PAGE: "Sound pages" };

export function useYouTube() {
  return useQuery({ queryKey: ["yt"], queryFn: () => api("/api/connect/youtube/overview"), refetchInterval: 60000 });
}

export function YouTubePanel() {
  const { data } = useYouTube();
  const [setup, setSetup] = useState(false);
  if (!data) return null;
  if (!data.channel) return (
    <Card title="YouTube" subtitle="Subscribers, views, watch time and where viewers come from">
      <div className="flex flex-wrap items-center justify-between gap-3"><p className="text-[13.5px] text-muted">Connect your channel to see its real numbers here (about 10 minutes, once).</p>
        <Button variant="primary" onClick={() => setSetup(true)}>Connect YouTube</Button></div>
      <ConnectDrawer pid="youtube" open={setup} onClose={() => setSetup(false)} />
    </Card>
  );
  const views = data.days.reduce((n: number, d: any) => n + (d.views || 0), 0);
  const minutes = data.days.reduce((n: number, d: any) => n + (d.estimatedMinutesWatched || 0), 0);
  const gained = data.days.reduce((n: number, d: any) => n + (d.subscribersGained || 0) - (d.subscribersLost || 0), 0);
  const src = data.traffic?.sources || [];
  const srcTotal = src.reduce((n: number, s: any) => n + s.views, 0) || 1;
  return (
    <div className="grid gap-4">
      <div className="flex items-center gap-2"><span className="size-2.5 rounded-full bg-[#ff3d3d]" /><h2 className="text-[16px] font-semibold">YouTube · {data.channel.name}</h2>
        <span className="text-[12.5px] text-faint">updated {ago(data.last_sync)}</span></div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Subscribers" icon={Users} tone="cyan" value={data.subscribers == null ? "Hidden" : num(data.subscribers)} sub={`${gained >= 0 ? "+" : ""}${num(gained)} in 28 days`} />
        <Stat label="Views, 28 days" icon={Eye} value={num(views)} sub={`${num(data.views_total || 0)} all time`} />
        <Stat label="Watch time, 28 days" icon={Timer} tone="good" value={`${num(Math.round(minutes / 60))} h`} sub={`${num(minutes)} minutes`} />
        <Stat label="Videos" icon={PlayCircle} tone="violet" value={num(data.videos || 0)} sub="On the channel" />
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <Card title="Views by day" subtitle="Last 28 days, from YouTube Analytics">
          {data.days.length ? <AreaSeries data={data.days} keys={[{ key: "views", color: SERIES.violet }]} names={{ views: "Views" }} height={220} />
            : <p className="text-[13px] text-muted">YouTube Analytics hasn't sent daily numbers yet (new channels can take a couple of days).</p>}
        </Card>
        <Card title="Where views came from" subtitle="Last 28 days">
          {src.length ? <div className="grid gap-2.5">{src.slice(0, 7).map((s: any) => (
            <div key={s.source}><div className="flex justify-between text-[13px]"><span className="text-ink-2">{SOURCE[s.source] || s.source}</span><span className="tabular text-muted">{num(s.views)}</span></div>
              <div className="mt-1 h-1.5 rounded-full bg-bg-2"><div className="h-full rounded-full bg-gradient-to-r from-violet to-cyan" style={{ width: `${(s.views / srcTotal) * 100}%` }} /></div></div>))}</div>
            : <p className="text-[13px] text-muted">No traffic sources yet.</p>}
        </Card>
      </div>
      <Card title="Latest videos" bodyClass="!p-0">
        <div className="divide-y divide-line">{data.recent.map((v: any) => (
          <a key={v.post_id} href={v.url} target="_blank" rel="noreferrer" className="flex items-center gap-3 px-5 py-3 hover:bg-hover/40">
            <span className="h-12 w-20 shrink-0 overflow-hidden rounded-lg bg-bg-2">{v.thumbnail && <img src={v.thumbnail} alt="" className="size-full object-cover" referrerPolicy="no-referrer" />}</span>
            <span className="min-w-0 grow"><span className="block truncate text-[14px] font-medium">{v.title}</span><span className="text-[12px] text-faint">{ago(v.published_at)}</span></span>
            <span className="hidden shrink-0 gap-4 text-[12.5px] tabular text-muted sm:flex"><span>{num(v.views ?? 0)} views</span><span>{num(v.likes ?? 0)} likes</span><span>{num(v.comments ?? 0)} comments</span></span>
          </a>))}</div>
      </Card>
    </div>
  );
}
