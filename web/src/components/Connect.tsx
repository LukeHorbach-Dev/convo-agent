// Connecting a platform with your own developer app: the steps, the two details to paste, and Connect.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Copy, ExternalLink, KeyRound, Link2, RefreshCw, Unplug } from "lucide-react";
import { ReactNode, useState } from "react";
import { api, post } from "../lib/api";
import { ago } from "../lib/format";
import { Badge, Button, Drawer, Field, Input, Note, useConfirm, useToast } from "./ui";

export type ConnStatus = { id: string; name: string; has_app: boolean; client_id: string | null; connected: boolean; account: any; connected_at: string | null;
  last_sync: string | null; error: string | null; redirect_uri: string; scopes: string[] };

type Guide = { minutes: number; intro: string; steps: ReactNode[]; idLabel: string; secretLabel: string; after?: ReactNode };

const L = ({ href, children }: { href: string; children: ReactNode }) =>
  <a className="inline-flex items-center gap-1 font-medium text-violet-2 hover:underline" href={href} target="_blank" rel="noreferrer">{children}<ExternalLink className="size-3" /></a>;

export const GUIDES: Record<string, (s: ConnStatus) => Guide> = {
  youtube: () => ({
    minutes: 10, idLabel: "Client ID", secretLabel: "Client secret",
    intro: "Google lets Convo read your channel's numbers and upload videos through your own free Google Cloud project. Use the Google account that owns your YouTube channel.",
    steps: [
      <>Open <L href="https://console.cloud.google.com/projectcreate">Google Cloud: new project</L>. Name it <b>Convo</b> and press <b>Create</b>.</>,
      <>Turn on two APIs: open <L href="https://console.cloud.google.com/apis/library/youtube.googleapis.com">YouTube Data API v3</L> and press <b>Enable</b>, then <L href="https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com">YouTube Analytics API</L> and press <b>Enable</b>.</>,
      <>Open <L href="https://console.cloud.google.com/auth/overview">Google Auth Platform</L> and press <b>Get started</b>. App name <b>Convo</b>, your email, Audience <b>External</b>, your email again, agree, <b>Create</b>.</>,
      <>Still there, open <b>Audience</b>, and under <b>Test users</b> add your own Gmail address.</>,
      <>Open <b>Clients</b>, press <b>Create client</b>, choose <b>Desktop app</b>, name it <b>Convo</b>, press <b>Create</b>. Copy the <b>Client ID</b> and <b>Client secret</b> into the boxes below.</>,
      <>Press <b>Connect YouTube</b>. Google says it hasn't verified the app: that's expected for your own app. Press <b>Continue</b> and allow the access.</>,
    ],
    after: <Note className="mt-3">While your Google app is in "Testing", Google signs Convo out every 7 days. To stop that, open <b>Audience</b> and press <b>Publish app</b> (it stays yours; nobody else can use it without your Client ID).</Note>,
  }),
};

