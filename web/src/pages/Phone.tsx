// Phone access: Convo on your phone through Tailscale (a free private network between your own devices).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, ExternalLink, Smartphone, Wifi } from "lucide-react";
import qrcode from "qrcode-generator";
import { ReactNode, useMemo } from "react";
import { Button, Card, Dot, ErrorState, Note, PageHeader, Skeleton, Toggle, useToast } from "../components/ui";
import { api, post } from "../lib/api";

type PhoneState = { on: boolean; listening: boolean; tailscale_ip: string | null; tailscale_name: string | null; error: string | null; url: string | null; mac: boolean };

function QR({ text }: { text: string }) {
  const cells = useMemo(() => {
    const q = qrcode(0, "M");
    q.addData(text);
    q.make();
    const n = q.getModuleCount();
    const out: [number, number][] = [];
    for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (q.isDark(r, c)) out.push([c, r]);
    return { n, out };
  }, [text]);
  return (
    <svg viewBox={`-3 -3 ${cells.n + 6} ${cells.n + 6}`} className="size-[220px] rounded-2xl bg-white p-1" role="img" aria-label="QR code for your phone">
      {cells.out.map(([x, y]) => <rect key={`${x}-${y}`} x={x} y={y} width="1.02" height="1.02" fill="#0a0b0f" />)}
    </svg>
  );
}

function Step({ n, children }: { n: number; children: ReactNode }) {
  return <li className="flex gap-3"><span className="grid size-6 shrink-0 place-items-center rounded-full bg-violet-soft text-[12px] font-semibold text-violet-2">{n}</span><span className="pt-0.5">{children}</span></li>;
}

export default function Phone() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data: p, isLoading, error, refetch } = useQuery({ queryKey: ["phone"], queryFn: () => api<PhoneState>("/api/phone"),
    refetchInterval: (q) => (q.state.data?.on && !q.state.data?.listening ? 2000 : 15000) });
  const set = useMutation({
    mutationFn: (on: boolean) => post("/api/settings", { phone_access: on }),
    onSuccess: (_, on) => { toast(on ? "Turning phone access on…" : "Phone access is off."); setTimeout(() => qc.invalidateQueries({ queryKey: ["phone"] }), 800); },
    onError: (e) => toast((e as Error).message, "bad"),
  });
  if (error) return <ErrorState error={error} retry={refetch} />;
  const copy = async () => { try { await navigator.clipboard.writeText(p!.url!); toast("Link copied. Send it only to yourself."); } catch { toast("Couldn't copy here: long-press the link instead.", "bad"); } };
  return (
    <div>
      <PageHeader title="Phone access" subtitle="Use Convo on your phone, at home or away, through Tailscale: a free private network between your own devices." />
      {isLoading || !p ? <Skeleton className="h-72" /> : (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
          <Card>
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="flex items-center gap-2 text-[16px] font-semibold"><Smartphone className="size-5 text-violet-2" />Phone access</div>
                <div className="mt-1.5 flex items-center gap-2 text-[13px] text-muted">
                  <Dot tone={p.listening ? "good" : p.on ? "warn" : "neutral"} pulse={p.on && !p.listening} />
                  {p.listening ? "On: your phone can open Convo" : p.on ? "Waiting for Tailscale on this Mac…" : "Off: Convo only opens on this Mac"}
                </div>
              </div>
              <Toggle label="Phone access" checked={p.on} disabled={set.isPending} onChange={(v) => set.mutate(v)} />
            </div>
            {p.on && p.error && !p.listening && <Note tone="warn" className="mt-4">{p.error} Follow steps 1 and 2, and Convo picks it up within a minute.</Note>}
            {p.listening && p.url && (
              <div className="mt-5 flex flex-col items-center gap-4 sm:flex-row sm:items-start">
                <QR text={p.url} />
                <div className="min-w-0 grow">
                  <div className="text-[14px] font-medium">Scan this with your phone's camera</div>
                  <p className="mt-1 text-[13px] text-muted">Tailscale must be on, on your phone. Then add it to your Home Screen (Share, Add to Home Screen) so Convo is one tap away.</p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Button size="sm" icon={Copy} onClick={copy}>Copy the link</Button>
                  </div>
                  <p className="mt-3 break-all rounded-lg bg-bg-2 px-3 py-2 font-mono text-[11.5px] text-faint">{p.url.split("#")[0]}</p>
                  <Note tone="warn" className="mt-3">The code and link include Convo's password. Only open them on your own phone, and don't share them.</Note>
                </div>
              </div>
            )}
            {!p.on && <p className="mt-4 text-[13px] text-muted">Nothing is opened to the internet or your Wi-Fi: only devices signed in to your own Tailscale can reach Convo, and they still need Convo's password.</p>}
          </Card>

          <Card title="Set up once (about 5 minutes)" icon={Wifi}>
            <ol className="grid gap-3 text-[13.5px] text-ink-2">
              <Step n={1}>On this Mac, install <b>Tailscale</b> from the Mac App Store and sign in (Google, Apple or Microsoft is fine). <a className="inline-flex items-center gap-1 text-violet-2 underline underline-offset-2" href="https://apps.apple.com/app/tailscale/id1475387142" target="_blank" rel="noreferrer">Open the App Store page<ExternalLink className="size-3" /></a></Step>
              <Step n={2}>On your phone, install <b>Tailscale</b> from the App Store, sign in with the <b>same account</b> and switch it on.</Step>
              <Step n={3}>Turn on <b>Phone access</b> here. If your Mac asks whether Python may accept incoming connections, choose <b>Allow</b>.</Step>
              <Step n={4}>Scan the code with your phone's camera. Convo opens, already signed in.</Step>
            </ol>
            <Note className="mt-4">Your Mac does the sending, so it needs to be on (it can be closed and charging). Everything else works the same on your phone: approve, Stop now, Ask and the rest.</Note>
          </Card>
        </div>
      )}
    </div>
  );
}
