// Pages whose main feature arrives in a later stage. Each says exactly what's coming and what it needs, and shows
// the real data that already exists for it. No fake buttons.
import { useQuery } from "@tanstack/react-query";
import { Handshake, Sprout, Star, Trophy } from "lucide-react";
import { useState } from "react";
import { Avatar, Badge, Card, Empty, PageHeader, Planned, Skeleton } from "../components/ui";
import { api } from "../lib/api";
import { num } from "../lib/format";
import { PersonDrawer } from "./Contacts";

export function EngagementAssistant() {
  return (<div><PageHeader title="Engagement Assistant" subtitle="Comments worth answering, with replies drafted for your approval." />
    <Planned stage={5} title="Comments, in one place" what={["New comments on YouTube and Instagram", "Replies drafted in your voice", "You approve each one",
      "Notifications for the ones that matter"]} needs={["YouTube connection (stage 4)", "Instagram Creator account and Meta app (stage 5)", "TikTok has no comment API"]} /></div>);
}

export function Collaborations() {
  const [person, setPerson] = useState<string | null>(null);
  const { data, isLoading } = useQuery({ queryKey: ["people", "collab"], queryFn: () => api("/api/people?show=interesting&sort=interest&limit=30") });
  const items: any[] = data?.items || [];
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Collaboration Opportunities" subtitle="People you'd probably click with, from your real conversations." />
      <Card title="People you'd click with" icon={Star} subtitle="Rated by the AI from your chats and your About me">
        {isLoading ? <Skeleton className="h-40" /> : items.length ? (
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">{items.map((p) => (
            <button key={p.username} onClick={() => setPerson(p.username)} className="flex items-start gap-3 rounded-xl border border-line bg-bg-2 px-3 py-2.5 text-left hover:border-line-2">
              <Avatar name={p.display_name || p.username} size={34} />
              <span className="min-w-0"><span className="flex items-center gap-2"><span className="truncate text-[13.5px] font-medium">@{p.username}</span>
                {p.interest >= 3 && <Badge tone="good">You'd click</Badge>}</span>
                <span className="line-clamp-2 text-[12px] text-muted">{p.interest_why || p.summary}</span></span>
            </button>))}</div>
        ) : <Empty icon={Handshake} title="None yet">After replies are written, people with real common ground appear here.</Empty>}
      </Card>
      <Planned stage={6} title="Collaboration tracking" what={["Mark someone as a collaboration idea", "Track where each one is up to", "Follow-up reminders",
        "Creators in your niche, where the platforms allow it"]} />
      <PersonDrawer username={person} onClose={() => setPerson(null)} />
    </div>
  );
}

export function WeeklyMissions() {
  const { data } = useQuery({ queryKey: ["overview"], queryFn: () => api("/api/hub/overview") });
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Weekly Missions" subtitle="Goals for the week, tracked automatically from real activity." />
      <Card title="This week so far" icon={Trophy} subtitle="Counted from what really happened">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          {[["Messages sent", data?.kpis.sent_7d], ["Messages received", data?.kpis.received_7d], ["Hellos sent", data?.hello.sent_7d],
            ["Chats waiting on you", data?.kpis.waiting_on_you]].map(([l, v]) => (
            <div key={l as string} className="rounded-xl border border-line bg-bg-2 px-4 py-3"><div className="tabular text-[22px] font-semibold">{num(v as number)}</div>
              <div className="text-[12px] text-muted">{l}</div></div>))}
        </div>
      </Card>
      <Planned stage={6} title="Missions, XP and streaks" what={["Weekly missions like “start 10 meaningful conversations”", "Your own missions and targets",
        "XP, badges, streaks and history", "Progress filled in automatically, only from genuine activity"]} />
    </div>
  );
}

export function GrowthGoals() {
  return (<div><PageHeader title="Growth Goals" subtitle="Longer-term targets, with progress from real numbers." />
    <Planned stage={6} title="Goals you set, measured honestly" what={["Followers, views or conversations by a date", "Progress from your connected platforms",
      "Whether you're on track, and why"]} needs={["Platform connections (stages 4 and 5)"]} /></div>);
}

export function OrganicIndependence() {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Organic Independence" subtitle="How self-sustaining your main brand is becoming, so you know when to rely on the launch network less." />
      <Planned stage={6} title="The Organic Independence Score" what={["Discovery from recommendations and search", "Returning viewers", "Genuine comments and conversations",
        "Consistent content performance", "Direct and outside referrals", "A clear recommendation on the launch network"]}
        needs={["YouTube traffic sources (the richest source)", "Instagram views and reach", "Tracking links on launch accounts"]} />
      <Card title="How it will be worked out" icon={Sprout}>
        <p className="text-[13.5px] text-ink-2">Each part of the score uses only numbers a platform really gives. YouTube shares where viewers came from; Instagram and
          TikTok share less. The score shows which parts it could measure, so a missing number never looks like a bad one.</p>
      </Card>
    </div>
  );
}
