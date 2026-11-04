import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpCircle, CheckCircle2, ExternalLink, GitBranch, KeyRound, RefreshCw, RotateCcw, Save, Trash2 } from "lucide-react";
import { Fragment, ReactNode, useState } from "react";
import { Badge, Button, Card, Dot, ErrorState, Field, Input, Note, PageHeader, Skeleton, Toggle, useConfirm, useToast } from "../components/ui";
import { api, post } from "../lib/api";
import { ago } from "../lib/format";

export type UpdateInfo = {
  status: "idle" | "checking" | "up_to_date" | "available" | "downloading" | "installing" | "restarting" | "error";
  current: string; available: boolean; connected: boolean; can_install: boolean; why_not: string | null; previous: string | null;
  latest: { version: string; notes: string; published: string | null; prerelease: boolean; page: string | null } | null;
  error: string | null; progress: number | null; checked_at: number | null; notes_current: string | null; previews: boolean; repo: string;
};

// GitHub's own page for making a key, filled in: read-only access to Convo's project, for a year.
const KEY_PAGE = "https://github.com/settings/personal-access-tokens/new?" + new URLSearchParams({
  name: "Convo updates", description: "Lets Convo on my Mac download its own updates (read-only).",
  target_name: "LukeHorbach-Dev", expires_in: "366", contents: "read",
}).toString();

/** Release notes are short Markdown: paragraphs, "- " bullets and **bold**. */
export function Notes({ text }: { text: string }) {
  const inline = (s: string) => s.split(/(\*\*[^*]+\*\*)/).map((p, i) =>
    p.startsWith("**") ? <b key={i} className="font-semibold text-ink">{p.slice(2, -2)}</b> : <Fragment key={i}>{p}</Fragment>);
  const blocks: ReactNode[] = [];
  let bullets: string[] = [];
  const flush = () => {
    if (bullets.length) blocks.push(<ul key={blocks.length} className="grid gap-2">{bullets.map((b, i) =>
      <li key={i} className="flex gap-2.5"><span className="mt-[9px] size-1.5 shrink-0 rounded-full bg-violet" /><span>{inline(b)}</span></li>)}</ul>);
    bullets = [];
  };
  let para = "";
  for (const line of text.split("\n")) {
    if (line.startsWith("- ")) { if (para) { blocks.push(<p key={blocks.length}>{inline(para)}</p>); para = ""; } flush(); bullets.push(line.slice(2)); }
    else if (line.startsWith("  ") && bullets.length) bullets[bullets.length - 1] += " " + line.trim();
    else if (!line.trim()) { flush(); if (para) { blocks.push(<p key={blocks.length}>{inline(para)}</p>); para = ""; } }
    else { flush(); para += (para ? " " : "") + line.trim(); }
  }
  flush();
  if (para) blocks.push(<p key={blocks.length}>{inline(para)}</p>);
  return <div className="grid gap-3 text-[14px] leading-relaxed text-ink-2">{blocks}</div>;
}

export function useUpdates(poll = false) {
  return useQuery({ queryKey: ["updates"], queryFn: () => api<UpdateInfo>("/api/updates"), refetchInterval: (q) =>
    poll || ["checking", "downloading", "installing", "restarting"].includes(q.state.data?.status || "") ? 1500 : false });
}

