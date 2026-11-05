import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AtSign, CheckCircle2, CircleDashed, ExternalLink, FolderKanban, Link2, Pencil, Plus, Rocket, Trash2, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { cx,
  Avatar, Badge, Button, Card, Dot, Empty, ErrorState, Field, IconButton, Input, Modal, Note, PageHeader, Select, Skeleton,
  Textarea, useConfirm, useToast,
} from "../components/ui";
import { api, del, patch, post } from "../lib/api";
import { ago, num } from "../lib/format";

const useAccounts = (role = "") => useQuery({ queryKey: ["accounts", role], queryFn: () => api(`/api/hub/accounts${role ? `?role=${role}` : ""}`) });
const useGroups = () => useQuery({ queryKey: ["groups"], queryFn: () => api("/api/hub/groups") });

function StatusBadge({ a }: { a: any }) {
  return a.status === "connected" ? <Badge tone="good"><Dot tone="good" />Connected</Badge>
    : a.status === "error" ? <Badge tone="bad">Needs attention</Badge> : <Badge tone="neutral">Not connected</Badge>;
}

export function AccountCard({ a, onEdit, onRemove }: { a: any; onEdit?: () => void; onRemove?: () => void }) {
  return (
    <Card>
      <div className="flex items-start gap-3">
        <div className="relative"><Avatar name={a.handle} size={46} />
          <span className="absolute -right-0.5 -bottom-0.5 size-3.5 rounded-full ring-2 ring-surface" style={{ background: a.platform === "instagram" ? "#e1306c" : a.platform === "tiktok" ? "#25f4ee" : "#ff3d3d" }} /></div>
        <div className="min-w-0 grow">
          <div className="flex flex-wrap items-center gap-2"><span className="truncate text-[15px] font-semibold">@{a.handle}</span>
            {a.role === "main" && <Badge tone="violet">Main</Badge>}{a.group && <Badge tone="cyan">{a.group.name}</Badge>}</div>
          <div className="text-[12.5px] text-muted">{a.platform_name}{a.niche ? ` · ${a.niche}` : ""}</div>
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          {a.url && <a href={a.url} target="_blank" rel="noreferrer" aria-label="Open profile" className="grid size-9 place-items-center rounded-xl text-muted hover:bg-hover hover:text-ink"><ExternalLink className="size-4" /></a>}
          {onEdit && <IconButton icon={Pencil} label="Edit" onClick={onEdit} />}
          {onRemove && <IconButton icon={Trash2} label="Remove from Convo" onClick={onRemove} />}
        </div>
      </div>
      {a.purpose && <p className="mt-3 text-[13.5px] text-ink-2">{a.purpose}</p>}
      <div className="mt-4 grid grid-cols-3 gap-2">
        <div className="rounded-xl border border-line bg-bg-2 px-3 py-2"><div className="tabular text-[17px] font-semibold">{num(a.followers)}</div><div className="text-[11.5px] text-muted">Followers</div></div>
        <div className="rounded-xl border border-line bg-bg-2 px-3 py-2"><div className="tabular text-[17px] font-semibold">{num(a.following)}</div><div className="text-[11.5px] text-muted">Following</div></div>
        <div className="rounded-xl border border-line bg-bg-2 px-3 py-2"><div className="tabular text-[17px] font-semibold">{a.followers_change_7d == null ? "—" : `${a.followers_change_7d >= 0 ? "+" : ""}${a.followers_change_7d}`}</div><div className="text-[11.5px] text-muted">This week</div></div>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2 text-[12px] text-muted"><StatusBadge a={a} />
        <span className="min-w-0 truncate">{a.as_of ? `Numbers from ${a.source} ${ago(a.as_of)}` : a.status_note}</span></div>
      {a.referral_link && <div className="mt-2 flex items-center gap-1.5 truncate text-[12px] text-faint"><Link2 className="size-3.5" />{a.referral_link}</div>}
    </Card>
  );
}

