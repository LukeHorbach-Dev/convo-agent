// What Convo is doing right now, and the Stop now / Start again control. Shown in the top bar on every page, so it's
// always clear whether anything is running.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CircleCheck, Loader2, Moon, Play, Square } from "lucide-react";
import { useState } from "react";
import { post } from "../lib/api";
import { AppState, useAppState } from "../lib/state";
import { useClickAway } from "./Shell";
import { Button, cx, Dot, useConfirm, useToast } from "./ui";

export type RunKind = "running" | "stopped" | "waiting" | "idle";
export type Run = { kind: RunKind; headline: string; lines: string[] };

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const plural = (n: number, one: string, many = one + "s") => `${n.toLocaleString()} ${n === 1 ? one : many}`;

export function describeRun(st?: AppState): Run {
  if (!st) return { kind: "idle", headline: "Checking…", lines: [] };
  const lines: string[] = [];
  const approved = st.counts.approved || 0;
  if (st.sender.active) lines.push(`${st.sender.status}${approved ? ` · ${plural(approved, "approved message")} left` : ""}`);
  if (st.job.name) lines.push(`${cap(st.job.name)}${st.job.total ? ` · ${st.job.done} of ${st.job.total}` : "…"}`);
  if (st.check.running) lines.push("Checking each Instagram step (sends nothing)");
  if (lines.length) return { kind: "running", headline: lines[0], lines };

  const later: string[] = [];
  if (st.overnight) later.push(`${plural(st.overnight.count, "reply", "replies")} being written overnight (half price)`);
  if (st.sender.stopped) {
    return { kind: "stopped", headline: "Stopped by you",
             lines: [`Nothing sends until you press Start again${approved ? ` (${plural(approved, "approved message")} waiting)` : ""}.`, ...later] };
  }
  if (st.sender.quiet_until || st.sender.waiting_until) return { kind: "waiting", headline: st.sender.status, lines: [st.sender.status, ...later] };
  if (st.sender.status.startsWith("Stopped")) return { kind: "waiting", headline: "Stopped: needs you", lines: [st.sender.status, ...later] };
  if (later.length) return { kind: "waiting", headline: later[0], lines: later };
  return { kind: "idle", headline: "Nothing running", lines: [approved ? `${plural(approved, "approved message")} waiting for you to press Send.` : "Nothing is sending or being written."] };
}

const TONE: Record<RunKind, "good" | "bad" | "warn" | "neutral"> = { running: "good", stopped: "bad", waiting: "warn", idle: "neutral" };

/** Press Stop now / Start again, with the screen changing straight away. */
export function useRunActions() {
  const qc = useQueryClient();
  const toast = useToast();
  const confirm = useConfirm();
  const stop = useMutation({
    mutationFn: () => post("/api/stop"),
    onMutate: () => qc.setQueryData<AppState>(["state"], (s) => s && ({ ...s, job: { ...s.job, name: null }, check: { ...s.check, running: false },
      sender: { ...s.sender, active: false, stopped: true, status: "Stopped by you. Nothing else goes until you press Send again." } })),
    onSuccess: () => toast("Stopped. Nothing sends until you press Start again."),
    onError: (e) => toast((e as Error).message, "bad"),
    onSettled: () => qc.invalidateQueries({ queryKey: ["state"] }),
  });
  const start = useMutation({
    mutationFn: () => post("/api/send/start", { just_one: false }),
    onSuccess: () => toast("Sending what you approved, one at a time."),
    onError: (e) => toast((e as Error).message, "bad"),
    onSettled: () => qc.invalidateQueries({ queryKey: ["state"] }),
  });
  const startAgain = async (approved: number) => {
    if (!await confirm({ title: "Start sending again?",
      text: approved ? `Convo sends the ${plural(approved, "message")} you approved, one at a time, with the usual gaps and daily limit. Stop now stops it again at any moment.`
        : "Nothing is approved yet, so nothing will send until you approve messages. Scheduled sending comes back on.",
      ok: "Start again" })) return;
    start.mutate();
  };
  return { stop, start, startAgain };
}

