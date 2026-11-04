import { useInfiniteQuery } from "@tanstack/react-query";
import { Bell, History } from "lucide-react";
import { useState } from "react";
import { ActivityRow } from "../components/Shell";
import { Button, Card, Empty, ErrorState, PageHeader, Segmented, Skeleton } from "../components/ui";
import { api } from "../lib/api";

function Feed({ kinds }: { kinds: string }) {
  const q = useInfiniteQuery({
    queryKey: ["activity", kinds], initialPageParam: 0,
    queryFn: ({ pageParam }) => api(`/api/hub/activity?kind=${kinds}&limit=50${pageParam ? `&before=${pageParam}` : ""}`),
    getNextPageParam: (last: any) => (last.items.length === 50 ? last.items[last.items.length - 1].id : undefined),
  });
  if (q.error) return <ErrorState error={q.error} retry={q.refetch} />;
  if (q.isLoading) return <Skeleton className="h-72" />;
  const items = q.data!.pages.flatMap((p: any) => p.items);
  return (
    <Card>
      {items.length ? <div className="divide-y divide-line">{items.map((i: any) => <ActivityRow key={i.id} item={i} />)}</div>
        : <Empty icon={Bell} title="Nothing here">Activity appears as Convo loads downloads, writes replies and sends.</Empty>}
      {q.hasNextPage && <div className="mt-3 flex justify-center"><Button loading={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>Show older</Button></div>}
    </Card>
  );
}

export function Notifications() {
  const [show, setShow] = useState<"" | "warn,problem" | "good">("");
  return (
    <div>
      <PageHeader title="Notifications" subtitle="Everything Convo has done and anything that needs you." />
      <Segmented className="mb-4" value={show} onChange={setShow} options={[["", "Everything"], ["warn,problem", "Needs attention"], ["good", "Done"]]} />
      <Feed kinds={show} />
    </div>
  );
}

export function AutomationHistory() {
  return (
    <div>
      <PageHeader title="Automation History" subtitle="Every run, result and problem, newest first. Each automation in the Automation Centre also shows its last run." />
      <div className="mb-3 flex items-center gap-2 text-[13px] text-muted"><History className="size-4" />Includes sending, overnight writing, downloads, self-checks and anything that stopped.</div>
      <Feed kinds="" />
    </div>
  );
}