export function AccountForm({ open, onClose, account, role, max }: { open: boolean; onClose: () => void; account?: any; role: "launch" | "other"; max?: number }) {
  const toast = useToast();
  const qc = useQueryClient();
  const { data: groups } = useGroups();
  const blank = { platform: "instagram", handle: "", display_name: "", niche: "", purpose: "", notes: "", group_id: "", referral_link: "" };
  const [f, setF] = useState<any>(blank);
  const [err, setErr] = useState("");
  useEffect(() => { if (open) { setErr(""); setF(account ? { ...blank, ...Object.fromEntries(Object.entries(account).map(([k, v]) => [k, v ?? ""])), group_id: account.group_id ?? "" } : blank); } }, [open, account]); // eslint-disable-line
  const m = useMutation({
    mutationFn: () => {
      const body = { ...f, group_id: f.group_id === "" ? null : Number(f.group_id), role: account ? undefined : role };
      return account ? patch(`/api/hub/accounts/${account.id}`, body) : post("/api/hub/accounts", body);
    },
    onSuccess: () => { toast(account ? "Saved." : "Account added."); qc.invalidateQueries(); onClose(); },
    onError: (e) => setErr((e as Error).message),
  });
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  return (
    <Modal open={open} onClose={onClose} title={account ? `Edit @${account.handle}` : role === "launch" ? "Add a launch network account" : "Add an account"} wide
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" loading={m.isPending} onClick={() => m.mutate()}>{account ? "Save" : "Add account"}</Button></>}>
      <form className="grid gap-4 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
        {!account && <>
          <Field label="Platform" htmlFor="f-platform"><Select id="f-platform" value={f.platform} onChange={set("platform")}>
            <option value="instagram">Instagram</option><option value="tiktok">TikTok</option><option value="youtube">YouTube</option></Select></Field>
          <Field label="Username" htmlFor="f-handle" hint="Without the @ is fine"><Input id="f-handle" value={f.handle} onChange={set("handle")} placeholder="luke.kicks" autoFocus /></Field>
        </>}
        <Field label="Name to show" htmlFor="f-name"><Input id="f-name" value={f.display_name} onChange={set("display_name")} placeholder="Luke · football" /></Field>
        <Field label="Niche" htmlFor="f-niche"><Input id="f-niche" value={f.niche} onChange={set("niche")} placeholder="Football, baking, study tips…" /></Field>
        <div className="sm:col-span-2"><Field label="Its purpose" htmlFor="f-purpose" hint="What makes this account worth following on its own">
          <Textarea id="f-purpose" rows={2} value={f.purpose} onChange={set("purpose")} placeholder="Short football clips with real tips, linking to my main account" /></Field></div>
        <Field label="Group" htmlFor="f-group"><Select id="f-group" value={f.group_id} onChange={set("group_id")}><option value="">No group</option>
          {(groups?.items || []).map((g: any) => <option key={g.id} value={g.id}>{g.name}</option>)}</Select></Field>
        <Field label="Tracking link" htmlFor="f-link" hint="The link in this account's bio, so visits to your main account can be counted">
          <Input id="f-link" value={f.referral_link} onChange={set("referral_link")} placeholder="https://…" /></Field>
        <div className="sm:col-span-2"><Field label="Notes" htmlFor="f-notes"><Textarea id="f-notes" rows={2} value={f.notes} onChange={set("notes")} /></Field></div>
        {err && <div className="sm:col-span-2"><ErrorState error={new Error(err)} /></div>}
        {!account && role === "launch" && max != null && <p className="text-[12.5px] text-muted sm:col-span-2">Live numbers arrive when the platform is connected. Up to {max} launch accounts.</p>}
        <button hidden />
      </form>
    </Modal>
  );
}

function useRemove() {
  const confirm = useConfirm();
  const toast = useToast();
  const qc = useQueryClient();
  return async (a: any) => {
    if (!await confirm({ title: `Remove @${a.handle} from Convo?`, text: "Only Convo forgets it. Nothing changes on the platform. A backup is made first.", ok: "Remove", danger: true })) return;
    try { await del(`/api/hub/accounts/${a.id}`); toast(`Removed @${a.handle}.`); qc.invalidateQueries(); } catch (e) { toast((e as Error).message, "bad"); }
  };
}

