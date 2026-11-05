// Content Library: every post, idea and file in one place.
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Library as LibraryIcon, Plus, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Card, Empty, ErrorState, Input, PageHeader, Segmented, Skeleton } from "../components/ui";
import { api } from "../lib/api";
import { Content, PLATFORM, PLATFORM_IDS, PlatformId, Status } from "../lib/content";
import { MiniCard } from "./Studio";

type Show = "" | "idea" | "draft" | "ready" | "scheduled,due" | "posted";

export default function Library() {
  const nav = useNavigate();
  const [show, setShow] = useState<Show>("");
  const [platform, setPlatform] = useState<PlatformId | "">("");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  useEffect(() => { const t = setTimeout(() => setQuery(q.trim()), 250); return () => clearTimeout(t); }, [q]);
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["content", "library", show, platform, query], placeholderData: keepPreviousData,
    queryFn: () => api<{ items: Content[]; total: number; counts: Record<Status, number> }>(
      `/api/hub/content?status=${encodeURIComponent(show)}&platform=${platform}&q=${encodeURIComponent(query)}&limit=300`),
  });
  const n = data?.counts;
  return (
    <div>
      <PageHeader title="Content Library" subtitle="Your ideas, drafts, videos and photos, kept on this Mac." actions={<Button variant="primary" icon={Plus} onClick={() => nav("/studio")}>New post</Button>} />
      <Card bodyClass="!p-0">
        <div className="flex flex-wrap items-center gap-3 border-b border-line p-4">
          <div className="relative min-w-[200px] grow"><Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-faint" />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search titles and notes" className="pl-9" aria-label="Search the library" /></div>
          <Segmented<Show> value={show} onChange={setShow} options={[["", "All"], ["idea", `Ideas${n ? ` (${n.idea})` : ""}`], ["draft", `Drafts${n ? ` (${n.draft})` : ""}`],
            ["ready", `Ready${n ? ` (${n.ready})` : ""}`], ["scheduled,due", `Scheduled${n ? ` (${n.scheduled + n.due})` : ""}`], ["posted", `Posted${n ? ` (${n.posted})` : ""}`]]} />
          <div className="flex gap-1.5">{PLATFORM_IDS.map((p) => (
            <button key={p} onClick={() => setPlatform(platform === p ? "" : p)} aria-pressed={platform === p} title={PLATFORM[p].name}
              className="flex h-9 items-center gap-1.5 rounded-xl border px-2.5 text-[12.5px]" style={{ borderColor: platform === p ? PLATFORM[p].color : "var(--color-line)" }}>
              <span className="size-2 rounded-full" style={{ background: PLATFORM[p].color }} />{PLATFORM[p].short}</button>))}</div>
        </div>
        <div className="p-4">
          {error ? <ErrorState error={error} retry={refetch} /> : isLoading ? <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{[...Array(8)].map((_, i) => <Skeleton key={i} className="h-52" />)}</div>
            : data?.items.length ? <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">{data.items.map((c) => <MiniCard key={c.id} c={c} />)}</div>
            : <Empty icon={LibraryIcon} title={query ? "Nothing matches" : "Your library is empty"} action={<Button variant="primary" icon={Plus} onClick={() => nav("/studio")}>Start a post</Button>}>
                Ideas, drafts and files you add in the Content Studio show up here.</Empty>}
        </div>
      </Card>
    </div>
  );
}