export function ConnectDrawer({ pid, open, onClose }: { pid: string | null; open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const confirm = useConfirm();
  const { data } = useQuery({ queryKey: ["connect"], queryFn: () => api<{ items: ConnStatus[] }>("/api/connect"), refetchInterval: open ? 3000 : false });
  const s = data?.items.find((x) => x.id === pid);
  const [id, setId] = useState("");
  const [secret, setSecret] = useState("");
  const refresh = () => { qc.invalidateQueries({ queryKey: ["connect"] }); qc.invalidateQueries({ queryKey: ["integrations"] }); qc.invalidateQueries({ queryKey: ["yt"] }); };
  const save = useMutation({ mutationFn: () => post(`/api/connect/${pid}/app`, { client_id: id, client_secret: secret }),
    onSuccess: () => { toast("Saved on this Mac."); setId(""); setSecret(""); refresh(); }, onError: (e) => toast((e as Error).message, "bad") });
  const connect = useMutation({ mutationFn: () => post<{ url: string }>(`/api/connect/${pid}/start`),
    onSuccess: (r) => { window.open(r.url, "_blank", "noopener"); toast("Sign in in your browser, then come back here."); }, onError: (e) => toast((e as Error).message, "bad") });
  const sync = useMutation({ mutationFn: () => post(`/api/connect/${pid}/sync`), onSuccess: () => { toast("Updated."); refresh(); }, onError: (e) => toast((e as Error).message, "bad") });
  if (!pid || !s) return <Drawer open={open} onClose={onClose} title="Connect"><div /></Drawer>;
  const g = GUIDES[pid]?.(s);
  return (
    <Drawer open={open} onClose={onClose} title={`Connect ${s.name}`}>
      <div className="flex flex-col gap-5">
        {s.connected ? (
          <div className="rounded-2xl border border-good/25 bg-good-soft p-4">
            <div className="flex items-center gap-2 text-[15px] font-semibold text-good"><CheckCircle2 className="size-5" />Connected{s.account?.name ? ` as ${s.account.name}` : ""}</div>
            <p className="mt-1 text-[13px] text-ink-2">{s.last_sync ? `Updated ${ago(s.last_sync)}. Convo updates it every 6 hours.` : "Fetching your numbers…"}</p>
            {s.error && <Note tone="warn" className="mt-3">{s.error}</Note>}
            <div className="mt-3 flex flex-wrap gap-2">
              <Button size="sm" icon={RefreshCw} loading={sync.isPending} onClick={() => sync.mutate()}>Update now</Button>
              {s.error && <Button size="sm" variant="primary" icon={Link2} loading={connect.isPending} onClick={() => connect.mutate()}>Connect again</Button>}
              <Button size="sm" variant="ghost" icon={Unplug} onClick={async () => {
                if (!await confirm({ title: `Disconnect ${s.name}?`, text: "Convo deletes its keys from this Mac and stops updating. Your app details stay, so connecting again is one click.", ok: "Disconnect", danger: true })) return;
                await post(`/api/connect/${pid}/disconnect`); refresh(); toast("Disconnected.");
              }}>Disconnect</Button>
            </div>
          </div>
        ) : g && (<>
          <p className="text-[14px] text-ink-2">{g.intro} <span className="text-muted">About {g.minutes} minutes, once.</span></p>
          <ol className="grid gap-3 text-[13.5px] text-ink-2">{g.steps.map((x, i) => (
            <li key={i} className="flex gap-3"><span className="grid size-6 shrink-0 place-items-center rounded-full bg-violet-soft text-[12px] font-semibold text-violet-2">{i + 1}</span><span className="pt-0.5">{x}</span></li>))}</ol>
          {g.after}
        </>)}
        {!s.connected && (
          <div className="rounded-2xl border border-line bg-bg-2 p-4">
            <div className="mb-3 flex items-center gap-2 text-[14px] font-semibold"><KeyRound className="size-4 text-violet-2" />Your app details {s.has_app && <Badge tone="good">Saved</Badge>}</div>
            {s.has_app && <p className="mb-3 truncate text-[12.5px] text-muted">{g?.idLabel}: {s.client_id}</p>}
            <form className="grid gap-3" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
              <Field label={g?.idLabel || "Client ID"} htmlFor="cx-id"><Input id="cx-id" value={id} onChange={(e) => setId(e.target.value)} autoComplete="off" /></Field>
              <Field label={g?.secretLabel || "Client secret"} htmlFor="cx-secret"><Input id="cx-secret" type="password" value={secret} onChange={(e) => setSecret(e.target.value)} autoComplete="off" /></Field>
              <Button variant={s.has_app ? "secondary" : "primary"} loading={save.isPending} disabled={!id.trim() || !secret.trim()}>{s.has_app ? "Replace" : "Save"}</Button>
            </form>
            <p className="mt-3 text-[12px] text-faint">Kept on this Mac only, readable by you alone. Never paste these into a chat.</p>
          </div>
        )}
        {!s.connected && <Button variant="primary" size="lg" icon={Link2} disabled={!s.has_app} loading={connect.isPending} onClick={() => connect.mutate()}>Connect {s.name}</Button>}
        <details className="text-[12.5px] text-muted"><summary className="cursor-pointer">Technical details</summary>
          <p className="mt-2">Return address: <code className="break-all">{s.redirect_uri}</code>
            <button className="ml-1 text-violet-2" onClick={() => navigator.clipboard.writeText(s.redirect_uri).then(() => toast("Copied."))}><Copy className="inline size-3" /></button></p>
          <p className="mt-1">Access asked for: {s.scopes.map((x) => x.split("/").pop()).join(", ")}</p></details>
      </div>
    </Drawer>
  );
}
