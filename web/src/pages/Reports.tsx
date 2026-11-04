import { useMutation, useQuery } from "@tanstack/react-query";
import { Download, FileSearch, FileText, ShieldCheck, Sparkles } from "lucide-react";
import { useState } from "react";
import { Avatar, Button, Card, Empty, PageHeader, Planned, Skeleton, useToast } from "../components/ui";
import { api, download, post } from "../lib/api";
import { useAppState } from "../lib/state";
import { PersonDrawer } from "./Contacts";

export default function Reports() {
  const toast = useToast();
  const st = useAppState().data;
  const [person, setPerson] = useState<string | null>(null);
  const check = useQuery({ queryKey: ["check", st?.check.at], queryFn: () => api("/api/check"), enabled: !!st?.check.at });
  const dl = useMutation({ mutationFn: () => post("/api/download/report"), onError: (e) => toast((e as Error).message, "bad") });
  const people = useQuery({ queryKey: ["people", "reports"], queryFn: () => api("/api/people?show=chats&sort=interest&limit=200") });
  const withReports = (people.data?.items || []).filter((p: any) => p.summary);
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Reports" subtitle="Checks you can run any time, and what Convo has learned about people."
        actions={<Button icon={Download} onClick={() => download("/api/people.csv", "convo-people.csv").catch((e) => toast(e.message, "bad"))}>People spreadsheet</Button>} />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Self-check" icon={ShieldCheck} subtitle={st?.check.at ? `Last run ${new Date(st.check.at).toLocaleString()}` : "Not run yet: start it in Account Settings"}>
          {check.data?.report ? <pre className="max-h-80 overflow-auto rounded-xl border border-line bg-bg-2 p-4 text-[12px] leading-relaxed whitespace-pre-wrap text-ink-2">{check.data.report.split("\n\n== ")[0]}</pre>
            : <Empty icon={ShieldCheck} title="No check yet">It tries every Instagram step without sending anything.</Empty>}
        </Card>
        <Card title="What's in my download" icon={FileSearch} subtitle="Kinds of messages in your newest download, with no words shown. Free."
          action={<Button size="sm" loading={dl.isPending} onClick={() => dl.mutate()}>Check now</Button>}>
          {dl.data ? <pre className="max-h-80 overflow-auto rounded-xl border border-line bg-bg-2 p-4 text-[12px] leading-relaxed whitespace-pre-wrap text-ink-2">{(dl.data as any).report}</pre>
            : <Empty icon={FileText} title="Press Check now">Handy to send to Claude if something looks off.</Empty>}
        </Card>
      </div>
      <Card title="People Convo has written for" icon={Sparkles} subtitle="Click anyone for their full report, chat and notes">
        {people.isLoading ? <Skeleton className="h-40" /> : withReports.length ? (
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">{withReports.map((p: any) => (
            <button key={p.username} onClick={() => setPerson(p.username)} className="flex items-start gap-3 rounded-xl border border-line bg-bg-2 px-3 py-2.5 text-left hover:border-line-2">
              <Avatar name={p.display_name || p.username} size={32} /><span className="min-w-0"><span className="block truncate text-[13.5px] font-medium">@{p.username}</span>
                <span className="line-clamp-2 text-[12px] text-muted">{p.summary}</span></span></button>))}</div>
        ) : <Empty icon={Sparkles} title="No summaries yet">After replies are written, each person gets a short summary here.</Empty>}
      </Card>
      <Planned stage={6} title="Weekly performance reports" what={["A plain summary every Monday", "Best and worst content", "Growth by platform", "Launch network results"]}
        needs={["Platform connections (stages 4 and 5)"]} />
      <PersonDrawer username={person} onClose={() => setPerson(null)} />
    </div>
  );
}
