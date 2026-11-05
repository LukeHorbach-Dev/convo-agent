// Convo's building blocks. Every screen is made from these, so they all look and behave the same.
import clsx from "clsx";
import { AlertTriangle, Check, Info, Loader2, X } from "lucide-react";
import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { hue, initial, messagePieces } from "../lib/format";

export const cx = clsx;

// ------------------------------------------------------------------------------------------------ buttons
type Variant = "primary" | "secondary" | "ghost" | "danger" | "danger-soft" | "cyan";
const variants: Record<Variant, string> = {
  primary: "bg-violet text-white hover:bg-violet-2 shadow-[0_6px_20px_-8px_rgb(139_92_246/0.7)]",
  secondary: "bg-raised text-ink border border-line-2 hover:bg-hover hover:border-faint/60",
  ghost: "text-ink-2 hover:bg-hover hover:text-ink",
  danger: "bg-[#dc3d3d] text-white hover:bg-[#e64a4a] shadow-[0_6px_20px_-8px_rgb(220_61_61/0.7)]",
  "danger-soft": "bg-bad-soft text-bad hover:bg-bad/20",
  cyan: "bg-cyan text-[#04161a] hover:brightness-110",
};
export function Button({ variant = "secondary", size = "md", icon: Icon, loading, className, children, ...rest }:
  { variant?: Variant; size?: "sm" | "md" | "lg"; icon?: any; loading?: boolean } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button {...rest} disabled={rest.disabled || loading}
      className={cx("inline-flex items-center justify-center gap-2 rounded-xl font-medium whitespace-nowrap transition-all",
        "disabled:opacity-45 disabled:cursor-not-allowed active:translate-y-px select-none",
        size === "sm" ? "h-8 px-3 text-[13px]" : size === "lg" ? "h-11 px-5 text-[15px]" : "h-9 px-4 text-sm",
        variants[variant], className)}>
      {loading ? <Loader2 className="size-4 animate-spin" /> : Icon ? <Icon className="size-4 shrink-0" strokeWidth={2} /> : null}
      {children}
    </button>
  );
}

export function IconButton({ icon: Icon, label, className, ...rest }: { icon: any; label: string } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button aria-label={label} title={label} {...rest}
      className={cx("grid size-9 place-items-center rounded-xl text-muted hover:bg-hover hover:text-ink transition-colors disabled:opacity-40", className)}>
      <Icon className="size-[18px]" strokeWidth={1.9} />
    </button>
  );
}

