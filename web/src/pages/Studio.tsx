// Content Studio: one post from idea to posted. Notes, the file, the words for each platform (written with you or
// by the AI from your notes), and when it goes out. Saves as you type.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CalendarClock, Check, CheckCircle2, Clapperboard, Copy, FolderOpen, ImagePlus, Lightbulb, Link2, Loader2, Sparkles, Trash2, Upload,
} from "lucide-react";
import { DragEvent, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Badge, Button, Card, cx, Empty, ErrorState, Field, Input, Note, PageHeader, Select, Skeleton, Textarea, useConfirm, useToast } from "../components/ui";
import { api, del, patch, post } from "../lib/api";
import { Content, getContent, Kind, KINDS, localInput, pasteText, PLATFORM, PLATFORM_IDS, PlatformId, preview, STATUS, uploadFile, Version, videoStill, when } from "../lib/content";
import { ago, money } from "../lib/format";
import { useAppState } from "../lib/state";

export default function Studio() {
  const { id } = useParams();
  return id ? <Editor id={Number(id)} key={id} /> : <Start />;
}

// ------------------------------------------------------------------------------------------------ start something
function Start() {
  const nav = useNavigate();
  const toast = useToast();
  const recent = useQuery({ queryKey: ["content", "recent"], queryFn: () => api<{ items: Content[] }>("/api/hub/content?status=idea,draft,ready&limit=8") });
  const create = useMutation({
    mutationFn: (kind: Kind) => post<Content>("/api/hub/content", { kind, title: "", platforms: kind === "video" ? ["youtube"] : kind === "short" ? ["tiktok", "instagram", "youtube"] : ["instagram"] }),
    onSuccess: (c) => nav(`/studio/${c.id}`),
    onError: (e) => toast((e as Error).message, "bad"),
  });
  const ICON: Record<Kind, any> = { idea: Lightbulb, video: Clapperboard, short: Clapperboard, photo: ImagePlus, carousel: ImagePlus, text: Lightbulb };
  const HINT: Record<Kind, string> = { idea: "Write it down before it's gone", video: "A longer video, for YouTube", short: "One vertical video for TikTok, Reels and Shorts",
    photo: "A single photo", carousel: "A few photos in one post", text: "Words only" };
  return (
    <div>
      <PageHeader title="Content Studio" subtitle="Start a post: an idea, a video or a photo. Convo keeps the file on your Mac and helps with the words for each platform." />
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {KINDS.map(([k, label]) => { const I = ICON[k]; return (
          <button key={k} onClick={() => create.mutate(k)} disabled={create.isPending}
            className="group flex items-start gap-3 rounded-2xl border border-line bg-surface p-5 text-left transition-colors hover:border-violet/50 hover:bg-raised">
            <span className="grid size-11 shrink-0 place-items-center rounded-xl bg-violet-soft text-violet-2 transition-transform group-hover:scale-105"><I className="size-5" /></span>
            <span><span className="block text-[15px] font-semibold">{label}</span><span className="block text-[13px] text-muted">{HINT[k]}</span></span>
          </button>); })}
      </div>
      <h2 className="mt-8 mb-3 text-[15px] font-semibold">Carry on with</h2>
      {recent.isLoading ? <Skeleton className="h-24" /> : recent.data?.items.length ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{recent.data.items.map((c) => <MiniCard key={c.id} c={c} />)}</div>
      ) : <Card><Empty icon={Lightbulb} title="Nothing in progress">Your ideas and drafts show up here.</Empty></Card>}
    </div>
  );
}

export function MiniCard({ c }: { c: Content }) {
  const nav = useNavigate();
  const img = preview(c);
  return (
    <button onClick={() => nav(`/studio/${c.id}`)} className="group overflow-hidden rounded-2xl border border-line bg-surface text-left transition-colors hover:border-line-2">
      <div className="relative aspect-[4/3] bg-bg-2">
        {img ? <img src={img} alt="" className="size-full object-cover" loading="lazy" />
          : <div className="grid size-full place-items-center text-faint">{c.kind === "idea" || c.kind === "text" ? <Lightbulb className="size-7" /> : <Clapperboard className="size-7" />}</div>}
        <div className="absolute left-2 top-2"><Badge tone={STATUS[c.status].tone}>{STATUS[c.status].label}</Badge></div>
      </div>
      <div className="p-3">
        <div className="truncate text-[14px] font-medium">{c.title || <span className="text-muted">Untitled</span>}</div>
        <div className="mt-1 flex items-center gap-1.5 text-[12px] text-faint">
          {c.platforms.map((p) => <span key={p} className="size-2 rounded-full" style={{ background: PLATFORM[p].color }} title={PLATFORM[p].name} />)}
          <span className="truncate">{c.scheduled_at && c.status !== "posted" ? when(c.scheduled_at) : `Edited ${ago(c.updated_at)}`}</span>
        </div>
      </div>
    </button>
  );
}