export default function Updates() {
  const { data: u, isLoading, error, refetch } = useUpdates();
  const qc = useQueryClient();
  const toast = useToast();
  const confirm = useConfirm();
  const [key, setKey] = useState("");
  const refresh = () => qc.invalidateQueries({ queryKey: ["updates"] });
  const act = (fn: () => Promise<unknown>, ok?: string) => async () => {
    try { await fn(); if (ok) toast(ok); } catch (e) { toast((e as Error).message, "bad"); } finally { refresh(); qc.invalidateQueries({ queryKey: ["state"] }); }
  };
  const check = useMutation({ mutationFn: () => post("/api/updates/check"), onSettled: refresh });
  const saveKey = useMutation({
    mutationFn: (v: string) => post("/api/settings", { github_token: v }),
    onSuccess: (_, v) => { toast(v ? "Key saved. Looking for updates…" : "Key removed."); setKey(""); if (v) check.mutate(); else refresh(); },
    onError: (e) => toast((e as Error).message, "bad"),
  });
  if (error) return <ErrorState error={error} retry={refetch} />;
  if (isLoading || !u) return <div><PageHeader title="Updates" /><Skeleton className="h-72" /></div>;

  const busy = ["downloading", "installing", "restarting"].includes(u.status);
  const install = act(async () => {
    if (!await confirm({ title: `Update to Convo ${u.latest?.version}?`,
      text: "Convo makes a backup, installs the new version and restarts by itself. Your hellos, people, approvals, settings and Instagram sign-in are kept. Anything sending pauses for a moment.",
      ok: "Update now" })) return;
    await post("/api/updates/install");
  });
  const undo = act(async () => {
    if (!await confirm({ title: `Go back to Convo ${u.previous}?`, text: "Convo makes a backup, switches back to the version you had before and restarts. Your data is kept.", ok: "Go back" })) return;
    await post("/api/updates/undo");
  });

  return (
    <div>
      <PageHeader title="Updates" subtitle="New versions of Convo come from its GitHub project. Nothing installs until you press Update." />
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
        <div className="grid content-start gap-5">
          <Card>
            <div className="flex flex-wrap items-start gap-4">
              <span className="grid size-12 shrink-0 place-items-center rounded-2xl bg-violet-soft text-violet-2"><ArrowUpCircle className="size-6" /></span>
              <div className="min-w-0 grow">
                <div className="text-[13px] text-muted">You have</div>
                <div className="flex flex-wrap items-center gap-2"><span className="text-[22px] font-semibold tracking-[-0.02em]">Convo {u.current}</span>
                  {u.current.includes("-") && <Badge tone="violet">Preview</Badge>}</div>
                <StatusLine u={u} />
              </div>
              <Button icon={RefreshCw} loading={check.isPending || u.status === "checking"} disabled={busy} onClick={() => check.mutate()}>Check for updates</Button>
            </div>
            {u.status === "error" && u.error && <Note tone="bad" className="mt-4">{u.error}</Note>}
            {!u.can_install && u.why_not && <Note className="mt-4">{u.why_not}</Note>}
            {(u.status === "downloading" || u.status === "installing") && (
              <div className="mt-5">
                <div className="mb-2 flex justify-between text-[13px] text-ink-2"><span>{u.status === "downloading" ? "Downloading" : "Installing and checking it starts"}…</span>
                  {u.progress != null && <span className="tabular text-muted">{u.progress}%</span>}</div>
                <div className="h-2 overflow-hidden rounded-full bg-bg-2"><div className={u.progress == null ? "h-full w-1/3 animate-pulse rounded-full bg-gradient-to-r from-violet to-cyan" : "h-full rounded-full bg-gradient-to-r from-violet to-cyan transition-[width]"}
                  style={u.progress == null ? undefined : { width: `${u.progress}%` }} /></div>
              </div>
            )}
            {u.status === "restarting" && <Note tone="good" className="mt-4">Restarting into the new version. This window comes back by itself in a few seconds.</Note>}
          </Card>

          {u.available && u.latest && (
            <Card title={`Convo ${u.latest.version} is ready`} subtitle={u.latest.published ? `Published ${ago(u.latest.published)}` : undefined}
              action={u.latest.prerelease ? <Badge tone="violet">Preview</Badge> : undefined}>
              <Notes text={u.latest.notes || "No notes for this version."} />
              <div className="mt-5 flex flex-wrap gap-2">
                <Button variant="primary" size="lg" icon={ArrowUpCircle} disabled={!u.can_install || busy} loading={busy} onClick={install}>Update now</Button>
                {u.latest.page && <a href={u.latest.page} target="_blank" rel="noreferrer"
                  className="inline-flex h-11 items-center gap-2 rounded-xl border border-line-2 bg-raised px-5 text-[15px] font-medium text-ink hover:bg-hover">
                  <ExternalLink className="size-4" />See it on GitHub</a>}
              </div>
            </Card>
          )}

          {u.notes_current && (
            <Card title={`What's new in ${u.current}`}><Notes text={u.notes_current} /></Card>
          )}
        </div>

        <div className="grid content-start gap-5">
          <Card title="GitHub key" icon={GitBranch} subtitle={u.connected ? undefined : "Convo's project is private, so Convo needs a key that can only read it."}>
            {u.connected ? (
              <div className="flex flex-wrap items-center gap-3">
                <span className="flex items-center gap-2 text-[14px] text-good"><CheckCircle2 className="size-4" />Key saved</span>
                <Button size="sm" variant="ghost" icon={Trash2} className="ml-auto" onClick={() => saveKey.mutate("")}>Remove</Button>
              </div>
            ) : (
              <ol className="mb-4 grid gap-2.5 text-[13.5px] text-ink-2">
                <Step n={1}><a className="font-medium text-violet-2 underline-offset-2 hover:underline" href={KEY_PAGE} target="_blank" rel="noreferrer">Open GitHub's key page</a> (it opens in your browser, already filled in).</Step>
                <Step n={2}>Under <b>Repository access</b>, choose <b>Only select repositories</b> and pick <b>convo-agent</b>.</Step>
                <Step n={3}>Check <b>Contents</b> says <b>Read-only</b>, then press <b>Generate token</b>.</Step>
                <Step n={4}>Copy the key (it starts with github_pat_) and paste it below.</Step>
              </ol>
            )}
            <form className="grid gap-2" onSubmit={(e) => { e.preventDefault(); if (key.trim()) saveKey.mutate(key.trim()); }}>
              <Field label={u.connected ? "Replace the key" : "Your GitHub key"} htmlFor="gh-key">
                <Input id="gh-key" type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder="github_pat_…" />
              </Field>
              <Button variant={u.connected ? "secondary" : "primary"} icon={Save} loading={saveKey.isPending} disabled={!key.trim()}>Save key</Button>
            </form>
            <p className="mt-3 flex items-start gap-2 text-[12px] text-faint"><KeyRound className="mt-0.5 size-3.5 shrink-0" />Kept on this Mac only (in Convo's settings file, readable by you alone) and only ever sent to GitHub.</p>
          </Card>

          <Card title="Options">
            <div className="flex items-center justify-between gap-4">
              <div><div className="text-[14px] font-medium">Offer preview versions</div>
                <div className="text-[12.5px] text-muted">Try new things early. Previews are always offered while you're on one.</div></div>
              <Toggle label="Offer preview versions" checked={u.previews} onChange={(v) => act(() => post("/api/settings", { update_previews: v }), "Saved.")()} />
            </div>
            {u.previous && (
              <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
                <div><div className="text-[14px] font-medium">Undo the last update</div><div className="text-[12.5px] text-muted">Go back to Convo {u.previous}.</div></div>
                <Button icon={RotateCcw} disabled={busy} onClick={undo}>Go back</Button>
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}

function Step({ n, children }: { n: number; children: ReactNode }) {
  return <li className="flex gap-3"><span className="grid size-6 shrink-0 place-items-center rounded-full bg-violet-soft text-[12px] font-semibold text-violet-2">{n}</span><span className="pt-0.5">{children}</span></li>;
}

function StatusLine({ u }: { u: UpdateInfo }) {
  const checked = u.checked_at ? ` · checked ${ago(new Date(u.checked_at * 1000).toISOString())}` : "";
  const [tone, text]: [any, string] =
    u.status === "checking" ? ["violet", "Looking for updates…"]
    : u.status === "available" ? ["violet", `Convo ${u.latest?.version} is ready to install`]
    : u.status === "up_to_date" ? ["good", `This is the newest version${checked}`]
    : u.status === "error" ? ["warn", `Couldn't check for updates${checked}`]
    : u.status === "restarting" ? ["good", "Restarting…"]
    : busy(u) ? ["violet", "Updating…"]
    : ["neutral", u.connected ? "Not checked yet" : "Add your GitHub key to get updates"];
  return <div className="mt-1.5 flex items-center gap-2 text-[13px] text-muted"><Dot tone={tone} pulse={tone === "violet"} />{text}</div>;
}

const busy = (u: UpdateInfo) => ["downloading", "installing"].includes(u.status);
