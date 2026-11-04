export const num = (n: number | null | undefined) => (n == null ? "—" : n.toLocaleString());

export const compact = (n: number | null | undefined) =>
  n == null ? "—" : n >= 10_000 ? Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(n) : n.toLocaleString();

export const pct = (x: number | null | undefined, digits = 0) => (x == null ? "—" : `${(x * 100).toFixed(digits)}%`);

export const money = (n: number | null | undefined) => (!n ? "$0.00" : n < 0.01 ? "under 1¢" : `$${n.toFixed(2)}`);

export const plural = (n: number, one: string, many = one + "s") => `${n.toLocaleString()} ${n === 1 ? one : many}`;

export function ago(when: string | number | null | undefined): string {
  if (when == null || when === "") return "";
  const t = typeof when === "number" ? when : Date.parse(when);
  const s = (Date.now() - t) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 86400 * 7) return `${Math.floor(s / 86400)} d ago`;
  return new Date(t).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

export const dayLabel = (iso: string) => new Date(iso + "T12:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" });

export const longDate = () =>
  new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });

export const greeting = () => {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
};

/* A stable hue per name, for avatars. */
export const hue = (s: string) => [...(s || "?")].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);
export const initial = (s?: string | null) => ((s || "?").replace(/[^\p{L}\p{N}]/gu, "")[0] || "?").toUpperCase();

/* Message text with Convo's tags ("[reel by @x: caption]") turned into labels. */
const MEDIA: Record<string, [string, string]> = {
  "voice message": ["🎤", "Voice message"], call: ["📞", "Call"], photo: ["📷", "Photo"], video: ["🎞️", "Video"],
  gif: ["✨", "GIF"], sticker: ["🙂", "Sticker"], file: ["📎", "File"], attachment: ["📎", "Attachment"],
  reel: ["🎬", "Reel"], post: ["🖼️", "Post"], "shared a post": ["🖼️", "Post"],
};
export type Piece = { text: string } | { media: string; icon: string; label: string; rest: string };
export function messagePieces(t: string): Piece[] {
  const out: Piece[] = [];
  const re = /\[(voice message|call|photo|video|gif|sticker|file|attachment|reel|post|shared a post)([^\]]*)\]/g;
  let last = 0;
  for (const m of t.matchAll(re)) {
    if (m.index! > last) out.push({ text: t.slice(last, m.index) });
    const [icon, label] = MEDIA[m[1]];
    out.push({ media: m[1], icon, label, rest: m[2].trim() });
    last = m.index! + m[0].length;
  }
  if (last < t.length) out.push({ text: t.slice(last) });
  return out;
}