// ------------------------------------------------------------------------------------------------ the editor
function Editor({ id }: { id: number }) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const confirm = useConfirm();
  const ai = useAppState().data?.ai.on;
  const q = useQuery({ queryKey: ["content", id], queryFn: () => getContent(id) });
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: () => api("/api/hub/accounts") });
  const [c, setC] = useState<Content | null>(null);
  const [saving, setSaving] = useState<"idle" | "saving" | "saved">("idle");
  const [tab, setTab] = useState<PlatformId>("instagram");
  const pending = useRef<Partial<Content>>({});
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => { if (q.data && !c) { setC(q.data); setTab(q.data.platforms[0] || "instagram"); } }, [q.data, c]);

  const flush = async () => {
    const body = pending.current;
    pending.current = {};
    if (!Object.keys(body).length) return;
    setSaving("saving");
    try { const r = await patch<Content>(`/api/hub/content/${id}`, body); setC((x) => x && { ...x, status: r.status, updated_at: r.updated_at }); setSaving("saved"); qc.invalidateQueries({ queryKey: ["content"] }); }
    catch (e) { toast((e as Error).message, "bad"); setSaving("idle"); }
  };
  const change = (part: Partial<Content>, now = false) => {
    setC((x) => x && { ...x, ...part });
    pending.current = { ...pending.current, ...part };
    window.clearTimeout(timer.current);
    if (now) flush(); else timer.current = window.setTimeout(flush, 700);
  };
  useEffect(() => () => { window.clearTimeout(timer.current); flush(); }, []); // eslint-disable-line

  const write = useMutation({
    mutationFn: async () => { await flush(); return post<Content & { cost: number }>(`/api/hub/content/${id}/write`); },
    onSuccess: (r) => { setC((x) => x && { ...x, versions: r.versions, status: r.status }); toast(`Written. Check and change anything you like${r.cost ? ` (${money(r.cost)})` : ""}.`); },
    onError: (e) => toast((e as Error).message, "bad"),
  });

  if (q.error) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  if (!c) return <div><PageHeader title="Content Studio" /><Skeleton className="h-[520px]" /></div>;
  const setVersion = (p: PlatformId, v: Partial<Version>) => change({ versions: { ...c.versions, [p]: { ...(c.versions[p] || {}), ...v } } });
  const togglePlatform = (p: PlatformId) => {
    const next = c.platforms.includes(p) ? c.platforms.filter((x) => x !== p) : [...c.platforms, p];
    change({ platforms: next }, true);
    if (!next.includes(tab) && next[0]) setTab(next[0]);
    if (next.includes(p)) setTab(p);
  };
  const accts: any[] = accounts.data?.items || [];

  return (
    <div>
      <PageHeader title={c.title || "Untitled post"}
        subtitle={<span className="flex items-center gap-2"><Badge tone={STATUS[c.status].tone}>{STATUS[c.status].label}</Badge>
          <span className="text-faint">{saving === "saving" ? "Saving…" : saving === "saved" ? "Saved" : `Edited ${ago(c.updated_at)}`}</span></span>}
        actions={<Button variant="ghost" onClick={() => nav("/library")}>Library</Button>} />
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="grid content-start gap-5">
          <Card>
            <div className="grid gap-4">
              <Field label="Title (just for you)" htmlFor="c-title"><Input id="c-title" value={c.title} onChange={(e) => change({ title: e.target.value })} placeholder="Lemon cake, first try" /></Field>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="What it is" htmlFor="c-kind"><Select id="c-kind" value={c.kind} onChange={(e) => change({ kind: e.target.value as Kind }, true)}>
                  {KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select></Field>
                <Field label="For which account" htmlFor="c-acct"><Select id="c-acct" value={c.account_id ?? ""} onChange={(e) => change({ account_id: Number(e.target.value) || 0 } as any, true)}>
                  <option value="">Any</option>{accts.map((a) => <option key={a.id} value={a.id}>@{a.handle} ({a.platform_name})</option>)}</Select></Field>
              </div>
              <div>
                <div className="mb-1.5 text-[13px] font-medium text-ink-2">Where it goes</div>
                <div className="flex flex-wrap gap-2">{PLATFORM_IDS.map((p) => (
                  <button key={p} onClick={() => togglePlatform(p)} aria-pressed={c.platforms.includes(p)}
                    className={cx("flex h-9 items-center gap-2 rounded-xl border px-3 text-[13.5px] transition-colors",
                      c.platforms.includes(p) ? "border-transparent bg-raised text-ink shadow-[inset_0_0_0_1.5px_var(--pc)]" : "border-line text-muted hover:border-line-2")}
                    style={{ ["--pc" as any]: PLATFORM[p].color }}>
                    <span className="size-2.5 rounded-full" style={{ background: PLATFORM[p].color }} />{PLATFORM[p].name}{c.platforms.includes(p) && <Check className="size-3.5" />}
                  </button>))}</div>
              </div>
              <Field label="Notes: the idea, script or what happens" htmlFor="c-notes" hint="The AI writes the captions from these notes and your About me, nothing else.">
                <Textarea id="c-notes" rows={5} value={c.notes} onChange={(e) => change({ notes: e.target.value })} placeholder="First time making lemon cake. Too much sugar, still good. Mum loved it." />
              </Field>
            </div>
          </Card>

          <MediaCard c={c} onChange={(x) => setC((old) => old && { ...old, ...x })} />

          <Card title="The words for each platform" icon={Sparkles}
            action={<Button size="sm" variant="primary" icon={Sparkles} loading={write.isPending} disabled={!ai || !c.platforms.length} onClick={() => write.mutate()}
              title={ai ? "About 1 to 2¢" : "Needs your AI key"}>{Object.keys(c.versions).length ? "Write again" : "Write with AI"}</Button>}>
            {!c.platforms.length ? <Note>Choose where it goes first.</Note> : (<>
              <div className="mb-4 flex flex-wrap gap-1.5">{c.platforms.map((p) => (
                <button key={p} onClick={() => setTab(p)} className={cx("flex items-center gap-2 rounded-lg px-3 py-1.5 text-[13px]", tab === p ? "bg-raised text-ink shadow-[inset_0_0_0_1px_var(--color-line-2)]" : "text-muted hover:text-ink")}>
                  <span className="size-2 rounded-full" style={{ background: PLATFORM[p].color }} />{PLATFORM[p].name}</button>))}</div>
              <Words p={tab} v={c.versions[tab]} onChange={(v) => setVersion(tab, v)} />
              {!ai && <p className="mt-3 text-[12.5px] text-faint">Add your AI key in API Connections to have these written for you. You can always write them yourself.</p>}
            </>)}
          </Card>
        </div>

        <div className="grid content-start gap-5">
          <Plan c={c} onChange={(x) => setC((old) => old && { ...old, ...x })} />
          <PublishCard c={c} onDone={() => q.refetch().then((r) => r.data && setC((old) => old && { ...old, versions: r.data!.versions, post_url: r.data!.post_url, status: r.data!.status }))} />
          <Card title="Preview">
            <PhonePreview c={c} p={tab} />
          </Card>
          <Button variant="ghost" icon={Trash2} className="justify-self-start text-bad" onClick={async () => {
            if (!await confirm({ title: "Delete this post?", text: "Its notes, words and file are removed from your Mac. This can't be undone.", ok: "Delete", danger: true })) return;
            await del(`/api/hub/content/${id}`); qc.invalidateQueries({ queryKey: ["content"] }); toast("Deleted."); nav("/library");
          }}>Delete post</Button>
        </div>
      </div>
    </div>
  );
}