/** The top-bar control: what's happening (click for details) and the button. */
export function RunControl() {
  const st = useAppState().data;
  const run = describeRun(st);
  const { stop, start, startAgain } = useRunActions();
  const [open, setOpen] = useState(false);
  const ref = useClickAway(() => setOpen(false));
  const approved = st?.counts.approved || 0;
  return (
    <div ref={ref} className="relative flex items-center gap-2">
      <button onClick={() => setOpen(!open)} aria-expanded={open} aria-label={`Status: ${run.headline}`} title={run.headline}
        className={cx("flex h-9 max-w-[300px] items-center gap-2 rounded-xl border px-3 text-[13px] transition-colors",
          run.kind === "running" ? "border-good/30 bg-good-soft text-ink" : run.kind === "stopped" ? "border-bad/30 bg-bad-soft text-ink"
            : run.kind === "waiting" ? "border-warn/30 bg-warn-soft text-ink" : "border-line bg-surface text-ink-2 hover:border-line-2")}>
        {run.kind === "waiting" && st?.sender.quiet_until ? <Moon className="size-3.5 text-warn" /> : <Dot tone={TONE[run.kind]} pulse={run.kind === "running"} />}
        <span className="truncate max-md:hidden">{run.headline}</span>
      </button>

      {run.kind === "stopped" ? (
        <Button variant="secondary" icon={Play} loading={start.isPending} onClick={() => startAgain(approved)} className="max-sm:px-2.5" aria-label="Start again">
          <span className="max-sm:hidden">Start again</span></Button>
      ) : (
        <Button variant={run.kind === "running" ? "danger" : "danger-soft"} icon={stop.isPending ? Loader2 : Square} disabled={stop.isPending}
          onClick={() => stop.mutate()} title="Stop all sending and writing straight away" className={cx("max-sm:px-2.5", stop.isPending && "[&_svg]:animate-spin")}>
          <span className="max-sm:hidden">{stop.isPending ? "Stopping…" : "Stop now"}</span></Button>
      )}

      {open && (
        <div role="dialog" aria-label="What Convo is doing" className="absolute right-0 top-11 z-40 w-[340px] max-w-[calc(100vw-24px)] animate-rise rounded-2xl border border-line bg-raised p-4 shadow-[var(--shadow-pop)]">
          <div className="flex items-center gap-2 text-[14px] font-semibold"><Dot tone={TONE[run.kind]} pulse={run.kind === "running"} />
            {run.kind === "running" ? "Running now" : run.kind === "stopped" ? "Stopped by you" : run.kind === "waiting" ? "Waiting" : "Nothing running"}</div>
          <ul className="mt-2.5 grid gap-1.5 text-[13px] text-ink-2">
            {run.lines.map((l) => <li key={l} className="flex gap-2"><CircleCheck className="mt-0.5 size-3.5 shrink-0 text-faint" />{l}</li>)}
          </ul>
          <div className="mt-3 border-t border-line pt-3 text-[12px] text-faint">
            {run.kind === "stopped" ? "Start again sends what you approved; nothing else changes." : "Stop now stops sending at Claude's next click and writing after the reply it's on."}
          </div>
        </div>
      )}
    </div>
  );
}

/** Phones: a slim bar under the top bar whenever something is running, stopped or waiting. */
export function RunBar() {
  const st = useAppState().data;
  const run = describeRun(st);
  const { stop, startAgain } = useRunActions();
  if (run.kind === "idle") return null;
  return (
    <div className={cx("flex items-center gap-2.5 border-b px-4 py-2 text-[13px] lg:hidden",
      run.kind === "running" ? "border-good/25 bg-good-soft" : run.kind === "stopped" ? "border-bad/25 bg-bad-soft" : "border-warn/25 bg-warn-soft")}>
      <Dot tone={TONE[run.kind]} pulse={run.kind === "running"} />
      <span className="min-w-0 grow truncate text-ink-2">{run.headline}</span>
      {run.kind === "stopped"
        ? <button className="shrink-0 font-semibold text-ink underline-offset-2 hover:underline" onClick={() => startAgain(st?.counts.approved || 0)}>Start again</button>
        : run.kind === "running" && <button className="shrink-0 font-semibold text-bad" onClick={() => stop.mutate()}>Stop</button>}
    </div>
  );
}