// ------------------------------------------------------------------------------------------------ Connected Accounts
export function ConnectedAccounts() {
  const nav = useNavigate();
  const { data, isLoading, error, refetch } = useAccounts();
  const [form, setForm] = useState<{ open: boolean; account?: any }>({ open: false });
  const remove = useRemove();
  const items: any[] = data?.items || [];
  return (
    <div>
      <PageHeader title="Connected Accounts" subtitle="Your accounts on every platform, in one place."
        actions={<><Button icon={Rocket} onClick={() => nav("/launch")}>Launch network</Button><Button variant="primary" icon={Plus} onClick={() => setForm({ open: true })}>Add account</Button></>} />
      {error ? <ErrorState error={error} retry={refetch} /> : isLoading ? <Skeleton className="h-52" /> : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {items.filter((a) => a.role !== "launch").map((a) => <AccountCard key={a.id} a={a} onEdit={() => setForm({ open: true, account: a })} onRemove={a.role === "main" ? undefined : () => remove(a)} />)}
        </div>)}
      <Note tone="cyan" className="mt-6">Instagram works today through your downloads and Convo's own browser. Live connections for YouTube (stage 4), Instagram and
        TikTok (stage 5) add views, reach and publishing. <button className="font-medium text-cyan underline-offset-2 hover:underline" onClick={() => nav("/integrations")}>See what each needs</button>.</Note>
      <AccountForm open={form.open} account={form.account} role="other" onClose={() => setForm({ open: false })} />
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ Launch Network
export function LaunchNetwork() {
  const { data, isLoading, error, refetch } = useAccounts("launch");
  const [form, setForm] = useState<{ open: boolean; account?: any }>({ open: false });
  const remove = useRemove();
  const items: any[] = data?.items || [];
  const max = data?.max_launch ?? 10;
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Launch Network" subtitle="Up to 10 supporting accounts that each give real value of their own, and introduce new people to your main account."
        actions={<Button variant="primary" icon={Plus} disabled={items.length >= max} onClick={() => setForm({ open: true })}>Add account ({items.length}/{max})</Button>} />
      {error ? <ErrorState error={error} retry={refetch} /> : isLoading ? <Skeleton className="h-52" /> : items.length ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {items.map((a) => <AccountCard key={a.id} a={a} onEdit={() => setForm({ open: true, account: a })} onRemove={() => remove(a)} />)}
        </div>
      ) : (
        <Card><Empty icon={Rocket} title="No launch accounts yet" action={<Button variant="primary" icon={Plus} onClick={() => setForm({ open: true })}>Add the first one</Button>}>
          Give each one its own niche and purpose, and a tracking link in its bio so you can see who comes through to your main account.</Empty></Card>
      )}
      <Note tone="neutral">Every supporting account must post its own genuine content. Convo coordinates calendars, cross-promotion and captions, and never fakes likes,
        comments, follows or shares.</Note>
      {items.length > 0 && <LaunchMode />}
      <AccountForm open={form.open} account={form.account} role="launch" max={max} onClose={() => setForm({ open: false })} />
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ Account Groups
export function AccountGroups() {
  const toast = useToast();
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { data, isLoading, error, refetch } = useGroups();
  const accounts = useAccounts().data?.items || [];
  const [name, setName] = useState(""), [color, setColor] = useState("#8b5cf6"), [desc, setDesc] = useState("");
  const add = useMutation({ mutationFn: () => post("/api/hub/groups", { name, color, description: desc }),
    onSuccess: () => { toast("Group made."); setName(""); setDesc(""); qc.invalidateQueries(); }, onError: (e) => toast((e as Error).message, "bad") });
  const items: any[] = data?.items || [];
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Account Groups" subtitle="Group accounts by project or theme, then filter and compare them together." />
      <Card title="New group" icon={FolderKanban}>
        <form className="grid gap-3 md:grid-cols-[1fr_120px_2fr_auto] md:items-end" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
          <Field label="Name" htmlFor="g-name"><Input id="g-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Football project" /></Field>
          <Field label="Colour" htmlFor="g-color"><Input id="g-color" type="color" value={color} onChange={(e) => setColor(e.target.value)} className="p-1" /></Field>
          <Field label="Description" htmlFor="g-desc"><Input id="g-desc" value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="Optional" /></Field>
          <Button variant="primary" icon={Plus} loading={add.isPending} disabled={!name.trim()}>Add group</Button>
        </form>
      </Card>
      {error ? <ErrorState error={error} retry={refetch} /> : isLoading ? <Skeleton className="h-40" /> : items.length ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{items.map((g) => (
          <Card key={g.id}>
            <div className="flex items-center gap-3"><span className="size-3 rounded-full" style={{ background: g.color }} />
              <span className="grow text-[15px] font-semibold">{g.name}</span>
              <IconButton icon={Trash2} label="Delete group" onClick={async () => {
                if (!await confirm({ title: `Delete “${g.name}”?`, text: "The accounts stay; they just leave the group.", ok: "Delete", danger: true })) return;
                await del(`/api/hub/groups/${g.id}`); toast("Group deleted."); qc.invalidateQueries();
              }} /></div>
            {g.description && <p className="mt-2 text-[13px] text-muted">{g.description}</p>}
            <div className="mt-3 flex flex-wrap gap-2">{accounts.filter((a: any) => a.group_id === g.id).map((a: any) =>
              <Badge key={a.id} tone="neutral"><AtSign className="size-3" />{a.handle}</Badge>)}
              {!g.accounts && <span className="text-[12.5px] text-faint">No accounts yet. Pick this group when you add or edit an account.</span>}</div>
          </Card>))}</div>
      ) : <Card><Empty icon={Users} title="No groups yet">Groups help when the launch network grows: for example “Football”, “Baking”, “Uni life”.</Empty></Card>}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ Launch mode
function LaunchMode() {
  const qc = useQueryClient();
  const toast = useToast();
  const nav = useNavigate();
  const { data } = useQuery({ queryKey: ["launch"], queryFn: () => api("/api/hub/launch") });
  const [typing, setTyping] = useState<any>(null);
  const shout = async (a: any) => {
    try { const r = await post(`/api/hub/launch/${a.id}/shoutout`); qc.invalidateQueries({ queryKey: ["launch"] }); toast("Shout-out idea added to your main account."); nav(`/studio/${r.content_id}`); }
    catch (e) { toast((e as Error).message, "bad"); }
  };
  return (
    <Card title="Launch mode" icon={Rocket} subtitle="Five steps to get each account going, checked from real data">
      <div className="grid gap-3">{(data?.items || []).map((a: any) => (
        <div key={a.id} className="rounded-xl border border-line bg-bg-2 p-4">
          <div className="flex flex-wrap items-center gap-2"><span className="text-[14px] font-semibold">@{a.handle}</span><span className="text-[12.5px] text-muted">{a.platform_name}</span>
            <span className="ml-auto text-[12.5px] tabular text-muted">{a.done} of {a.of}</span></div>
          <div className="mt-2 h-1.5 rounded-full bg-bg"><div className="h-full rounded-full bg-gradient-to-r from-violet to-cyan" style={{ width: `${(a.done / a.of) * 100}%` }} /></div>
          <ul className="mt-3 grid gap-1.5 text-[13px] sm:grid-cols-2">{a.steps.map((s: any) => (
            <li key={s.id} className={cx("flex items-center gap-2", s.done ? "text-ink-2" : "text-muted")}>
              {s.done ? <CheckCircle2 className="size-4 shrink-0 text-good" /> : <CircleDashed className="size-4 shrink-0 text-faint" />}{s.title}</li>))}</ul>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button size="sm" onClick={() => setTyping(a)}>Type its numbers</Button>
            <Button size="sm" onClick={() => shout(a)}>Plan a shout-out</Button>
            <Button size="sm" variant="ghost" onClick={() => nav("/studio")}>Plan a post</Button>
            {a.followers != null && <span className="ml-auto self-center text-[12.5px] text-muted">{a.followers.toLocaleString()} followers{a.as_of ? ` · ${ago(a.as_of)}` : ""}</span>}
          </div>
        </div>))}</div>
      <NumbersForm a={typing} onClose={() => setTyping(null)} />
    </Card>
  );
}

function NumbersForm({ a, onClose }: { a: any; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [f, setF] = useState({ followers: "", following: "", posts: "" });
  const n = (v: string) => (v.trim() === "" ? null : Number(v));
  const save = useMutation({ mutationFn: () => post(`/api/hub/accounts/${a.id}/numbers`, { followers: n(f.followers), following: n(f.following), posts: n(f.posts) }),
    onSuccess: () => { toast("Saved. They're part of its history now."); qc.invalidateQueries(); setF({ followers: "", following: "", posts: "" }); onClose(); },
    onError: (e) => toast((e as Error).message, "bad") });
  return (
    <Modal open={!!a} onClose={onClose} title={a ? `@${a.handle}'s numbers today` : ""}
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending} disabled={!f.followers && !f.following && !f.posts} onClick={() => save.mutate()}>Save</Button></>}>
      <p className="mb-3 text-[13px] text-muted">Copy them from the app. Each time you do, the follower history grows (connected accounts do this by themselves).</p>
      <div className="grid grid-cols-3 gap-3">
        {(["followers", "following", "posts"] as const).map((k) => <Field key={k} label={k[0].toUpperCase() + k.slice(1)} htmlFor={`n-${k}`}>
          <Input id={`n-${k}`} type="number" min={0} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></Field>)}
      </div>
    </Modal>
  );
}