function Words({ p, v, onChange }: { p: PlatformId; v?: Version; onChange: (v: Partial<Version>) => void }) {
  const toast = useToast();
  const tags = (v?.hashtags || []).join(" ");
  const copy = async () => { try { await navigator.clipboard.writeText(pasteText(v, p)); toast(`Copied for ${PLATFORM[p].name}.`); } catch { toast("Couldn't copy here.", "bad"); } };
  return (
    <div className="grid gap-3">
      {p === "youtube" ? (<>
        <Field label={`Title (${(v?.title || "").length}/100)`} htmlFor="v-title"><Input id="v-title" maxLength={100} value={v?.title || ""} onChange={(e) => onChange({ title: e.target.value })} /></Field>
        <Field label="Description" htmlFor="v-desc"><Textarea id="v-desc" rows={5} value={v?.description || ""} onChange={(e) => onChange({ description: e.target.value })} /></Field>
      </>) : (
        <Field label={`Caption (${(v?.caption || "").length}/2200)`} htmlFor="v-cap"><Textarea id="v-cap" rows={5} maxLength={2200} value={v?.caption || ""} onChange={(e) => onChange({ caption: e.target.value })} /></Field>
      )}
      <Field label={p === "youtube" ? "Tags" : "Hashtags"} htmlFor="v-tags" hint="Separated by spaces, without #">
        <Input id="v-tags" value={tags} onChange={(e) => onChange({ hashtags: e.target.value.split(/[\s,]+/).map((t) => t.replace(/^#/, "")).filter(Boolean) })} />
      </Field>
      <Button size="sm" icon={Copy} className="justify-self-start" disabled={!pasteText(v, p)} onClick={copy}>Copy for {PLATFORM[p].name}</Button>
    </div>
  );
}

function MediaCard({ c, onChange }: { c: Content; onChange: (x: Partial<Content>) => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [pct, setPct] = useState<number | null>(null);
  const [over, setOver] = useState(false);
  const send = async (file: File) => {
    setPct(0);
    try {
      let r = await uploadFile(c.id, file, file.name, "media", setPct);
      if (file.type.startsWith("video/")) {
        const still = await videoStill(file);
        if (still) r = await uploadFile(c.id, still, "thumb.jpg", "thumb");
      }
      onChange(r); qc.invalidateQueries({ queryKey: ["content"] }); toast("File added. It stays on your Mac.");
    } catch (e) { toast((e as Error).message, "bad"); } finally { setPct(null); }
  };
  const drop = (e: DragEvent) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files[0]; if (f) send(f); };
  const isVideo = c.media_type?.startsWith("video/");
  return (
    <Card title="The file" icon={ImagePlus} action={c.media && <Button size="sm" variant="ghost" icon={FolderOpen} onClick={() => post(`/api/hub/content/${c.id}/reveal`).catch((e) => toast(e.message, "bad"))}>Show in Finder</Button>}>
      <div onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={drop}
        className={cx("relative grid min-h-[180px] place-items-center overflow-hidden rounded-2xl border-2 border-dashed transition-colors", over ? "border-violet bg-violet-soft" : "border-line-2 bg-bg-2")}>
        {c.media_url ? (isVideo ? <video src={c.media_url} poster={c.thumb_url || undefined} controls className="max-h-[420px] w-full bg-black" />
          : <img src={c.media_url} alt="" className="max-h-[420px] w-full object-contain" />)
          : <div className="px-6 py-8 text-center"><Upload className="mx-auto mb-2 size-7 text-faint" />
              <p className="text-[14px] font-medium">Drop a video or photo here</p><p className="text-[12.5px] text-muted">mp4, mov, jpg, png, heic. Up to 4 GB. It stays on this Mac.</p></div>}
        {pct !== null && <div className="absolute inset-x-0 bottom-0 h-1.5 bg-bg-2"><div className="h-full bg-gradient-to-r from-violet to-cyan transition-[width]" style={{ width: `${pct}%` }} /></div>}
      </div>
      <input ref={input} type="file" accept="image/*,video/*" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) send(f); e.target.value = ""; }} />
      <Button size="sm" className="mt-3" icon={pct !== null ? Loader2 : Upload} disabled={pct !== null} onClick={() => input.current?.click()}>
        {pct !== null ? `Uploading ${pct}%` : c.media ? "Replace the file" : "Choose a file"}</Button>
    </Card>
  );
}

function Plan({ c, onChange }: { c: Content; onChange: (x: Partial<Content>) => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [at, setAt] = useState(localInput(c.scheduled_at));
  const [url, setUrl] = useState(c.post_url || "");
  const save = async (body: any, msg: string) => {
    try { const r = await patch<Content>(`/api/hub/content/${c.id}`, body); onChange(r); qc.invalidateQueries({ queryKey: ["content"] }); qc.invalidateQueries({ queryKey: ["calendar"] }); toast(msg); }
    catch (e) { toast((e as Error).message, "bad"); }
  };
  const steps: [string, boolean][] = [["Idea", true], ["Words and file", c.status !== "idea"], ["Ready", ["ready", "scheduled", "due", "posted"].includes(c.status)],
    ["Scheduled", ["scheduled", "due", "posted"].includes(c.status)], ["Posted", c.status === "posted"]];
  return (
    <Card title="Plan it" icon={CalendarClock}>
      <ol className="mb-4 flex items-center gap-1">{steps.map(([l, on], i) => (
        <li key={l} className="flex grow flex-col items-center gap-1 text-center" title={l}>
          <span className={cx("h-1.5 w-full rounded-full", on ? "bg-gradient-to-r from-violet to-cyan" : "bg-bg-2")} />
          <span className={cx("text-[10.5px]", on ? "text-ink-2" : "text-faint")}>{i === 1 ? "Words" : l}</span></li>))}</ol>
      {c.status === "due" && <Note tone="warn" className="mb-3">It's time to post this. Copy the words, use Show in Finder for the file, then mark it posted.</Note>}
      {c.status === "posted" ? (
        <div className="grid gap-2 text-[13.5px]">
          <div className="flex items-center gap-2 text-good"><CheckCircle2 className="size-4" />Posted {ago(c.posted_at)}</div>
          {c.post_url && <a className="truncate text-violet-2 hover:underline" href={c.post_url} target="_blank" rel="noreferrer">{c.post_url}</a>}
          <Button size="sm" variant="ghost" className="justify-self-start" onClick={() => save({ status: "ready" }, "Back to ready.")}>Not posted after all</Button>
        </div>
      ) : (
        <div className="grid gap-3">
          {(c.status === "idea" || c.status === "draft") && <Button icon={Check} onClick={() => save({ status: "ready" }, "Marked ready.")}>Mark ready</Button>}
          <Field label="When it goes out" htmlFor="c-when"><Input id="c-when" type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} /></Field>
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" icon={CalendarClock} disabled={!at} onClick={() => save({ scheduled_at: at }, `Scheduled for ${when(new Date(at).toISOString())}.`)}>
              {c.scheduled_at ? "Change time" : "Schedule"}</Button>
            {c.scheduled_at && <Button variant="ghost" onClick={() => { setAt(""); save({ scheduled_at: "" }, "Unscheduled."); }}>Unschedule</Button>}
          </div>
          <p className="text-[12px] text-faint">Until Instagram, TikTok and YouTube are connected (stages 4 and 5), Convo tells you when it's time, with the words ready to paste.</p>
          <div className="border-t border-line pt-3">
            <Field label="Posted it? Paste the link (optional)" htmlFor="c-url"><Input id="c-url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://" /></Field>
            <Button size="sm" className="mt-2" icon={Link2} onClick={() => save({ status: "posted", post_url: url.trim() }, "Marked posted.")}>Mark posted</Button>
          </div>
        </div>
      )}
    </Card>
  );
}

function PhonePreview({ c, p }: { c: Content; p: PlatformId }) {
  const v = c.versions[p];
  const img = preview(c);
  const isVideo = c.media_type?.startsWith("video/");
  return (
    <div className="mx-auto w-[260px] overflow-hidden rounded-[28px] border-[6px] border-[#1b1d26] bg-black shadow-[var(--shadow-pop)]">
      <div className="flex items-center gap-2 bg-[#0d0e14] px-3 py-2 text-[11px] text-white/80"><span className="size-2 rounded-full" style={{ background: PLATFORM[p].color }} />{PLATFORM[p].name}</div>
      <div className={cx("grid place-items-center bg-[#111]", p === "youtube" && !c.kind.includes("short") ? "aspect-video" : c.kind === "photo" || c.kind === "carousel" ? "aspect-square" : "aspect-[9/16] max-h-[360px]")}>
        {img ? <img src={img} alt="" className="size-full object-cover" /> : isVideo ? <Clapperboard className="size-8 text-white/30" /> : <ImagePlus className="size-8 text-white/30" />}
      </div>
      <div className="grid gap-1 bg-[#0d0e14] px-3 py-3 text-[11.5px] leading-snug text-white/85">
        {p === "youtube" ? <><b className="text-[12.5px] text-white">{v?.title || "Your title"}</b><span className="line-clamp-3 text-white/60">{v?.description || "Your description"}</span></>
          : <span className="line-clamp-4">{v?.caption || "Your caption"}</span>}
        {v?.hashtags?.length ? <span className="text-sky-300/80">{v.hashtags.map((t) => `#${t}`).join(" ")}</span> : null}
      </div>
    </div>
  );
}

function PublishCard({ c, onDone }: { c: Content; onDone: () => void }) {
  const info = useQuery({ queryKey: ["publish-info"], queryFn: () => api<Record<PlatformId, { kind: string; explain: string; connected: boolean }>>("/api/connect/publish-info") });
  if (!c.platforms.length) return null;
  return (
    <Card title="Publish" icon={Upload} subtitle="Straight from here, once a platform is connected">
      <div className="grid gap-3">{c.platforms.map((p) => <PublishRow key={p} c={c} p={p} info={info.data?.[p]} onDone={onDone} />)}</div>
    </Card>
  );
}

function PublishRow({ c, p, info, onDone }: { c: Content; p: PlatformId; info?: { kind: string; explain: string; connected: boolean }; onDone: () => void }) {
  const toast = useToast();
  const confirm = useConfirm();
  const [state, setState] = useState<{ pct: number | null; error: string | null; done: boolean; result: string | null } | null>(null);
  useEffect(() => {
    if (!state || state.done) return;
    const t = setInterval(async () => {
      const s = await api(`/api/hub/content/${c.id}/publish/${p}`).catch(() => null);
      if (!s) return;
      setState(s);
      if (s.done) { clearInterval(t); if (s.error) toast(s.error, "bad"); else { toast(DONE[info?.kind || ""] || "Done."); onDone(); } }
    }, 1500);
    return () => clearInterval(t);
  }, [state?.done, c.id, p]); // eslint-disable-line
  const DONE: Record<string, string> = { private: "Uploaded to YouTube as private.", public: `Posted on ${PLATFORM[p].name}.`, draft: "Sent to your TikTok inbox. Finish it in the TikTok app." };
  const LABEL: Record<string, string> = { private: "Upload as private", public: "Publish now", draft: "Send to TikTok as a draft" };
  const done = (c.versions[p] as any)?.published;
  const isVideo = c.media_type?.startsWith("video/");
  const go = async () => {
    if (!info) return;
    if (!await confirm({ title: `${LABEL[info.kind]} on ${PLATFORM[p].name}?`, text: info.explain, ok: LABEL[info.kind], danger: info.kind === "public" })) return;
    try { setState(await post(`/api/hub/content/${c.id}/publish/${p}`)); } catch (e) { toast((e as Error).message, "bad"); }
  };
  return (
    <div className="rounded-xl border border-line bg-bg-2 p-3">
      <div className="mb-2 flex items-center gap-2 text-[13.5px] font-medium"><span className="size-2 rounded-full" style={{ background: PLATFORM[p].color }} />{PLATFORM[p].name}</div>
      {done ? (
        <div className="flex items-center gap-2 text-[13px] text-good"><CheckCircle2 className="size-4" />
          {p === "youtube" ? <a className="hover:underline" href={`https://studio.youtube.com/video/${(c.versions.youtube as any).uploaded_id || done}/edit`} target="_blank" rel="noreferrer">Uploaded as private: publish it in YouTube Studio</a>
            : p === "tiktok" ? "Sent to your TikTok inbox" : <a className="hover:underline" href={String(done)} target="_blank" rel="noreferrer">Posted</a>}</div>
      ) : !info?.connected ? <p className="text-[12.5px] text-muted">Connect {PLATFORM[p].name} in Integrations to publish from here.</p>
        : !isVideo ? <p className="text-[12.5px] text-muted">{p === "instagram" ? "Convo can publish Reels (videos). For a photo, copy the caption and post it from your phone." : "Add a video file to publish it."}</p>
        : state && !state.done ? (
          <div><div className="mb-1.5 text-[12.5px] text-ink-2">Working… {state.pct ?? 0}%</div>
            <div className="h-1.5 rounded-full bg-bg"><div className="h-full rounded-full bg-gradient-to-r from-violet to-cyan transition-[width]" style={{ width: `${state.pct ?? 0}%` }} /></div></div>
        ) : <Button size="sm" variant={info.kind === "public" ? "primary" : "secondary"} icon={Upload} onClick={go}>{LABEL[info.kind]}</Button>}
    </div>
  );
}