// ------------------------------------------------------------------------------------------------ surfaces
export function Card({ title, subtitle, action, children, className, bodyClass, icon: Icon }:
  { title?: ReactNode; subtitle?: ReactNode; action?: ReactNode; children?: ReactNode; className?: string; bodyClass?: string; icon?: any }) {
  return (
    <section className={cx("rounded-[var(--radius-card)] border border-line bg-surface shadow-[var(--shadow-card)] min-w-0", className)}>
      {(title || action) && (
        <header className="flex items-start justify-between gap-3 px-5 pt-4">
          <div className="min-w-0">
            <h2 className="flex items-center gap-2 text-[15px] font-semibold tracking-[-0.01em] text-ink">
              {Icon && <Icon className="size-4 text-violet-2" strokeWidth={2} />}{title}
            </h2>
            {subtitle && <p className="mt-0.5 text-[13px] text-muted">{subtitle}</p>}
          </div>
          {action && <div className="shrink-0">{action}</div>}
        </header>
      )}
      <div className={cx("px-5 pb-5", title || action ? "pt-3" : "pt-5", bodyClass)}>{children}</div>
    </section>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-[26px] font-semibold tracking-[-0.025em] text-ink">{title}</h1>
        {subtitle && <p className="mt-1 max-w-[70ch] text-[14px] text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

type Tone = "neutral" | "violet" | "cyan" | "good" | "warn" | "bad";
const tones: Record<Tone, string> = {
  neutral: "bg-hover text-ink-2", violet: "bg-violet-soft text-violet-2", cyan: "bg-cyan-soft text-cyan",
  good: "bg-good-soft text-good", warn: "bg-warn-soft text-warn", bad: "bg-bad-soft text-bad",
};
export function Badge({ tone = "neutral", children, icon: Icon, className }: { tone?: Tone; children: ReactNode; icon?: any; className?: string }) {
  return (
    <span className={cx("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11.5px] font-semibold whitespace-nowrap", tones[tone], className)}>
      {Icon && <Icon className="size-3" strokeWidth={2.4} />}{children}
    </span>
  );
}

export function Dot({ tone = "neutral", pulse }: { tone?: Tone; pulse?: boolean }) {
  const c = { neutral: "bg-faint", violet: "bg-violet", cyan: "bg-cyan", good: "bg-good", warn: "bg-warn", bad: "bg-bad" }[tone];
  return <span className={cx("relative inline-block size-2 shrink-0 rounded-full", c)}>
    {pulse && <span className={cx("absolute inset-0 animate-ping rounded-full opacity-60", c)} />}</span>;
}

export function Avatar({ name, size = 36, className }: { name?: string | null; size?: number; className?: string }) {
  const h = hue(name || "?");
  return (
    <span aria-hidden className={cx("grid shrink-0 place-items-center rounded-full font-semibold", className)}
      style={{ width: size, height: size, fontSize: size * 0.4, background: `hsl(${h} 45% 22%)`, color: `hsl(${h} 80% 82%)` }}>
      {initial(name)}
    </span>
  );
}

// ------------------------------------------------------------------------------------------------ states
export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("skeleton rounded-xl", className)} />;
}

export function Empty({ icon: Icon = Info, title, children, action }: { icon?: any; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center px-4 py-10 text-center">
      <div className="mb-3 grid size-12 place-items-center rounded-2xl bg-violet-soft text-violet-2"><Icon className="size-5" /></div>
      <p className="text-[15px] font-semibold text-ink">{title}</p>
      {children && <div className="mt-1 max-w-[46ch] text-[13.5px] text-muted">{children}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  return (
    <div className="flex items-start gap-3 rounded-xl border border-bad/25 bg-bad-soft px-4 py-3 text-[13.5px]">
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-bad" />
      <div className="grow text-ink-2">{(error as Error)?.message || "Something went wrong."}</div>
      {retry && <Button size="sm" variant="ghost" onClick={retry}>Try again</Button>}
    </div>
  );
}

export function Note({ tone = "violet", icon: Icon = Info, children, className }: { tone?: Tone; icon?: any; children: ReactNode; className?: string }) {
  const t = { neutral: "border-line bg-raised", violet: "border-violet/20 bg-violet-soft", cyan: "border-cyan/20 bg-cyan-soft",
    good: "border-good/20 bg-good-soft", warn: "border-warn/25 bg-warn-soft", bad: "border-bad/25 bg-bad-soft" }[tone];
  const ic = { neutral: "text-muted", violet: "text-violet-2", cyan: "text-cyan", good: "text-good", warn: "text-warn", bad: "text-bad" }[tone];
  return (
    <div className={cx("flex items-start gap-3 rounded-xl border px-4 py-3 text-[13.5px] text-ink-2", t, className)}>
      <Icon className={cx("mt-0.5 size-4 shrink-0", ic)} /><div className="min-w-0 grow">{children}</div>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ inputs
export function Field({ label, hint, error, children, htmlFor }: { label: string; hint?: ReactNode; error?: string; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="min-w-0">
      <label htmlFor={htmlFor} className="mb-1.5 block text-[13px] font-medium text-ink-2">{label}</label>
      {children}
      {error ? <p className="mt-1.5 text-[12.5px] text-bad">{error}</p> : hint ? <p className="mt-1.5 text-[12.5px] text-muted">{hint}</p> : null}
    </div>
  );
}

const inputBase = "w-full rounded-xl border border-line-2 bg-bg-2 px-3 text-[14px] text-ink placeholder:text-faint outline-none transition focus:border-violet focus:ring-4 focus:ring-violet/15";
export const Input = (p: React.InputHTMLAttributes<HTMLInputElement>) => <input {...p} className={cx(inputBase, "h-10", p.className)} />;
export const Textarea = (p: React.TextareaHTMLAttributes<HTMLTextAreaElement>) => <textarea {...p} className={cx(inputBase, "py-2.5 leading-relaxed", p.className)} />;
export const Select = (p: React.SelectHTMLAttributes<HTMLSelectElement>) => <select {...p} className={cx(inputBase, "h-10 appearance-none pr-9 bg-[length:12px] bg-[right_12px_center] bg-no-repeat", p.className)}
  style={{ backgroundImage: "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%238e95a3' stroke-width='2.5'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E\")", ...p.style }} />;

export function Toggle({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled} onClick={() => onChange(!checked)}
      className={cx("relative h-6 w-11 shrink-0 rounded-full transition-colors disabled:opacity-40", checked ? "bg-violet" : "bg-line-2")}>
      <span className={cx("absolute top-0.5 left-0.5 size-5 rounded-full bg-white shadow transition-transform", checked && "translate-x-5")} />
    </button>
  );
}

export function Segmented<T extends string>({ value, onChange, options, className }: { value: T; onChange: (v: T) => void; options: [T, string][]; className?: string }) {
  return (
    <div role="tablist" className={cx("inline-flex max-w-full gap-1 overflow-x-auto rounded-xl border border-line bg-bg-2 p-1", className)}>
      {options.map(([v, l]) => (
        <button key={v} role="tab" aria-selected={value === v} onClick={() => onChange(v)}
          className={cx("h-7 shrink-0 rounded-lg px-3 text-[13px] font-medium transition-colors",
            value === v ? "bg-raised text-ink shadow-[0_1px_2px_rgb(0_0_0/0.4)]" : "text-muted hover:text-ink")}>{l}</button>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ overlays
export function Modal({ open, onClose, title, children, footer, wide }: { open: boolean; onClose: () => void; title: string; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  useEscape(open, onClose);
  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-50 grid place-items-end sm:place-items-center">
      <div className="absolute inset-0 animate-fade bg-black/60 backdrop-blur-[2px]" onClick={onClose} />
      <div role="dialog" aria-modal="true" aria-label={title}
        className={cx("relative w-full animate-rise rounded-t-3xl border border-line bg-raised shadow-[var(--shadow-pop)] sm:rounded-3xl safe-bottom", wide ? "sm:max-w-2xl" : "sm:max-w-md")}>
        <div className="flex items-center justify-between px-6 pt-5"><h2 className="text-[17px] font-semibold">{title}</h2>
          <IconButton icon={X} label="Close" onClick={onClose} className="-mr-2" /></div>
        <div className="max-h-[70vh] overflow-y-auto px-6 pt-3 pb-5">{children}</div>
        {footer && <div className="flex flex-wrap justify-end gap-2 border-t border-line px-6 py-4">{footer}</div>}
      </div>
    </div>, document.body);
}

export function Drawer({ open, onClose, title, children, header }: { open: boolean; onClose: () => void; title: string; children: ReactNode; header?: ReactNode }) {
  useEscape(open, onClose);
  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-50">
      <div className="absolute inset-0 animate-fade bg-black/55 backdrop-blur-[2px]" onClick={onClose} />
      <aside role="dialog" aria-modal="true" aria-label={title}
        className="absolute inset-y-0 right-0 flex w-full max-w-[560px] animate-rise flex-col border-l border-line bg-bg-2 shadow-[var(--shadow-pop)]">
        <div className="flex items-start gap-3 border-b border-line bg-surface px-6 py-5">
          <div className="min-w-0 grow">{header ?? <h2 className="text-[17px] font-semibold">{title}</h2>}</div>
          <IconButton icon={X} label="Close" onClick={onClose} className="-mr-2 -mt-1" />
        </div>
        <div className="grow overflow-y-auto px-6 py-5">{children}</div>
      </aside>
    </div>, document.body);
}

function useEscape(open: boolean, onClose: () => void) {
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", h);
    return () => document.removeEventListener("keydown", h);
  }, [open, onClose]);
}

// ------------------------------------------------------------------------------------------------ toasts and confirms
type ToastAction = { label: string; run: () => void };
type ToastItem = { id: number; text: string; tone: "good" | "bad" | "info"; action?: ToastAction };
const ToastCtx = createContext<(text: string, tone?: ToastItem["tone"], action?: ToastAction) => void>(() => {});
const ConfirmCtx = createContext<(o: ConfirmOpts) => Promise<boolean>>(async () => false);
type ConfirmOpts = { title: string; text: ReactNode; ok?: string; danger?: boolean; typed?: string };

export function Providers({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const next = useRef(1);
  const toast = useCallback((text: string, tone: ToastItem["tone"] = "good", action?: ToastAction) => {
    const id = next.current++;
    setItems((x) => [...x.slice(-3), { id, text, tone, action }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), tone === "bad" ? 7000 : action ? 8000 : 4000);
  }, []);
  const [confirm, setConfirm] = useState<(ConfirmOpts & { resolve: (v: boolean) => void }) | null>(null);
  const [typed, setTyped] = useState("");
  const ask = useCallback((o: ConfirmOpts) => new Promise<boolean>((resolve) => { setTyped(""); setConfirm({ ...o, resolve }); }), []);
  const close = (v: boolean) => { confirm?.resolve(v); setConfirm(null); };
  return (
    <ToastCtx.Provider value={toast}>
      <ConfirmCtx.Provider value={ask}>
        {children}
        {createPortal(
          <div aria-live="polite" className="pointer-events-none fixed inset-x-0 bottom-20 z-[60] flex flex-col items-center gap-2 px-4 lg:bottom-6">
            {items.map((t) => (
              <div key={t.id} role={t.tone === "bad" ? "alert" : "status"}
                className={cx("pointer-events-auto flex max-w-md animate-rise items-center gap-2.5 rounded-2xl border px-4 py-3 text-[13.5px] shadow-[var(--shadow-pop)]",
                  t.tone === "bad" ? "border-bad/30 bg-[#2a1416] text-ink" : "border-line-2 bg-raised text-ink")}>
                {t.tone === "bad" ? <AlertTriangle className="size-4 text-bad" /> : t.tone === "good" ? <Check className="size-4 text-good" /> : <Info className="size-4 text-cyan" />}
                <span className="grow">{t.text}</span>
                {t.action && <button className="shrink-0 rounded-lg px-2 py-1 text-[13px] font-semibold text-violet-2 hover:bg-hover"
                  onClick={() => { t.action!.run(); setItems((x) => x.filter((y) => y.id !== t.id)); }}>{t.action.label}</button>}
              </div>
            ))}
          </div>, document.body)}
        <Modal open={!!confirm} onClose={() => close(false)} title={confirm?.title || ""}
          footer={<>
            <Button variant="ghost" onClick={() => close(false)}>Cancel</Button>
            <Button variant={confirm?.danger ? "danger" : "primary"} disabled={!!confirm?.typed && typed !== confirm.typed}
              onClick={() => close(true)}>{confirm?.ok || "OK"}</Button>
          </>}>
          <div className="text-[14px] text-ink-2">{confirm?.text}</div>
          {confirm?.typed && <Input className="mt-4" autoFocus placeholder={`Type ${confirm.typed}`} value={typed} onChange={(e) => setTyped(e.target.value)} />}
        </Modal>
      </ConfirmCtx.Provider>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);
export const useConfirm = () => useContext(ConfirmCtx);

// ------------------------------------------------------------------------------------------------ messages
export function MessageText({ text }: { text: string }) {
  return <>{messagePieces(text).map((p, i) => "text" in p ? <span key={i}>{p.text}</span> :
    <span key={i} className="font-medium">{p.icon} {p.label}{p.rest && <span className="font-normal text-muted"> {p.rest}</span>}</span>)}</>;
}

export function Bubble({ mine, text, time }: { mine: boolean; text: string; time?: string }) {
  return (
    <div className={cx("max-w-[82%] rounded-2xl px-3.5 py-2 text-[14px] leading-snug break-words",
      mine ? "self-end rounded-br-md bg-violet/25 text-ink" : "self-start rounded-bl-md bg-raised text-ink border border-line")}>
      <MessageText text={text} />
      {time && <div className="mt-0.5 text-[10.5px] text-faint">{time}</div>}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ "arrives in stage N"
export function Planned({ stage, title, what, needs, action }: { stage: number; title: string; what: string[]; needs?: string[]; action?: ReactNode }) {
  return (
    <Card>
      <div className="flex flex-col gap-6 md:flex-row md:items-start">
        <div className="grid size-12 shrink-0 place-items-center rounded-2xl bg-gradient-to-br from-violet/30 to-cyan/20 text-violet-2 text-[15px] font-bold">
          {stage}
        </div>
        <div className="min-w-0 grow">
          <Badge tone="violet">Coming in stage {stage}</Badge>
          <h2 className="mt-2 text-[17px] font-semibold">{title}</h2>
          <p className="mt-1 text-[13.5px] text-muted">This page will show real data only. Here's what it will do:</p>
          <ul className="mt-3 grid gap-1.5 text-[14px] text-ink-2 sm:grid-cols-2">
            {what.map((w) => <li key={w} className="flex gap-2"><Check className="mt-0.5 size-4 shrink-0 text-cyan" />{w}</li>)}
          </ul>
          {needs && needs.length > 0 && (
            <div className="mt-4 rounded-xl border border-line bg-bg-2 px-4 py-3 text-[13px] text-muted">
              <span className="font-medium text-ink-2">What it needs: </span>{needs.join(" · ")}
            </div>
          )}
          {action && <div className="mt-4">{action}</div>}
        </div>
      </div>
    </Card>
  );
}
