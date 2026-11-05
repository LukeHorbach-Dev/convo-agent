import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUpCircle, Bell, ChevronDown, ChevronsLeft, ChevronsRight, Download, FlaskConical, LayoutTemplate, Menu, PenLine, Plus, Send, Sparkles, X,
} from "lucide-react";
import { Component, ErrorInfo, ReactNode, useCallback, useEffect, useId, useRef, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { api, onConnection, post } from "../lib/api";
import { ago, money } from "../lib/format";
import { MOBILE_TABS, NAV, titleFor } from "../lib/nav";
import { prefs, useAccount, useAppState } from "../lib/state";
import { Notes, useUpdates } from "../pages/Updates";
import { describeRun, RunBar, RunControl } from "./RunControl";
import { Avatar, Button, cx, Dot, IconButton, Modal, useConfirm, useToast } from "./ui";

// ------------------------------------------------------------------------------------------------ the logo
export function Logo({ small }: { small?: boolean }) {
  // The same drawing as the Mac app icon (mac/icon.svg): a speech bubble shaped like a C, with the next message's dot.
  const id = useId().replace(/:/g, "");
  const size = small ? 32 : 36;
  return (
    <svg width={size} height={size} viewBox="100 100 824 824" className="shrink-0 drop-shadow-[0_6px_14px_rgb(139_92_246/0.35)]" aria-hidden="true">
      <defs>
        <linearGradient id={`${id}b`} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#2a2150" /><stop offset="1" stopColor="#0c0d16" /></linearGradient>
        <radialGradient id={`${id}v`} cx="0.22" cy="0.12" r="0.8"><stop offset="0" stopColor="#8b5cf6" stopOpacity="0.8" /><stop offset="1" stopColor="#8b5cf6" stopOpacity="0" /></radialGradient>
        <linearGradient id={`${id}r`} x1="0.15" y1="0.1" x2="0.85" y2="0.95"><stop offset="0" stopColor="#c4b5fd" /><stop offset="0.45" stopColor="#8b5cf6" /><stop offset="1" stopColor="#22d3ee" /></linearGradient>
      </defs>
      <rect x="100" y="100" width="824" height="824" rx="200" fill={`url(#${id}b)`} />
      <rect x="100" y="100" width="824" height="824" rx="200" fill={`url(#${id}v)`} />
      <rect x="104" y="104" width="816" height="816" rx="196" fill="none" stroke="#fff" strokeOpacity="0.12" strokeWidth="8" />
      <g transform="translate(0 -6)">
        <path d="M 328 624 C 326 700 296 756 242 792 C 336 806 422 778 468 726 Z" fill={`url(#${id}r)`} />
        <path d="M 688 660 A 232 232 0 1 1 688 364" fill="none" stroke={`url(#${id}r)`} strokeWidth="100" strokeLinecap="round" />
        <circle cx="734" cy="512" r="58" fill="#e0fbff" />
      </g>
    </svg>
  );
}

// ------------------------------------------------------------------------------------------------ sidebar
function SideNav({ collapsed, onNavigate }: { collapsed: boolean; onNavigate?: () => void }) {
  const st = useAppState().data;
  const badge = (b?: string) => (b === "replies" ? st?.counts.replies_to_check : b === "hellos" ? st?.counts.hellos_to_check
    : b === "update" ? (st?.update?.available ? 1 : 0) : 0) || 0;
  return (
    <nav aria-label="Main" className="flex flex-col gap-5 px-3 pb-6">
      {NAV.map((sec) => (
        <div key={sec.title}>
          {!collapsed && <div className="px-3 pb-1.5 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-faint">{sec.title}</div>}
          {collapsed && <div className="mx-3 mb-2 h-px bg-line" />}
          <div className="flex flex-col gap-0.5">
            {sec.items.map((it) => {
              const n = badge(it.badge);
              return (
                <NavLink key={it.path} to={it.path} end={it.path === "/"} onClick={onNavigate} title={collapsed ? it.label : undefined}
                  className={({ isActive }) => cx("group relative flex h-9 items-center gap-3 rounded-xl px-3 text-[13.5px] font-medium transition-colors",
                    isActive ? "bg-raised text-ink shadow-[inset_0_0_0_1px_var(--color-line)]" : "text-muted hover:bg-hover/60 hover:text-ink",
                    collapsed && "justify-center px-0")}>
                  {({ isActive }) => (<>
                    {isActive && <span className="absolute left-0 top-2 bottom-2 w-[3px] rounded-r-full bg-gradient-to-b from-violet to-cyan" />}
                    <it.icon className={cx("size-[17px] shrink-0", isActive ? "text-violet-2" : "")} strokeWidth={1.9} />
                    {!collapsed && <span className="truncate">{it.label}</span>}
                    {n > 0 && (collapsed
                      ? <span className="absolute right-1.5 top-1.5 size-2 rounded-full bg-violet" />
                      : it.badge === "update" ? <span className="ml-auto rounded-full bg-cyan-soft px-1.5 text-[11px] font-semibold text-cyan">New</span>
                      : <span className="ml-auto rounded-full bg-violet-soft px-1.5 text-[11px] font-semibold text-violet-2 tabular">{n > 999 ? "999+" : n}</span>)}
                  </>)}
                </NavLink>
              );
            })}
          </div>
        </div>
      ))}
    </nav>
  );
}

function StatusPill({ collapsed }: { collapsed: boolean }) {
  const run = describeRun(useAppState().data);
  const tone = ({ running: "good", stopped: "bad", waiting: "warn", idle: "neutral" } as const)[run.kind];
  return (
    <div role="status" title={run.headline} className={cx("flex items-center gap-2.5 rounded-xl border border-line bg-surface px-3 py-2.5 text-[12.5px] text-ink-2", collapsed && "justify-center px-0")}>
      <Dot tone={tone} pulse={run.kind === "running"} />{!collapsed && <span className="truncate">{run.headline}</span>}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ top bar pieces
function AccountSwitcher() {
  const [acct, setAcct] = useAccount();
  const { data } = useQuery({ queryKey: ["accounts"], queryFn: () => api("/api/hub/accounts") });
  const [open, setOpen] = useState(false);
  const ref = useClickAway(() => setOpen(false));
  const items: any[] = data?.items || [];
  const current = items.find((a) => String(a.id) === acct);
  return (
    <div ref={ref} className="relative">
      <button onClick={() => setOpen(!open)} aria-haspopup="listbox" aria-expanded={open}
        className="flex h-9 max-w-[220px] items-center gap-2 rounded-xl border border-line bg-surface px-2.5 text-[13px] text-ink-2 hover:border-line-2">
        {current ? <Avatar name={current.handle} size={20} /> : <span className="grid size-5 place-items-center rounded-full bg-gradient-to-br from-violet to-cyan text-[9px] font-bold text-white">{items.length}</span>}
        <span className="truncate">{current ? `@${current.handle}` : "All accounts"}</span><ChevronDown className="size-3.5 shrink-0 text-faint" />
      </button>
      {open && (
        <div role="listbox" className="absolute right-0 z-40 mt-2 w-72 animate-rise rounded-2xl border border-line bg-raised p-1.5 shadow-[var(--shadow-pop)]">
          {[{ id: "all", handle: "All accounts", platform_name: `${items.length} accounts` }, ...items].map((a: any) => (
            <button key={a.id} role="option" aria-selected={String(a.id) === acct} onClick={() => { setAcct(String(a.id)); setOpen(false); }}
              className={cx("flex w-full items-center gap-3 rounded-xl px-2.5 py-2 text-left hover:bg-hover", String(a.id) === acct && "bg-hover")}>
              {a.id === "all" ? <span className="grid size-7 place-items-center rounded-full bg-gradient-to-br from-violet to-cyan text-[10px] font-bold text-white">All</span> : <Avatar name={a.handle} size={28} />}
              <span className="min-w-0"><span className="block truncate text-[13.5px] text-ink">{a.id === "all" ? a.handle : `@${a.handle}`}</span>
                <span className="block text-[11.5px] text-muted">{a.platform_name}{a.role && a.role !== "main" ? ` · ${a.role === "launch" ? "launch network" : "other"}` : a.role === "main" ? " · main" : ""}</span></span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function Notifications() {
  const nav = useNavigate();
  const { data } = useQuery({ queryKey: ["activity", "bell"], queryFn: () => api("/api/hub/activity?limit=8"), refetchInterval: 15000 });
  const items: any[] = data?.items || [];
  const [seen, setSeen] = useState(() => Number(prefs.read("convo.seen", "0")));
  const unread = items.filter((i) => i.id > seen && i.kind !== "info").length;
  const [open, setOpen] = useState(false);
  const ref = useClickAway(() => setOpen(false));
  const toggle = () => {
    setOpen(!open);
    if (!open && items[0]) { setSeen(items[0].id); prefs.write("convo.seen", String(items[0].id)); }
  };
  return (
    <div ref={ref} className="relative">
      <IconButton icon={Bell} label="Notifications" onClick={toggle} />
      {unread > 0 && <span className="pointer-events-none absolute right-1.5 top-1.5 size-2 rounded-full bg-cyan ring-2 ring-bg" />}
      {open && (
        <div className="absolute right-0 z-40 mt-2 w-[340px] max-w-[calc(100vw-24px)] animate-rise rounded-2xl border border-line bg-raised shadow-[var(--shadow-pop)]">
          <div className="flex items-center justify-between px-4 pt-3 pb-2"><span className="text-[13px] font-semibold">Notifications</span>
            <button className="text-[12.5px] text-violet-2 hover:underline" onClick={() => { setOpen(false); nav("/notifications"); }}>See all</button></div>
          <div className="max-h-[360px] overflow-y-auto px-2 pb-2">
            {items.length ? items.map((i) => <ActivityRow key={i.id} item={i} compact />) : <p className="px-2 py-6 text-center text-[13px] text-muted">Nothing yet.</p>}
          </div>
        </div>
      )}
    </div>
  );
}

export function ActivityRow({ item, compact }: { item: { id?: number; at: string; kind: string; text: string }; compact?: boolean }) {
  const tone = { good: "good", warn: "warn", problem: "bad", info: "neutral" }[item.kind] as any;
  return (
    <div className={cx("flex items-start gap-3 rounded-xl", compact ? "px-2 py-2 hover:bg-hover/60" : "py-3")}>
      <span className="mt-1.5"><Dot tone={tone} /></span>
      <span className="min-w-0 grow text-[13px] leading-snug text-ink-2">{item.text}</span>
      <time className="shrink-0 text-[11.5px] text-faint">{ago(item.at)}</time>
    </div>
  );
}

function QuickActions({ onAsk }: { onAsk: () => void }) {
  const [open, setOpen] = useState(false);
  const ref = useClickAway(() => setOpen(false));
  const toast = useToast();
  const qc = useQueryClient();
  const st = useAppState().data;
  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setOpen(false);
    try { await fn(); toast(ok); qc.invalidateQueries(); } catch (e) { toast((e as Error).message, "bad"); }
  };
  const actions = [
    { icon: Download, label: st?.practice ? "Make a new practice download" : "Load the newest download", go: () => run(() => post("/api/import"), "Loading your download…") },
    { icon: PenLine, label: "Write replies now", go: () => run(() => post("/api/think", { now: true }), "Writing replies…"), off: !st?.ai.on },
    { icon: Send, label: "Send just one", go: () => run(() => post("/api/send/start", { just_one: true }), "Sending one. Check it in Instagram."), off: !st?.counts.approved },
    { icon: Sparkles, label: "Ask Convo", go: () => { setOpen(false); onAsk(); } },
  ];
  return (
    <div ref={ref} className="relative">
      <Button variant="secondary" icon={Plus} onClick={() => setOpen(!open)} aria-haspopup="menu" className="max-sm:px-2.5"><span className="max-sm:hidden">Quick actions</span></Button>
      {open && (
        <div role="menu" className="absolute right-0 z-40 mt-2 w-64 animate-rise rounded-2xl border border-line bg-raised p-1.5 shadow-[var(--shadow-pop)]">
          {actions.map((a) => (
            <button key={a.label} role="menuitem" disabled={a.off} onClick={a.go}
              className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-[13.5px] text-ink-2 hover:bg-hover hover:text-ink disabled:opacity-40">
              <a.icon className="size-4 text-violet-2" />{a.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ Ask (⌘K)
export function AskPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [q, setQ] = useState("");
  const nav = useNavigate();
  const qc = useQueryClient();
  const m = useMutation({ mutationFn: (text: string) => post("/api/ask", { text }), onSuccess: () => qc.invalidateQueries() });
  useEffect(() => { if (open) { setQ(""); m.reset(); } }, [open]); // eslint-disable-line
  return (
    <Modal open={open} onClose={onClose} title="Ask or tell Convo">
      <form onSubmit={(e) => { e.preventDefault(); if (q.trim()) m.mutate(q.trim()); }} className="flex gap-2">
        <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="status, stop now, what did @maya say?"
          className="h-11 grow rounded-xl border border-line-2 bg-bg-2 px-3 text-[15px] outline-none focus:border-violet" aria-label="Ask Convo" />
        <Button variant="primary" size="lg" loading={m.isPending} icon={Sparkles}>Ask</Button>
      </form>
      <div className="mt-4 min-h-[60px] text-[14px] text-ink-2">
        {m.isError ? <p className="text-bad">{(m.error as Error).message}</p>
          : m.data ? <><p className="whitespace-pre-wrap">{m.data.answer}</p>
              <p className="mt-2 text-[12px] text-faint">{m.data.cost ? money(m.data.cost) : "Free"}{m.data.actions?.length ? ` · Did: ${m.data.actions.join(", ")}` : ""}</p></>
          : <p className="text-muted">Free: status, stop, pause, send, send one. Questions about people or your data cost about 1 to 2¢.</p>}
      </div>
      <button className="mt-2 text-[13px] text-violet-2 hover:underline" onClick={() => { onClose(); nav("/strategist"); }}>Open the AI Strategist for a longer chat</button>
    </Modal>
  );
}

// ------------------------------------------------------------------------------------------------ the frame
export function Shell({ children }: { children: ReactNode }) {
  const loc = useLocation();
  const nav = useNavigate();
  const [collapsed, setCollapsed] = useState(() => prefs.read("convo.collapsed", "") === "1");
  const [drawer, setDrawer] = useState(false);
  const [ask, setAsk] = useState(false);
  const st = useAppState().data;
  useEffect(() => setDrawer(false), [loc.pathname]);
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setAsk(true); } };
    document.addEventListener("keydown", h);
    return () => document.removeEventListener("keydown", h);
  }, []);
  const toggle = () => { setCollapsed(!collapsed); prefs.write("convo.collapsed", collapsed ? "" : "1"); };
  const title = titleFor(loc.pathname);
  useEffect(() => { document.title = loc.pathname === "/" ? "Convo" : `${title} · Convo`; }, [title, loc.pathname]);
  return (
    <div className="app-glow min-h-dvh">
      {/* desktop sidebar */}
      <aside className={cx("fixed inset-y-0 left-0 z-30 hidden flex-col border-r border-line bg-bg/80 backdrop-blur-xl transition-[width] duration-200 lg:flex",
        collapsed ? "w-[72px]" : "w-[266px]")}>
        <div className={cx("flex h-16 shrink-0 items-center gap-3 px-5", collapsed && "justify-center px-0")}>
          <Logo />{!collapsed && <div className="min-w-0"><div className="text-[15px] font-semibold tracking-[-0.02em]">Convo</div>
            <div className="truncate text-[11.5px] text-muted">Personal brand command centre</div></div>}
        </div>
        <div className="grow overflow-y-auto pt-2"><SideNav collapsed={collapsed} /></div>
        <div className="grid gap-2 border-t border-line p-3">
          <StatusPill collapsed={collapsed} />
          {!collapsed && <AppFooter />}
          <button onClick={toggle} className="flex h-8 items-center justify-center gap-2 rounded-lg text-[12px] text-faint hover:bg-hover hover:text-ink"
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
            {collapsed ? <ChevronsRight className="size-4" /> : <><ChevronsLeft className="size-4" />Collapse</>}
          </button>
        </div>
      </aside>

      {/* phone and tablet: the sidebar as a sheet */}
      {drawer && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div className="absolute inset-0 animate-fade bg-black/60" onClick={() => setDrawer(false)} />
          <aside className="absolute inset-y-0 left-0 flex w-[290px] max-w-[85vw] animate-rise flex-col border-r border-line bg-bg-2">
            <div className="flex h-16 items-center gap-3 px-5"><Logo /><span className="grow text-[15px] font-semibold">Convo</span>
              <IconButton icon={X} label="Close menu" onClick={() => setDrawer(false)} /></div>
            <div className="grow overflow-y-auto"><SideNav collapsed={false} onNavigate={() => setDrawer(false)} /></div>
            <div className="grid gap-2 border-t border-line p-3"><StatusPill collapsed={false} /><AppFooter /></div>
          </aside>
        </div>
      )}

      <div className={cx("transition-[padding] duration-200", collapsed ? "lg:pl-[72px]" : "lg:pl-[266px]")}>
        {st?.practice && (
          <div className="flex items-center gap-2 border-b border-warn/25 bg-warn-soft px-4 py-2 text-[13px] text-ink-2 lg:px-8">
            <FlaskConical className="size-4 text-warn" /><b className="text-warn">Practice.</b> A pretend Instagram. Nothing here touches your real account.
          </div>
        )}
        <header className="sticky top-0 z-20 flex h-16 items-center gap-2 border-b border-line/70 bg-bg/75 px-4 backdrop-blur-xl lg:gap-3 lg:px-8">
          <IconButton icon={Menu} label="Menu" className="lg:hidden" onClick={() => setDrawer(true)} />
          <div className="flex min-w-0 grow items-center gap-3"><span className="lg:hidden"><Logo small /></span>
            <span className="truncate text-[15px] font-semibold tracking-[-0.01em] max-lg:hidden">{title}</span></div>
          <button onClick={() => setAsk(true)} className="hidden h-9 w-[260px] items-center gap-2 rounded-xl border border-line bg-surface px-3 text-[13px] text-faint hover:border-line-2 xl:flex">
            <Sparkles className="size-4 text-violet-2" />Ask or tell Convo…<kbd className="ml-auto rounded-md border border-line px-1.5 text-[11px]">⌘K</kbd>
          </button>
          <div className="max-sm:hidden"><AccountSwitcher /></div>
          <Notifications />
          <QuickActions onAsk={() => setAsk(true)} />
          <RunControl />
        </header>
        <RunBar />
        <UpdateBanner />
        <main className="mx-auto w-full max-w-[1320px] px-4 pt-6 pb-28 lg:px-8 lg:pb-12" key={loc.pathname}>
          <div className="animate-rise"><PageBoundary>{children}</PageBoundary></div>
        </main>
      </div>

      {/* phone: bottom bar */}
      <nav aria-label="Quick" className="safe-bottom fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 border-t border-line bg-bg/90 backdrop-blur-xl lg:hidden">
        {MOBILE_TABS.map((p) => {
          const it = NAV.flatMap((s) => s.items).find((i) => i.path === p)!;
          return (
            <NavLink key={p} to={p} end={p === "/"} className={({ isActive }) => cx("flex h-16 flex-col items-center justify-center gap-1 text-[10.5px] font-medium",
              isActive ? "text-violet-2" : "text-muted")}>
              <it.icon className="size-5" strokeWidth={1.9} /><span className="max-w-[72px] truncate">{it.label.replace("Message", "").replace("AI ", "")}</span>
            </NavLink>
          );
        })}
        <button onClick={() => setDrawer(true)} className="flex h-16 flex-col items-center justify-center gap-1 text-[10.5px] font-medium text-muted">
          <Menu className="size-5" strokeWidth={1.9} />More
        </button>
      </nav>

      <AskPalette open={ask} onClose={() => setAsk(false)} />
      <WhatsNew />
      <ConnectionWatch onAuth={() => nav("/")} />
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ version, updates, what's new
function AppFooter() {
  const st = useAppState().data;
  return (
    <div className="flex items-center justify-between gap-2 px-1 text-[11.5px] text-faint">
      <NavLink to="/updates" className="truncate hover:text-ink" title="Updates">Convo {st?.version || ""}</NavLink>
      <a href="/classic" className="flex shrink-0 items-center gap-1 hover:text-ink" title="The classic Convo screens"><LayoutTemplate className="size-3.5" />Classic screens</a>
    </div>
  );
}

function UpdateBanner() {
  const st = useAppState().data;
  const loc = useLocation();
  const [hidden, setHidden] = useState(() => prefs.read("convo.update-hidden", ""));
  const v = st?.update?.version;
  if (!st?.update?.available || !v || hidden === v || loc.pathname === "/updates") return null;
  return (
    <div className="mx-auto mt-4 flex w-full max-w-[1320px] px-4 lg:px-8">
      <div className="flex w-full flex-wrap items-center gap-3 rounded-2xl border border-cyan/25 bg-cyan-soft px-4 py-3 text-[13.5px] text-ink-2">
        <ArrowUpCircle className="size-5 shrink-0 text-cyan" />
        <span className="grow"><b className="text-ink">Convo {v} is ready.</b> It installs in about a minute and keeps all your data.</span>
        <NavLink to="/updates"><Button size="sm" variant="cyan">See what's new</Button></NavLink>
        <IconButton icon={X} label="Hide until the next version" onClick={() => { prefs.write("convo.update-hidden", v); setHidden(v); }} />
      </div>
    </div>
  );
}

function WhatsNew() {
  const st = useAppState().data;
  const want = !!st?.update?.whats_new;
  const { data } = useUpdates();
  const [closed, setClosed] = useState(false);
  const qc = useQueryClient();
  if (!want || closed || !data?.notes_current) return null;
  const done = () => { setClosed(true); post("/api/updates/seen").then(() => qc.invalidateQueries({ queryKey: ["state"] })).catch(() => {}); };
  return (
    <Modal open onClose={done} title={`What's new in Convo ${data.current}`} wide
      footer={<Button variant="primary" onClick={done}>Got it</Button>}>
      <Notes text={data.notes_current} />
    </Modal>
  );
}

/** One page going wrong shows a message on that page instead of a blank window. */
class PageBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidCatch(error: Error, info: ErrorInfo) { console.warn("Page problem:", error, info.componentStack); }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="mx-auto mt-10 max-w-md rounded-2xl border border-line bg-surface p-6 text-center">
        <h1 className="text-[18px] font-semibold">This page hit a problem</h1>
        <p className="mt-2 text-[13.5px] text-muted">The rest of Convo is fine, and nothing was sent or changed. Try again, or send Claude a screenshot of this.</p>
        <p className="mt-3 rounded-lg bg-bg-2 px-3 py-2 text-left font-mono text-[12px] text-faint">{this.state.error.message}</p>
        <Button className="mt-4" variant="primary" onClick={() => this.setState({ error: null })}>Try again</Button>
      </div>
    );
  }
}

// ------------------------------------------------------------------------------------------------ when Convo isn't running
function ConnectionWatch({ onAuth }: { onAuth: () => void }) {
  const [down, setDown] = useState(false);
  useEffect(() => onConnection((k) => { if (k === "offline") setDown(true); else onAuth(); }), [onAuth]);
  useEffect(() => {
    if (!down) return;
    const t = setInterval(async () => { try { if ((await fetch("/api/health", { cache: "no-store" })).ok) location.reload(); } catch { /* still down */ } }, 2000);
    return () => clearInterval(t);
  }, [down]);
  if (!down) return null;
  return (
    <div id="offline" className="fixed inset-0 z-[70] grid place-items-center bg-bg/95 px-6 text-center backdrop-blur">
      <div className="max-w-sm"><div className="mx-auto mb-4 w-fit"><Logo /></div>
        <h2 className="text-[19px] font-semibold">Convo isn't running right now</h2>
        <p className="mt-2 text-[14px] text-muted">Open Convo from your Applications folder. This window comes back by itself as soon as it's running.</p>
        <p className="mt-4 flex items-center justify-center gap-2 text-[12.5px] text-faint"><Dot tone="violet" pulse />Waiting for Convo…</p></div>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ helpers
export function useClickAway<T extends HTMLElement = HTMLDivElement>(fn: () => void) {
  const ref = useRef<T>(null);
  const cb = useCallback(fn, [fn]);
  useEffect(() => {
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) cb(); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [cb]);
  return ref;
}

export function useConfirmAction() {
  return useConfirm();
}
